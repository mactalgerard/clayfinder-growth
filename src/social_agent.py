"""
social_agent.py — Main orchestrator for the clayfinder-growth social listening agent.

Usage:
  python src/social_agent.py                         # Full run
  python src/social_agent.py --subreddit Pottery     # One subreddit
  python src/social_agent.py --dry-run               # Fetch + filter only, skip Claude
  python src/social_agent.py --limit 20              # Cap threads per subreddit per term
  python src/social_agent.py --days 7                # Only threads from last N days
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

# Ensure src/ is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reddit_client import (
    RedditThread,
    build_session,
    is_show_and_tell,
    search_subreddit,
)

console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_SUBREDDITS = [
    "Pottery",
    "Ceramics",
    "PotteryClasses",
    "ArtClasses",
    "Hobbyists",
    "AskWomen",
    "AskMen",
    "Frugal",
    "moving",
    "AskReddit",
]

SEARCH_TERMS = [
    "pottery classes",
    "ceramics classes",
    "pottery studio",
    "ceramics studio",
    "wheel throwing classes",
    "find pottery near me",
    "pottery classes recommendation",
    "looking for pottery",
    "started pottery",
    "pottery beginner",
]

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 512
OUTPUTS_DIR = Path("outputs")
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Broad subreddits where we require pottery keywords in title/body
# to avoid false positives from unrelated threads
BROAD_SUBREDDITS = {"AskMen", "AskWomen", "Frugal", "moving", "AskReddit", "Hobbyists"}
POTTERY_KEYWORDS = ["potter", "ceramic", "kiln", "wheel throw", "clay", "glaze", "studio"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="clayfinder-growth social listening agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--subreddit",
        type=str,
        default=None,
        metavar="NAME",
        help="Target a single subreddit (overrides TARGET_SUBREDDITS list)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and filter threads only — skip Claude drafting",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        metavar="N",
        help="Max threads to fetch per subreddit per search term (default: 25)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        metavar="N",
        help="Only include threads from last N days (default: 30)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Thread scoring and classification
# ---------------------------------------------------------------------------

def score_thread(thread: RedditThread) -> int:
    """
    Priority score for sorting before Claude drafting. Higher = more worth engaging with.

    +2  score >= 50
    +1  score >= 10
    +1  num_comments >= 5
    -1  age_days > 14
    """
    s = 0

    if thread.score >= 50:
        s += 2
    elif thread.score >= 10:
        s += 1

    if thread.num_comments >= 5:
        s += 1

    if thread.age_days > 14:
        s -= 1

    return s


# ---------------------------------------------------------------------------
# Claude drafting
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    """Load the engagement system prompt from src/prompts/engagement_system.md."""
    prompt_path = PROMPTS_DIR / "engagement_system.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"System prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_user_message(thread: RedditThread) -> str:
    """Build the user message sent to Claude for a given thread."""
    lines = [
        f"Subreddit: r/{thread.subreddit}",
        f"Title: {thread.title}",
        f"Body: {thread.selftext.strip() or '(no body)'}",
        "",
        "Top comments:",
    ]
    if thread.top_comments:
        for i, comment in enumerate(thread.top_comments, 1):
            # Truncate very long comments
            truncated = comment[:400] + "..." if len(comment) > 400 else comment
            lines.append(f"{i}. {truncated}")
    else:
        lines.append("(no comments yet)")

    return "\n".join(lines)


def draft_response(
    client: anthropic.Anthropic,
    thread: RedditThread,
    system_prompt: str,
) -> tuple[str, bool, str]:
    """
    Call Claude to assess and draft an engagement response for the given thread.

    Claude returns a structured response with:
      CONFIDENCE: HIGH|MEDIUM|LOW
      INCLUDE_LINK: YES|NO

      [comment text]

    Returns (confidence, include_link, response_text).
    On failure returns ("LOW", False, "[drafting failed — ...]").
    Retries once on rate limit (waits 60s).
    """
    user_message = build_user_message(thread)

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            return _parse_claude_response(raw)

        except anthropic.RateLimitError:
            if attempt == 0:
                console.print("  [yellow]Rate limited — waiting 60s before retry...[/yellow]")
                time.sleep(60)
            else:
                return ("LOW", False, "[drafting failed — rate limit exceeded]")

        except anthropic.APIError as e:
            return ("LOW", False, f"[drafting failed — API error: {e}]")

    return ("LOW", False, "[drafting failed — unknown error]")


def _parse_claude_response(raw: str) -> tuple[str, bool, str]:
    """
    Parse Claude's structured response into (confidence, include_link, response_text).

    Expected format:
      CONFIDENCE: HIGH
      INCLUDE_LINK: YES

      [comment text here]

    Falls back gracefully if headers are missing.
    """
    lines = raw.splitlines()
    confidence = "LOW"
    include_link = False
    text_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("CONFIDENCE:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value in ("HIGH", "MEDIUM", "LOW"):
                confidence = value
        elif stripped.startswith("INCLUDE_LINK:"):
            value = stripped.split(":", 1)[1].strip().upper()
            include_link = value == "YES"
        elif stripped == "" and i <= 3:
            # Blank line after headers — response text starts after this
            text_start = i + 1
            break

    response_text = "\n".join(lines[text_start:]).strip()
    return (confidence, include_link, response_text)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def build_output_filename(args: argparse.Namespace) -> str:
    """
    Build a descriptive filename slug from the run parameters.

    Examples:
      --subreddit Pottery              → opportunities_pottery_2026-04-06.md
      --subreddit Pottery --dry-run    → opportunities_pottery_dry_2026-04-06.md
      (full run)                       → opportunities_all_2026-04-06.md
      (full run --dry-run)             → opportunities_all_dry_2026-04-06.md
    """
    scope = args.subreddit.lower() if args.subreddit else "all"
    dry = "_dry" if args.dry_run else ""
    date = datetime.now().strftime("%Y-%m-%d")
    return f"opportunities_{scope}{dry}_{date}.md"


def format_age(age_days: float) -> str:
    if age_days < 1:
        hours = int(age_days * 24)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(age_days)
    return f"{days} day{'s' if days != 1 else ''} ago"


def write_report(
    opportunities: list[dict],
    output_path: Path,
    dry_run: bool = False,
    failed_subreddits: list[str] | None = None,
) -> None:
    """
    Write the markdown opportunities report to output_path.
    Creates the parent directory if needed. Overwrites same-day files (idempotent).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Social Listening Opportunities: {today}",
        "",
    ]

    if dry_run:
        lines += ["**DRY RUN** - Claude drafting was skipped. Reddit data only.", ""]

    if failed_subreddits:
        lines += [
            f"**Warning:** The following subreddits could not be searched: "
            f"{', '.join(f'r/{s}' for s in failed_subreddits)}",
            "",
        ]

    if not opportunities:
        lines += ["No opportunities found matching the current filters.", ""]
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines += [
        f"Found **{len(opportunities)}** engagement opportunities.",
        "",
    ]

    for i, opp in enumerate(opportunities, 1):
        thread: RedditThread = opp["thread"]
        confidence: str = opp["confidence"]
        include_link: bool = opp.get("include_link", False)
        response: str = opp["drafted_response"]
        link_label = "YES" if include_link else "NO"

        lines += [
            f"## Opportunity #{i}",
            f"**Subreddit:** r/{thread.subreddit}",
            f'**Thread:** "{thread.title}"',
            f"**URL:** {thread.url}",
            f"**Posted:** {format_age(thread.age_days)} | {thread.score} upvotes | {thread.num_comments} comments",
            f"**Confidence:** {confidence} | **Link:** {link_label}",
            "",
            "**Drafted Response:**",
            "---",
            response,
            "---",
            "",
        ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    """
    Main pipeline:
    1. Build Reddit client
    2. Fetch threads across subreddits × search terms
    3. Filter (show-and-tell, clayfinder already mentioned)
    4. Rank by priority score
    5. Classify confidence
    6. Draft responses via Claude (unless --dry-run)
    7. Write markdown report
    """
    if args.dry_run:
        console.print(Panel(
            "[yellow]DRY RUN[/yellow] — Claude drafting will be skipped",
            expand=False,
        ))

    # Build Reddit session (no credentials needed)
    reddit = build_session()

    # Determine subreddit list
    subreddits = [args.subreddit] if args.subreddit else TARGET_SUBREDDITS

    # Collect threads
    console.print(f"\n[bold]Searching {len(subreddits)} subreddit(s) × {len(SEARCH_TERMS)} search terms...[/bold]")

    seen_ids: set[str] = set()
    all_threads: list[RedditThread] = []
    failed_subreddits: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Searching Reddit...", total=None)

        for subreddit in subreddits:
            subreddit_threads: list[RedditThread] = []

            for term in SEARCH_TERMS:
                progress.update(task, description=f"r/{subreddit} — \"{term}\"")
                results = search_subreddit(
                    reddit,
                    subreddit_name=subreddit,
                    search_term=term,
                    limit=args.limit,
                    days=args.days,
                    seen_ids=seen_ids,
                )
                subreddit_threads.extend(results)

            if not subreddit_threads and subreddit not in [s.subreddit for s in all_threads]:
                # Only mark as failed if we got zero results AND no prior results from it
                pass  # Warnings are printed inline by search_subreddit

            all_threads.extend(subreddit_threads)

    console.print(f"Fetched [bold]{len(all_threads)}[/bold] unique threads before filtering")

    # Apply filters
    filtered = []
    for t in all_threads:
        if t.mentions_clayfinder:
            continue
        if is_show_and_tell(t):
            continue
        # For broad subreddits, require pottery keywords in title or body
        if t.subreddit in BROAD_SUBREDDITS:
            combined = (t.title + " " + t.selftext).lower()
            if not any(kw in combined for kw in POTTERY_KEYWORDS):
                continue
        filtered.append(t)

    removed = len(all_threads) - len(filtered)
    console.print(
        f"Filtered out [dim]{removed}[/dim] threads "
        f"(show-and-tell or already mentions clayfinder)"
    )
    console.print(f"[bold]{len(filtered)}[/bold] threads remaining")

    if not filtered:
        console.print("[yellow]No threads passed filters. Try increasing --days or --limit.[/yellow]")
        # Still write an empty report
        output_path = OUTPUTS_DIR / build_output_filename(args)
        write_report([], output_path, dry_run=args.dry_run, failed_subreddits=failed_subreddits)
        console.print(f"\nReport written to [bold]{output_path}[/bold]")
        return

    # Sort by score before Claude (rough priority — Claude will reclassify)
    filtered.sort(key=score_thread, reverse=True)

    # Build initial opportunity list (confidence assigned by Claude during drafting)
    opportunities = [
        {"thread": t, "confidence": "LOW", "include_link": False, "drafted_response": ""}
        for t in filtered
    ]

    # Draft responses via Claude — Claude assigns confidence and link decision
    if args.dry_run:
        for opp in opportunities:
            opp["drafted_response"] = "[dry-run — Claude not called]"
    else:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_key:
            console.print("[red]Error:[/red] ANTHROPIC_API_KEY not set in environment")
            sys.exit(1)

        client = anthropic.Anthropic(api_key=anthropic_key)

        try:
            system_prompt = load_system_prompt()
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        console.print(f"\n[bold]Drafting responses for {len(opportunities)} opportunities...[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Drafting...", total=len(opportunities))

            for i, opp in enumerate(opportunities, 1):
                thread = opp["thread"]
                progress.update(
                    task,
                    description=f"Drafting {i}/{len(opportunities)}: {thread.title[:60]}...",
                )
                confidence, include_link, response_text = draft_response(client, thread, system_prompt)
                opp["confidence"] = confidence
                opp["include_link"] = include_link
                opp["drafted_response"] = response_text
                progress.advance(task)

        # Re-sort after Claude has classified everything: HIGH → MEDIUM → LOW
        opportunities.sort(
            key=lambda o: (CONFIDENCE_ORDER[o["confidence"]], -score_thread(o["thread"]))
        )

    # Write report
    output_path = OUTPUTS_DIR / build_output_filename(args)
    write_report(
        opportunities,
        output_path,
        dry_run=args.dry_run,
        failed_subreddits=failed_subreddits,
    )

    # Summary
    high = sum(1 for o in opportunities if o["confidence"] == "HIGH")
    medium = sum(1 for o in opportunities if o["confidence"] == "MEDIUM")
    low = sum(1 for o in opportunities if o["confidence"] == "LOW")

    console.print(Panel(
        f"[bold green]Done![/bold green]\n\n"
        f"  Opportunities: [bold]{len(opportunities)}[/bold] total  "
        f"([green]{high} HIGH[/green] / [yellow]{medium} MEDIUM[/yellow] / [dim]{low} LOW[/dim])\n"
        f"  Report: [bold]{output_path}[/bold]",
        title="clayfinder-growth",
        expand=False,
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_dotenv()
    args = parse_args()
    run(args)
