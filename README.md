# clayfinder-growth — Social Listening Agent

A CLI tool that monitors Reddit for pottery and ceramics discussions, identifies genuine engagement opportunities, and drafts authentic responses mentioning [clayfinder.com](https://clayfinder.com) where relevant.

The agent **never posts autonomously**. All output is a markdown report you review and act on manually.

---

## Prerequisites

- Python 3.11+
- A Reddit script app (read-only access) — see setup below
- An Anthropic API key

---

## Setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/youruser/clayfinder-growth.git
cd clayfinder-growth
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env and fill in your keys
```

### Reddit API Setup

1. Go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. Click **Create another app**
3. Choose **script** type
4. Set redirect URI to `http://localhost:8080` (not used, but required)
5. Copy the client ID (under the app name) and client secret
6. Add both to your `.env` file

Read-only access is all that's needed — the agent never posts.

---

## Running

```bash
# Full run — all subreddits, all search terms
python src/social_agent.py

# Target a single subreddit
python src/social_agent.py --subreddit Pottery

# Dry run — fetch and filter threads, skip Claude drafting
python src/social_agent.py --dry-run

# Limit threads fetched per subreddit per search term (default: 25)
python src/social_agent.py --limit 10

# Only include threads from the last N days (default: 30)
python src/social_agent.py --days 7

# Combine flags
python src/social_agent.py --subreddit Pottery --dry-run --limit 5 --days 7
```

The report is saved to `outputs/opportunities_YYYY-MM-DD.md`.

---

## Output Format

Each opportunity in the report looks like this:

```markdown
## Opportunity #1
**Subreddit:** r/Pottery
**Thread:** "Best way to find pottery classes in a new city?"
**URL:** https://reddit.com/r/Pottery/comments/...
**Posted:** 2 days ago | 47 upvotes | 12 comments
**Confidence:** HIGH

**Why:** Direct recommendation request, high engagement, no directory mentioned yet.

**Drafted Response:**
---
Moving to a new city and wanting to find pottery studios is one of the more fun
parts of settling in honestly. Local art centres usually have beginner
wheel-throwing classes at reasonable prices, and Facebook groups for your city's
arts community often have pinned recommendations.

I also built a directory specifically for this — clayfinder.com — has studios
across the US with class types, skill levels, and whether they take drop-ins.
Might save you a few hours of Googling.
---
```

### Confidence Levels

| Level | Meaning |
|---|---|
| HIGH | Explicit recommendation request — someone is actively looking for studios or classes |
| MEDIUM | General question with relevance to finding a studio |
| LOW | Tangential mention — worth engaging with for community presence |

Responses marked `[no link]` are worth replying to for community building but don't need a clayfinder.com mention.

---

## Subreddits Monitored

`r/Pottery`, `r/Ceramics`, `r/PotteryClasses`, `r/ArtClasses`, `r/Hobbyists`,
`r/AskWomen`, `r/AskMen`, `r/Frugal`, `r/moving`, `r/AskReddit`

Add more in the `TARGET_SUBREDDITS` list in [src/social_agent.py](src/social_agent.py).

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude API key |
| `REDDIT_CLIENT_ID` | Reddit script app client ID |
| `REDDIT_CLIENT_SECRET` | Reddit script app client secret |
| `REDDIT_USER_AGENT` | e.g. `clayfinder-growth:v1.0 (by /u/youruser)` |
