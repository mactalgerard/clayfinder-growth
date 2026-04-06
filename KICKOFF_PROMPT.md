# ClayFinder Growth — Kickoff Prompt
> Paste this into Claude Code at the start of a new session to initialise the build.

---

## Project Overview

You are helping me build `clayfinder-growth` — a lightweight Python toolkit that
drives organic traffic and backlinks to **clayfinder.com**, a pottery and ceramics
studio directory for the US (Canada and Australia coming).

This repo contains one tool to start: a **Social Listening Agent** that monitors
Reddit and other forums for pottery/ceramics discussions, then drafts authentic
engagement responses (posts, replies, comments) that I post manually from my
own Reddit account.

The agent is a read/draft tool — it never posts autonomously. All output is a
markdown report I review before acting on anything.

---

## What clayfinder.com Is

- A niche directory of pottery and ceramics studios across the US
- Built on Next.js + Supabase + Vercel
- Currently has 1,993 US listings across 35 states and 259+ cities
- Primary keywords: `pottery classes near me` (110k SV/mo, KD 4),
  `ceramics classes near me` (110k SV/mo, KD 3)
- Monetisation: lead generation for studios + display ads at scale
- Live at clayfinder.com — launched April 2026

---

## Social Listening Agent — What It Does

### Goal
Find Reddit threads and forum posts where people are:
- Asking where to find pottery/ceramics classes in a specific city
- Recommending studios or asking for recommendations
- Discussing pottery as a hobby and mentioning frustrations finding a studio
- Talking about topics adjacent to ClayFinder's value prop

### For each opportunity found, draft:
1. A **reply** to the thread that genuinely helps the person — answers their
   question first, then naturally mentions clayfinder.com as a resource if relevant
2. Or a **new post** if there's a gap in a subreddit (e.g. no "find studios near you"
   megathread exists)

### Tone & persona
The drafted responses should read as a genuine pottery enthusiast who also
happens to run a directory. Helpful first, promotional second (or not at all if the
thread doesn't warrant it). Authentic, conversational, not salesy. No fake claims.
The mention of clayfinder.com should feel like a natural recommendation, not an ad.

### Output
A markdown report saved to `outputs/opportunities_YYYY-MM-DD.md` with:
- Thread title, URL, subreddit, upvotes/comments (signal of reach)
- Why this is a good opportunity (one line)
- Drafted response ready to copy-paste
- Confidence rating: HIGH / MEDIUM / LOW (based on relevance and fit)

---

## Repo Structure

```
clayfinder-growth/
├── CLAUDE.md                        # Project memory for Claude Code
├── KICKOFF_PROMPT.md                # This file
├── .env                             # Secrets (gitignored)
├── .env.example                     # Template committed to git
├── .gitignore
├── requirements.txt
├── README.md
├── src/
│   ├── __init__.py
│   ├── reddit_client.py             # PRAW wrapper — search + fetch threads
│   ├── social_agent.py              # Main agent — orchestrates search + drafting
│   └── prompts/
│       └── engagement_system.md     # Agent system prompt
└── outputs/                         # Gitignored — reports saved here
    └── .gitkeep
```

---

## Tech Stack

- Python 3.11+
- `praw` — Reddit API wrapper (read-only access)
- `anthropic` — Claude API for drafting responses (claude-sonnet-4-6)
- `python-dotenv` — environment variables
- `rich` — terminal output formatting
- No frameworks — plain Python scripts

---

## Environment Variables

```
ANTHROPIC_API_KEY=sk-ant-...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=clayfinder-growth/1.0 by u/YOUR_REDDIT_USERNAME
```

Reddit credentials: register a read-only script app at reddit.com/prefs/apps.
Set `user_agent` to something descriptive — Reddit requires this to not block requests.
Read-only access is sufficient — the agent never posts.

---

## CLI Interface

```bash
python src/social_agent.py                        # Full run — all subreddits, all search terms
python src/social_agent.py --subreddit pottery    # Target one subreddit
python src/social_agent.py --dry-run              # Fetch threads only, skip drafting
python src/social_agent.py --limit 20             # Cap threads fetched per subreddit (default: 25)
python src/social_agent.py --days 7               # Only threads from last N days (default: 30)
```

---

## Target Subreddits

```python
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
```

---

## Search Terms

```python
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
```

---

## First Session Goals

1. Confirm `CLAUDE.md` has correct project context
2. Run `python src/social_agent.py --dry-run --subreddit Pottery --limit 5`
3. Confirm `outputs/opportunities_YYYY-MM-DD.md` is generated
4. Review the dry-run output — are threads relevant? Is filtering working?
5. Run a live draft: `python src/social_agent.py --subreddit Pottery --limit 5 --days 7`
6. Review the drafted responses for tone and authenticity

Do not build a web interface, scheduler, or anything that posts automatically.
This is a run-on-demand CLI tool only.
