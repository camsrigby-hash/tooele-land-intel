#!/usr/bin/env python3
"""Scrape local news RSS feeds for Tooele Valley land-development signals.

Sources: Tooele Transcript, Deseret News, KSL, Salt Lake Tribune, plus UDOT
news. Any feed that 404s or times out is silently skipped.

Outputs: data/signals_news.csv

Usage:
    python scripts/scrape_news_rss.py
    python scripts/scrape_news_rss.py --days 60
"""
import argparse
import csv
import hashlib
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser

ROOT = Path(__file__).parent.parent
OUT_CSV = ROOT / "data" / "signals_news.csv"

# Feeds that commonly cover Tooele Valley / Wasatch Front development.
# Any feed that returns a non-200 or empty result is skipped gracefully.
RSS_FEEDS = [
    ("tooele_transcript",  "https://www.tooeletranscript.com/feed/"),
    ("deseret_news",       "https://www.deseret.com/arc/outboundfeeds/rss/category/utah/?outputType=xml"),
    ("ksl_news",           "https://www.ksl.com/rss/news"),
    ("ksl_utah",           "https://www.ksl.com/rss/utah"),
    ("salt_lake_tribune",  "https://www.sltrib.com/feed/"),
    ("udot_news",          "https://www.udot.utah.gov/connect/news/rss-feed/"),
]

# Keywords to filter entries — at least one must appear in title or summary.
KEYWORDS = [
    "rezone", "rezoning", "annexation", "subdivision", "plat",
    "Erda", "Grantsville", "Tooele", "Stansbury", "Lake Point",
    "general plan", "zoning", "planning commission", "city council",
    "warehouse", "distribution", "commercial", "developer",
    "Costco", "Walmart", "Amazon",
    "SR-138", "SR-36", "highway", "infrastructure",
]

# Columns in output CSV
FIELDNAMES = [
    "id", "source", "feed_name", "title", "url", "published_date",
    "summary", "matched_keywords", "scraped_at",
]


def _keyword_hit(text: str) -> list[str]:
    """Return list of keywords found (case-insensitive) in text."""
    lower = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower]


def _entry_id(url: str, title: str) -> str:
    """Stable ID: SHA-1 of url + title, truncated to 12 hex chars."""
    return hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:12]


def _parse_date(entry) -> datetime | None:
    """Best-effort parse of feedparser entry's published date."""
    ts = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if ts:
        return datetime(*ts[:6], tzinfo=timezone.utc)
    return None


def scrape_feeds(cutoff: datetime) -> list[dict]:
    rows: list[dict] = []
    scraped_at = datetime.now(timezone.utc).isoformat()

    for feed_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                # Feed failed to parse and has no entries — skip
                print(f"  skip {feed_name}: parse error or empty feed", file=sys.stderr)
                continue
        except Exception as exc:
            print(f"  skip {feed_name}: {exc}", file=sys.stderr)
            continue

        count = 0
        for entry in feed.entries:
            pub = _parse_date(entry)
            if pub and pub < cutoff:
                continue

            title   = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            url     = getattr(entry, "link", "") or ""

            hits = _keyword_hit(title + " " + summary)
            if not hits:
                continue

            rows.append({
                "id":               _entry_id(url, title),
                "source":           "News",
                "feed_name":        feed_name,
                "title":            title.strip(),
                "url":              url,
                "published_date":   pub.date().isoformat() if pub else "",
                "summary":          summary[:500].strip(),
                "matched_keywords": "|".join(hits),
                "scraped_at":       scraped_at,
            })
            count += 1

        print(f"  {feed_name}: {count} matching entries", file=sys.stderr)
        time.sleep(0.5)  # polite delay between feeds

    return rows


def main(days: int = 30) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"Scraping news RSS feeds (last {days} days)...", file=sys.stderr)

    rows = scrape_feeds(cutoff)
    print(f"Total news signals: {len(rows)}", file=sys.stderr)

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
