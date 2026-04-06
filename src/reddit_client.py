"""
reddit_client.py — PRAW wrapper for clayfinder-growth social listening.

Provides:
  - build_reddit_client(): instantiate read-only PRAW client
  - search_subreddit(): search one subreddit for one search term
  - RedditThread: dataclass representing a fetched thread
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import praw
import prawcore.exceptions
from rich.console import Console

console = Console()

QUESTION_KEYWORDS = [
    "recommend",
    "looking for",
    "where",
    "best",
    "find",
    "help",
    "advice",
    "suggestion",
    "beginner",
    "start",
    "how",
    "any",
    "anyone",
    "can i",
    "should i",
    "which",
]


@dataclass
class RedditThread:
    id: str                          # PRAW submission.id — stable identifier, used for dedup
    subreddit: str                   # e.g. "Pottery"
    title: str
    url: str                         # Full reddit.com permalink
    selftext: str                    # Body text (empty string for link posts or deleted posts)
    score: int                       # Net upvotes
    num_comments: int
    created_utc: float               # Unix timestamp
    age_days: float                  # Computed at fetch time
    top_comments: list[str] = field(default_factory=list)  # Top 5 comment bodies
    mentions_clayfinder: bool = False
    search_term: str = ""            # Which search term surfaced this thread


def build_reddit_client() -> praw.Reddit:
    """
    Instantiate a read-only PRAW client from environment variables.

    Required env vars: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT.
    Raises RuntimeError if any are missing.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT")

    missing = [k for k, v in {
        "REDDIT_CLIENT_ID": client_id,
        "REDDIT_CLIENT_SECRET": client_secret,
        "REDDIT_USER_AGENT": user_agent,
    }.items() if not v]

    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in your Reddit credentials."
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        read_only=True,
        ratelimit_seconds=1,
    )


def search_subreddit(
    reddit: praw.Reddit,
    subreddit_name: str,
    search_term: str,
    limit: int = 25,
    days: int = 30,
    seen_ids: set[str] | None = None,
) -> list[RedditThread]:
    """
    Search one subreddit for one search term and return matching threads.

    Filters applied:
      - Threads older than `days` are skipped
      - Threads with score < 3 are skipped
      - Threads whose id is already in `seen_ids` are skipped (deduplication)
      - Thread ids that pass are added to `seen_ids` in-place

    Returns an empty list (with a warning) on any Reddit API error.
    """
    if seen_ids is None:
        seen_ids = set()

    time_filter = _days_to_time_filter(days)
    threads: list[RedditThread] = []
    now = datetime.now(timezone.utc).timestamp()

    try:
        subreddit = reddit.subreddit(subreddit_name)
        results = subreddit.search(
            query=search_term,
            sort="relevance",
            time_filter=time_filter,
            limit=limit,
        )

        for submission in results:
            # Deduplication
            if submission.id in seen_ids:
                continue

            # Age filter
            age_days = (now - submission.created_utc) / 86400
            if age_days > days:
                seen_ids.add(submission.id)  # Mark seen so we don't reprocess
                continue

            # Score filter
            if submission.score < 3:
                seen_ids.add(submission.id)
                continue

            # Fetch full context (selftext + top comments)
            selftext, top_comments = get_thread_context(submission)

            # Check for clayfinder mention
            mentions_cf = _mentions_clayfinder(submission)

            thread = RedditThread(
                id=submission.id,
                subreddit=subreddit_name,
                title=submission.title,
                url=f"https://reddit.com{submission.permalink}",
                selftext=selftext,
                score=submission.score,
                num_comments=submission.num_comments,
                created_utc=submission.created_utc,
                age_days=round(age_days, 1),
                top_comments=top_comments,
                mentions_clayfinder=mentions_cf,
                search_term=search_term,
            )

            seen_ids.add(submission.id)
            threads.append(thread)

    except prawcore.exceptions.Redirect:
        console.print(f"  [yellow]Warning:[/yellow] r/{subreddit_name} does not exist — skipping")
    except prawcore.exceptions.Forbidden:
        console.print(f"  [yellow]Warning:[/yellow] r/{subreddit_name} is private — skipping")
    except prawcore.exceptions.TooManyRequests:
        console.print(f"  [yellow]Warning:[/yellow] Rate limited on r/{subreddit_name} — waiting 60s")
        time.sleep(60)
    except prawcore.exceptions.PrawcoreException as e:
        console.print(f"  [yellow]Warning:[/yellow] Reddit API error on r/{subreddit_name}: {e}")

    return threads


def get_thread_context(submission) -> tuple[str, list[str]]:
    """
    Return (selftext, top_5_comment_bodies) for a PRAW submission.

    Uses replace_more(limit=0) to avoid triggering extra API calls for
    MoreComments objects. Comments are sorted by score descending.
    Handles deleted/removed posts by treating them as empty strings.
    """
    # Normalise selftext
    selftext = submission.selftext or ""
    if selftext in ("[deleted]", "[removed]"):
        selftext = ""

    # Fetch top comments without expanding MoreComments
    try:
        submission.comments.replace_more(limit=0)
        all_comments = submission.comments.list()
        # Sort by score, take top 5
        sorted_comments = sorted(
            all_comments,
            key=lambda c: getattr(c, "score", 0),
            reverse=True,
        )
        top_comments = []
        for comment in sorted_comments[:5]:
            body = getattr(comment, "body", "")
            if body and body not in ("[deleted]", "[removed]"):
                top_comments.append(body)
    except Exception:
        top_comments = []

    return selftext, top_comments


def _days_to_time_filter(days: int) -> str:
    """Map a --days integer to the nearest PRAW time_filter string."""
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 30:
        return "month"
    if days <= 365:
        return "year"
    return "all"


def _mentions_clayfinder(submission) -> bool:
    """
    Return True if "clayfinder" appears anywhere in the thread
    (title, selftext, or any comment body). Case-insensitive.
    """
    needle = "clayfinder"

    if needle in submission.title.lower():
        return True

    selftext = submission.selftext or ""
    if needle in selftext.lower():
        return True

    try:
        submission.comments.replace_more(limit=0)
        for comment in submission.comments.list():
            body = getattr(comment, "body", "") or ""
            if needle in body.lower():
                return True
    except Exception:
        pass

    return False


def is_show_and_tell(thread: RedditThread) -> bool:
    """
    Heuristic to detect show-and-tell / gallery posts that have no question.

    Returns True (exclude this thread) if ALL of:
      1. No "?" in the title
      2. No question keywords in the title (case-insensitive)
      3. Body text is empty or under 30 characters

    A post needs to fail only one condition to be kept.
    """
    title_lower = thread.title.lower()

    has_question_mark = "?" in thread.title
    if has_question_mark:
        return False

    has_question_keyword = any(kw in title_lower for kw in QUESTION_KEYWORDS)
    if has_question_keyword:
        return False

    body_is_short = len(thread.selftext.strip()) < 30
    if not body_is_short:
        return False

    return True
