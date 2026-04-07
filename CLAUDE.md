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
- **Claude classifies confidence and link decision** — do not use string matching for this
- **HIGH threads must include clayfinder.com** — person is actively seeking a studio or class
- **3–5 sentences max** per drafted response — Reddit ignores walls of text
- Outputs go to `outputs/opportunities_{scope}_{date}.md` (gitignored)

## Repo Layout

```
src/reddit_client.py         requests-based Reddit public JSON client — search, fetch, dedup, filter
src/social_agent.py          CLI entry point + orchestrator + report writer
src/prompts/
  engagement_system.md       Claude system prompt — defines voice, confidence classification, link rules
outputs/                     Daily markdown reports (gitignored except .gitkeep)
```

## Running

```bash
python src/social_agent.py                        # Full run — all subreddits, all terms (~$0.50-1.00)
python src/social_agent.py --subreddit Pottery    # Target one subreddit
python src/social_agent.py --dry-run              # Fetch + filter only, skip Claude
python src/social_agent.py --limit 10             # Cap threads per subreddit per term
python src/social_agent.py --days 7               # Only threads from last N days (default: 30)
```

## Environment Variables

```
ANTHROPIC_API_KEY         Claude API key (claude-sonnet-4-6)
REDDIT_USER_AGENT         e.g. "clayfinder-growth:v1.0 (by /u/feltlucky_justhappy)"
```

No Reddit API credentials needed — uses Reddit's public JSON endpoints.

## Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Reddit client | `requests` + public JSON API | Reddit now requires pre-approval for script apps; public endpoints need only a User-Agent |
| Confidence classification | Claude (not string matching) | String matching produced too many false positives; Claude reads full context |
| Claude output format | Structured header (CONFIDENCE + INCLUDE_LINK) then comment | Single call per thread handles both classification and drafting |
| Deduplication | `seen_ids: set[str]` passed by reference | Catches dups across subreddit × term loops |
| Claude calls | Sequential, one per opportunity | Interactive CLI; avoids batch API latency |
| Report filenames | `opportunities_{scope}_{dry}_{date}.md` | Descriptive — distinguishes full runs, subreddit runs, and dry runs |

## Filtering Logic (pre-Claude)

Threads are **excluded** if:
- Older than `--days` threshold
- Score (net upvotes) < 3
- Show-and-tell post: title starts with known sharing phrase OR (no `?` AND no question keywords AND body < 30 chars)
- clayfinder.com already mentioned in title/body/comments
- From broad subreddits (AskMen, AskWomen, Frugal, moving, AskReddit, Hobbyists) without pottery keywords in title/body

## Confidence Levels (assigned by Claude)

- **HIGH** — person actively seeking a studio or class. clayfinder.com is directly useful. Link always included.
- **MEDIUM** — genuine pottery question (technique, gear, advice). Link included if naturally relevant.
- **LOW** — sharing/social post. Brief supportive comment, no link.

## Report Format

```
## Opportunity #N
**Subreddit:** r/X
**Thread:** "title"
**URL:** https://...
**Posted:** X days ago | Y upvotes | Z comments
**Confidence:** HIGH | **Link:** YES

[drafted response ready to copy-paste]
```

## Coding Conventions

- Type hints on all function signatures
- `dataclasses` for data structures
- `rich` for all terminal output
- Synchronous — 1s delay between Reddit requests to respect public rate limit
- Functions catch specific exceptions, not bare `except:`

## Current Status

- **2026-04-06** — Initial build. Switched from PRAW to public JSON API (Reddit pre-approval required for script apps).
- **2026-04-07** — Classification moved entirely into Claude (single API call per thread handles both confidence rating and drafting). String-matching classifiers removed. System prompt tightened with concrete HIGH examples and explicit link rules. Output filename now descriptive: `opportunities_{scope}_{dry}_{date}.md`.
  Run: `python src/social_agent.py --days 30`
