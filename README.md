# clayfinder-growth — Social Listening Agent

A CLI tool that monitors Reddit for pottery and ceramics discussions, identifies genuine engagement opportunities, and drafts authentic responses mentioning [clayfinder.com](https://clayfinder.com) where relevant.

The agent **never posts autonomously**. All output is a markdown report you review and act on manually.

---

## Prerequisites

- Python 3.11+
- An Anthropic API key
- No Reddit API credentials needed

---

## Setup

```bash
# 1. Clone and create virtual environment
git clone https://github.com/mactalgerard/clayfinder-growth.git
cd clayfinder-growth
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in your keys
```

`.env` only needs two values:
```
ANTHROPIC_API_KEY=sk-ant-...
REDDIT_USER_AGENT=clayfinder-growth:v1.0 (by /u/feltlucky_justhappy)
```

No Reddit app registration required — the agent uses Reddit's public JSON endpoints.

---

## Running

```bash
# Full run — all subreddits, all search terms (~$0.50-1.00 in API credits)
python src/social_agent.py

# Target a single subreddit
python src/social_agent.py --subreddit Pottery

# Dry run — fetch and filter threads, skip Claude drafting
python src/social_agent.py --dry-run

# Limit threads per subreddit per search term (default: 25)
python src/social_agent.py --limit 10

# Only include threads from the last N days (default: 30)
python src/social_agent.py --days 7

# Combine flags
python src/social_agent.py --subreddit Pottery --dry-run --limit 5 --days 7
```

Reports are saved to `outputs/opportunities_{scope}_{date}.md`:
- `opportunities_all_2026-04-07.md` — full run
- `opportunities_pottery_2026-04-07.md` — single subreddit
- `opportunities_all_dry_2026-04-07.md` — dry run

---

## Output Format

Each opportunity in the report:

```markdown
## Opportunity #1
**Subreddit:** r/Ceramics
**Thread:** "Where do you guys fire your pottery? (I live in Georgia and am just getting started)"
**URL:** https://reddit.com/r/Ceramics/comments/...
**Posted:** 28 days ago | 6 upvotes | 18 comments
**Confidence:** HIGH | **Link:** YES

---
Starting out in Georgia, your easiest path is finding a local ceramics studio that
offers kiln access — most charge per cubic inch or by piece weight, and you get to
use their glazes while you're learning. Clayfinder.com lists studios by state so
you can filter down to Georgia and see what's near you. Once you've got a feel for
the process, the argument for eventually getting your own kiln starts to make sense.
---
```

### Confidence Levels

| Level | Meaning | Link |
|---|---|---|
| HIGH | Person actively seeking a studio or class — clayfinder.com directly helps | Always included |
| MEDIUM | Genuine pottery question (technique, gear, advice) — worth engaging | Included if natural fit |
| LOW | Sharing or social post — brief supportive comment | Not included |

Confidence and link decisions are made by Claude after reading the full thread context — not by keyword matching.

---

## How It Works

1. Searches Reddit's public JSON API across 10 subreddits × 10 search terms
2. Filters out show-and-tell posts, threads mentioning clayfinder.com already, and off-topic threads
3. For each passing thread, calls Claude (claude-sonnet-4-6) which:
   - Reads the full thread context (title + body + top 5 comments)
   - Classifies confidence (HIGH / MEDIUM / LOW)
   - Decides whether clayfinder.com is relevant (YES / NO)
   - Drafts an authentic 3-5 sentence response
4. Sorts results HIGH → MEDIUM → LOW and writes the markdown report

---

## Subreddits Monitored

`r/Pottery`, `r/Ceramics`, `r/PotteryClasses`, `r/ArtClasses`, `r/Hobbyists`,
`r/AskWomen`, `r/AskMen`, `r/Frugal`, `r/moving`, `r/AskReddit`

Add more in `TARGET_SUBREDDITS` in [src/social_agent.py](src/social_agent.py).

---

## Cost

~$0.50–$1.00 per full 30-day run. Essentially free for daily use.

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude API key |
| `REDDIT_USER_AGENT` | e.g. `clayfinder-growth:v1.0 (by /u/feltlucky_justhappy)` |
