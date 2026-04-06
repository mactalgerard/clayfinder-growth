"""
reddit_client.py — Reddit public JSON API client for clayfinder-growth.

No API credentials required. Uses Reddit's public JSON endpoints with a
User-Agent header. Rate limit: ~1 request/second for unauthenticated access.

Provides:
  - build_session(): create a requests.Session with User-Agent set
  - search_subreddit(): search one subreddit for one search term
  - get_thread_context(): fetch selftext + top comments for a thread
  - RedditThread: dataclass representing a fetched thread
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from rich.console import Console

console = Console()

REDDIT_BASE = "https://www.reddit.com"
REQUEST_DELAY = 1.0  # seconds between requests — stay within public rate limit

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
    id: str                          # Reddit submission id — used for dedup
    subreddit: str                   # e.g. "Pottery"
    title: str
    url: str                         # Full reddit.com permalink
    selftext: str                    # Body text (empty for link posts or deleted)
    score: int                       # Net upvotes
    num_comments: int
    created_utc: float               # Unix timestamp
    age_days: float                  # Computed at fetch time
    top_comments: list[str] = field(default_factory=list)  # Top 5 comment bodies
    mentions_clayfinder: bool = False
    search_term: str = ""            # Which search term surfaced this thread


def build_session() -> requests.Session:
    """
    Build a requests.Session with a User-Agent header.

    User-Agent is read from the REDDIT_USER_AGENT env var.
    Falls back to a sensible default if not set.
    No Reddit API credentials are needed.
    """
    user_agent = os.environ.get(
        "REDDIT_USER_AGENT",
        "clayfinder-growth:v1.0 (by /u/feltlucky_justhappy)",
    )
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def search_subreddit(
    session: requests.Session,
    subreddit_name: str,
    search_term: str,
    limit: int = 25,
    days: int = 30,
    seen_ids: set[str] | None = None,
) -> list[RedditThread]:
    """
    Search one subreddit for one search term via the public JSON API.

    Endpoint: GET /r/{subreddit}/search.json
    Filters applied:
      - Threads older than `days` are skipped
      - Threads with score < 3 are skipped
      - Threads already in `seen_ids` are skipped (deduplication)
      - Passing thread ids are added to `seen_ids` in-place

    Returns an empty list (with a warning logged) on any API error.
    """
    if seen_ids is None:
        seen_ids = set()

    time_filter = _days_to_time_filter(days)
    threads: list[RedditThread] = []
    now = datetime.now(timezone.utc).timestamp()

    params = {
        "q": search_term,
        "sort": "relevance",
        "t": time_filter,
        "limit": min(limit, 100),  # Reddit caps at 100
        "restrict_sr": "1",        # Restrict to this subreddit only
        "type": "link",
    }
    url = f"{REDDIT_BASE}/r/{subreddit_name}/search.json?{urlencode(params)}"

    for attempt in range(2):
        try:
            resp = session.get(url, timeout=15)

            if resp.status_code == 404:
                console.print(f"  [yellow]Warning:[/yellow] r/{subreddit_name} does not exist — skipping")
                return []
            if resp.status_code == 403:
                console.print(f"  [yellow]Warning:[/yellow] r/{subreddit_name} is private — skipping")
                return []
            if resp.status_code == 429:
                if attempt == 0:
                    console.print(f"  [yellow]Warning:[/yellow] Rate limited on r/{subreddit_name} — waiting 60s")
                    time.sleep(60)
                    continue
                console.print(f"  [yellow]Warning:[/yellow] Rate limited on r/{subreddit_name} after retry — skipping")
                return []

            resp.raise_for_status()
            data = resp.json()
            break

        except requests.RequestException as e:
            console.print(f"  [yellow]Warning:[/yellow] Request failed for r/{subreddit_name}: {e}")
            return []
    else:
        return []

    time.sleep(REQUEST_DELAY)

    children = data.get("data", {}).get("children", [])

    for child in children:
        post = child.get("data", {})
        post_id = post.get("id", "")

        if not post_id or post_id in seen_ids:
            continue

        # Age filter
        created_utc = float(post.get("created_utc", 0))
        age_days = (now - created_utc) / 86400
        if age_days > days:
            seen_ids.add(post_id)
            continue

        # Score filter
        score = int(post.get("score", 0))
        if score < 3:
            seen_ids.add(post_id)
            continue

        # Normalise selftext
        selftext = post.get("selftext", "") or ""
        if selftext in ("[deleted]", "[removed]"):
            selftext = ""

        permalink = post.get("permalink", "")
        full_url = f"{REDDIT_BASE}{permalink}" if permalink else ""

        # Fetch comments for context
        _, top_comments = get_thread_context(session, subreddit_name, post_id)

        # Check clayfinder mention
        title = post.get("title", "")
        mentions_cf = _mentions_clayfinder(title, selftext, top_comments)

        thread = RedditThread(
            id=post_id,
            subreddit=subreddit_name,
            title=title,
            url=full_url,
            selftext=selftext,
            score=score,
            num_comments=int(post.get("num_comments", 0)),
            created_utc=created_utc,
            age_days=round(age_days, 1),
            top_comments=top_comments,
            mentions_clayfinder=mentions_cf,
            search_term=search_term,
        )

        seen_ids.add(post_id)
        threads.append(thread)

    return threads


def get_thread_context(
    session: requests.Session,
    subreddit: str,
    thread_id: str,
) -> tuple[str, list[str]]:
    """
    Fetch selftext and top 5 comments for a thread via the public JSON API.

    Endpoint: GET /r/{subreddit}/comments/{id}.json
    Response is a 2-element list:
      [0] post listing (already have this data — used for selftext fallback)
      [1] comments listing — data.children[*].data with .body and .score

    Returns (selftext, top_5_comment_bodies_by_score).
    Handles deleted/removed bodies by treating them as empty strings.
    """
    url = f"{REDDIT_BASE}/r/{subreddit}/comments/{thread_id}.json"

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        time.sleep(REQUEST_DELAY)
    except Exception:
        return "", []

    # data[0] = post listing, data[1] = comments listing
    if not isinstance(data, list) or len(data) < 2:
        return "", []

    # Extract selftext from post listing (fallback)
    try:
        post_data = data[0]["data"]["children"][0]["data"]
        selftext = post_data.get("selftext", "") or ""
        if selftext in ("[deleted]", "[removed]"):
            selftext = ""
    except (KeyError, IndexError):
        selftext = ""

    # Extract top comments
    try:
        comment_children = data[1]["data"]["children"]
        comments = []
        for child in comment_children:
            if child.get("kind") != "t1":
                continue
            cdata = child.get("data", {})
            body = cdata.get("body", "") or ""
            if body in ("[deleted]", "[removed]", ""):
                continue
            score = int(cdata.get("score", 0))
            comments.append((score, body))

        # Sort by score descending, take top 5
        comments.sort(key=lambda x: x[0], reverse=True)
        top_comments = [body for _, body in comments[:5]]
    except (KeyError, TypeError):
        top_comments = []

    return selftext, top_comments


def _days_to_time_filter(days: int) -> str:
    """Map a --days integer to the nearest Reddit time filter string."""
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 30:
        return "month"
    if days <= 365:
        return "year"
    return "all"


def _mentions_clayfinder(title: str, selftext: str, comments: list[str]) -> bool:
    """
    Return True if "clayfinder" appears anywhere in the thread.
    Case-insensitive substring check across title, body, and all comments.
    """
    needle = "clayfinder"
    if needle in title.lower():
        return True
    if needle in selftext.lower():
        return True
    return any(needle in c.lower() for c in comments)


def is_show_and_tell(thread: RedditThread) -> bool:
    """
    Heuristic to detect show-and-tell / gallery posts with no question.

    Returns True (exclude this thread) if ALL of:
      1. No "?" in the title
      2. No question keywords in the title (case-insensitive)
      3. Body text is empty or under 30 characters

    A post needs to fail only one condition to be kept.
    """
    title_lower = thread.title.lower()

    if "?" in thread.title:
        return False

    if any(kw in title_lower for kw in QUESTION_KEYWORDS):
        return False

    if len(thread.selftext.strip()) >= 30:
        return False

    return True
