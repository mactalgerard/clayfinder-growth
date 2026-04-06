# clayfinder-growth — Claude Code Context

## What This Project Is

A Python CLI Social Listening Agent that monitors Reddit for pottery/ceramics
discussions, filters for genuine engagement opportunities, drafts authentic
responses via Claude API, and saves a daily markdown report.

This toolkit drives organic traffic and backlinks to **clayfinder.com** — a free
directory of pottery and ceramics studios across the US (1,993 listings, 35 states,
259+ cities). Primary keywords: `pottery classes near me` (110k SV/mo, KD 4).

## Critical Rules

- **Never post anything to Reddit** — this tool is read-only and draft-only
- **Never use marketing language** in drafted responses ("amazing", "best", "check out")
- **Always read full thread context** before drafting (title + selftext + top comments)
- **Mention clayfinder.com only if it directly helps** the person asking
- **Mark `[no link]`** if clayfinder.com is not naturally relevant to the thread
- **3–5 sentences max** per drafted response — Reddit ignores walls of text
- Outputs go to `outputs/opportunities_YYYY-MM-DD.md` (gitignored)

## Repo Layout

```
src/reddit_client.py         PRAW wrapper — search, fetch, dedup, filter
src/social_agent.py          CLI entry point + orchestrator + report writer
src/prompts/
  engagement_system.md       Claude system prompt — defines the authentic voice
outputs/                     Daily markdown reports (gitignored except .gitkeep)
```

## Running

```bash
python src/social_agent.py                        # Full run — all subreddits, all terms
python src/social_agent.py --subreddit pottery    # Target one subreddit
python src/social_agent.py --dry-run              # Fetch + filter only, skip Claude
python src/social_agent.py --limit 20             # Cap threads per subreddit per term
python src/social_agent.py --days 7               # Only threads from last N days
```

## Environment Variables

```
ANTHROPIC_API_KEY         Claude API key
REDDIT_CLIENT_ID          PRAW script app client ID
REDDIT_CLIENT_SECRET      PRAW script app client secret
REDDIT_USER_AGENT         e.g. "clayfinder-growth:v1.0 (by /u/youruser)"
```

Set up a Reddit script app at reddit.com/prefs/apps — read-only access is sufficient.

## Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Reddit client | PRAW read-only | Simplest; never needs write access |
| Deduplication | `seen_ids: set[str]` passed by reference | Catches dups across subreddit × term loops |
| Claude calls | Sequential, one per opportunity | Interactive CLI; avoids batch API latency |
| PRAW time filter | Mapped from `--days` arg | Reddit-side pre-filter reduces data transfer |
| Comment fetch | `replace_more(limit=0)` | Avoids extra API calls for MoreComments |
| Report format | Markdown with copy-paste blocks | User posts manually — easy to review |

## Coding Conventions

- Type hints on all function signatures
- `dataclasses` for data structures (no Pydantic — too heavy for this use case)
- `rich` for all terminal output — no raw `print()` except in tests
- Synchronous — no async needed; PRAW's built-in rate limiter handles Reddit limits
- Functions catch specific exceptions, not bare `except:`
- Each module is runnable standalone for quick testing

## Filtering Logic

Threads are **excluded** if:
- Older than `--days` threshold
- Score (net upvotes) < 3
- Show-and-tell post: no `?` in title AND no question keywords AND body < 30 chars
- clayfinder.com already mentioned in title/body/comments

Threads are **prioritised** if:
- Title contains recommendation keywords ("recommend", "looking for", "where to find")
- Score ≥ 50 or ≥ 10
- Comment count ≥ 5
- Age ≤ 14 days

## Confidence Levels

- **HIGH** — explicit recommendation request ("can anyone recommend", "looking for classes")
- **MEDIUM** — general question with some relevance to finding studios
- **LOW** — tangential mention, not a direct ask

## Current Status

- **2026-04-06** — Initial build. All files scaffolded and implemented.
  Test with: `python src/social_agent.py --dry-run --subreddit Pottery --limit 5`
