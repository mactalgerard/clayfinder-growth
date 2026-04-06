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

RECOMMENDATION_KEYWORDS = [
    "recommend",
    "looking for",
    "where to find",
    "best ",
    "suggestions",
    "any good",
    "how do i find",
    "help me find",
    "trying to find",
]

HIGH_CONFIDENCE_PHRASES = [
    "can anyone recommend",
    "looking for classes",
    "looking for studios",
    "where to find",
    "how do i find",
    "trying to find",
    "need to find",
    "any recommendations",
    "any suggestions",
    "where can i",
    "help finding",
]


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
    Priority score for sorting. Higher = more worth engaging with.

    +3  explicit recommendation request in title
    +2  score >= 50
    +1  score >= 10
    +1  num_comments >= 5
    -1  age_days > 14
    """
    s = 0
    title_lower = thread.title.lower()

    if any(kw in title_lower for kw in RECOMMENDATION_KEYWORDS):
        s += 3

    if thread.score >= 50:
        s += 2
    elif thread.score >= 10:
        s += 1

    if thread.num_comments >= 5:
        s += 1

    if thread.age_days > 14:
        s -= 1

    return s


def classify_confidence(thread: RedditThread) -> str:
    """
    HIGH   — explicit recommendation or resource request
    MEDIUM — general question with relevance to finding studios
    LOW    — tangential or weak match
    """
    title_lower = thread.title.lower()
    body_lower = thread.selftext.lower()
    combined = title_lower + " " + body_lower

    if any(phrase in combined for phrase in HIGH_CONFIDENCE_PHRASES):
        return "HIGH"

    if "?" in thread.title and any(kw in title_lower for kw in RECOMMENDATION_KEYWORDS):
        return "HIGH"

    if "pottery" in combined or "ceramics" in combined or "studio" in combined:
        if "?" in thread.title:
            return "MEDIUM"
        if len(thread.selftext.strip()) > 50:
            return "MEDIUM"

    return "LOW"


def why_reason(thread: RedditThread, confidence: str) -> str:
    """One-line explanation of why this thread is an opportunity."""
    title_lower = thread.title.lower()

    if confidence == "HIGH":
        if any(p in title_lower for p in ["recommend", "suggestion", "where"]):
            return "Direct recommendation request — person is actively looking for a studio or class."
        return "High-intent question about finding pottery resources."

    if confidence == "MEDIUM":
        if thread.score >= 20:
            return f"General pottery question with good reach ({thread.score} upvotes, {thread.num_comments} comments)."
        return "Beginner or discovery question — good community engagement opportunity."

    return "Tangential pottery discussion — low-effort community presence reply."


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
) -> str:
    """
    Call Claude to draft an engagement response for the given thread.

    Retries once on rate limit (waits 60s). Returns an error placeholder
    on other API failures so the report can still be written.
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
            return response.content[0].text.strip()

        except anthropic.RateLimitError:
            if attempt == 0:
                console.print("  [yellow]Rate limited — waiting 60s before retry...[/yellow]")
                time.sleep(60)
            else:
                return "[drafting failed — rate limit exceeded]"

        except anthropic.APIError as e:
            return f"[drafting failed — API error: {e}]"

    return "[drafting failed — unknown error]"


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
        f"# Social Listening Opportunities — {today}",
        "",
    ]

    if dry_run:
        lines += ["**DRY RUN** — Claude drafting was skipped. Reddit data only.", ""]

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
        why: str = opp["why"]
        response: str = opp["drafted_response"]

        lines += [
            f"## Opportunity #{i}",
            f"**Subreddit:** r/{thread.subreddit}",
            f'**Thread:** "{thread.title}"',
            f"**URL:** {thread.url}",
            f"**Posted:** {format_age(thread.age_days)} | {thread.score} upvotes | {thread.num_comments} comments",
            f"**Confidence:** {confidence}",
            "",
            f"**Why:** {why}",
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
    filtered = [
        t for t in all_threads
        if not t.mentions_clayfinder and not is_show_and_tell(t)
    ]

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

    # Classify confidence and build opportunity list
    opportunities = []
    for thread in filtered:
        confidence = classify_confidence(thread)
        opportunities.append({
            "thread": thread,
            "confidence": confidence,
            "why": why_reason(thread, confidence),
            "drafted_response": "",
        })

    # Sort: HIGH → MEDIUM → LOW, then by score within each group
    opportunities.sort(
        key=lambda o: (CONFIDENCE_ORDER[o["confidence"]], -score_thread(o["thread"]))
    )

    # Draft responses via Claude
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
                opp["drafted_response"] = draft_response(client, thread, system_prompt)
                progress.advance(task)

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
