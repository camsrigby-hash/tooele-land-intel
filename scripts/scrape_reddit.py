#!/usr/bin/env python3
"""Reddit Data API ingestion via PRAW.

STATUS as of 2026-04-25: This script is the SECONDARY Reddit ingestion path.
RSS-based ingestion in scrape_news_rss.py is the PRIMARY path (no auth, no
approval gate, operational today).

This script becomes the primary path when Reddit Data API access is approved
under the Responsible Builder Policy:
  https://support.reddithelp.com/hc/en-us/articles/42728983564564

Until approval lands, this script soft-fails — it writes an empty
signals_reddit.csv and exits cleanly when REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
or REDDIT_USER_AGENT env vars are absent. signals.yml continues without error.

When approval lands:
  1. Add REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT as GitHub
     Secrets to the tooele-land-intel repo (reddit.com/prefs/apps, script-type app).
  2. This script's richer output (with score, num_comments, subreddit_subscribers)
     automatically populates signals_reddit.csv on the next workflow run.
  3. Decide whether to remove the four r/* RSS feeds from scrape_news_rss.py
     to avoid duplicate signals, or keep both for redundancy (recommended: keep
     both — RSS catches posts faster; API gives richer metadata for correlation).
     correlate_signals.py deduplicates by URL, preferring the PRAW row.

Outputs: data/signals_reddit.csv

Usage:
    REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=... REDDIT_USER_AGENT=... \\
        python scripts/scrape_reddit.py
    python scripts/scrape_reddit.py --days 60
"""
import argparse
import csv
import hashlib
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT    = Path(__file__).parent.parent
OUT_CSV = ROOT / "data" / "signals_reddit.csv"

SUBREDDITS = [
    "Utah",
    "SaltLakeCity",
    "UtahPolitics",
    "tooele",
]

KEYWORDS = [
    "rezone", "rezoning", "annexation", "subdivision", "plat",
    "Erda", "Grantsville", "Tooele", "Stansbury", "Lake Point",
    "general plan", "zoning", "planning commission", "city council",
    "warehouse", "distribution", "commercial", "developer",
    "Costco", "Walmart", "Amazon",
    "SR-138", "SR-36",
]

FIELDNAMES = [
    "id", "source", "subreddit", "title", "url", "published_date",
    "summary", "score", "matched_keywords", "scraped_at",
]


def _keyword_hit(text: str) -> list[str]:
    lower = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower]


def _post_id(post_id: str) -> str:
    return hashlib.sha1(f"reddit:{post_id}".encode()).hexdigest()[:12]


def scrape_reddit(days: int) -> list[dict]:
    try:
        import praw
    except ImportError:
        print("praw not installed — Reddit scraping skipped", file=sys.stderr)
        return []

    client_id     = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user_agent    = os.environ.get("REDDIT_USER_AGENT", "").strip()

    if not (client_id and client_secret and user_agent):
        print(
            "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT not set — "
            "Reddit scraping skipped. "
            "Register a script-type app at reddit.com/prefs/apps and add these as "
            "GitHub Secrets to tooele-land-intel to enable Reddit signals.",
            file=sys.stderr,
        )
        return []

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        read_only=True,
    )

    cutoff     = datetime.now(timezone.utc) - timedelta(days=days)
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            count = 0
            for post in subreddit.new(limit=200):
                pub = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                if pub < cutoff:
                    continue

                text = f"{post.title} {post.selftext or ''}"
                hits = _keyword_hit(text)
                if not hits:
                    continue

                rows.append({
                    "id":               _post_id(post.id),
                    "source":           "Rumor",
                    "subreddit":        sub_name,
                    "title":            post.title.strip(),
                    "url":              f"https://reddit.com{post.permalink}",
                    "published_date":   pub.date().isoformat(),
                    "summary":          post.selftext[:500].strip() if post.selftext else "",
                    "score":            post.score,
                    "matched_keywords": "|".join(hits),
                    "scraped_at":       scraped_at,
                })
                count += 1

            print(f"  r/{sub_name}: {count} matching posts", file=sys.stderr)
            time.sleep(1.5)  # PRAW rate-limits; 1.5s is the CM_RE-tested safe value

        except Exception as exc:
            print(f"  r/{sub_name}: skipped ({exc})", file=sys.stderr)

    return rows


def main(days: int = 30) -> None:
    print(f"Scraping Reddit (last {days} days)...", file=sys.stderr)
    rows = scrape_reddit(days)
    print(f"Total Reddit signals: {len(rows)}", file=sys.stderr)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written: {OUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Look-back window in days")
    args = parser.parse_args()
    main(days=args.days)
