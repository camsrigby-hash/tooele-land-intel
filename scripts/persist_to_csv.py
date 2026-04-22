#!/usr/bin/env python3
"""
Merge scraper JSON outputs into a single deduplicated CSV.

Input:  data/agendas/*.json (from scrape_agendas.py)
        data/agendas/pmn_*.json (from scrape_utah_pmn.py)
Output: data/agenda_items.csv

Dedup key: (jurisdiction, source_url_or_pdf_url, meeting_date)
"""
import csv
import json
import re
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
AGENDA_DIR = ROOT / "data" / "agendas"
CSV_PATH = ROOT / "data" / "agenda_items.csv"

# Match MM.DD.YYYY or MM-DD-YYYY in URLs (Grantsville pattern)
DATE_IN_URL = re.compile(r'(\d{1,2})[.\-](\d{1,2})[.\-](20\d{2})')

CSV_FIELDS = [
    "id", "jurisdiction", "body", "meeting_date", "title",
    "item_type", "confidence", "url", "agenda_text", "source",
    "scraped_at",
]


def extract_meeting_date(url: str, text: str = "") -> str:
    """Try URL pattern first, then PDF text. Returns ISO date or empty."""
    if url:
        m = DATE_IN_URL.search(url)
        if m:
            mm, dd, yy = m.groups()
            try:
                return datetime(int(yy), int(mm), int(dd)).date().isoformat()
            except ValueError:
                pass
    # Try natural-language date in text
    if text:
        m = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})', text)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
            except ValueError:
                pass
    return ""


def normalize_pmn_record(notice: dict, body_name: str, jurisdiction: str) -> dict:
    """Convert PMN notice dict to our CSV row shape."""
    meeting_date = ""
    if notice.get("event_date_iso"):
        meeting_date = notice["event_date_iso"][:10]
    return {
        "id": f"pmn_{notice.get('notice_id', '')}",
        "jurisdiction": jurisdiction,
        "body": body_name,
        "meeting_date": meeting_date,
        "title": notice.get("title", ""),
        "item_type": "",  # PMN notices not classified yet
        "confidence": "",
        "url": notice.get("notice_url", ""),
        "agenda_text": (notice.get("agenda_text") or "")[:2000],
        "source": "pmn",
        "scraped_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_agenda_pdf(item: dict) -> dict:
    """Convert scrape_agendas.py PDF record to our CSV row shape."""
    pdf_url = item.get("pdf_url", "")
    text = item.get("pdf_text_excerpt", "") or ""
    classification = item.get("classification", {}) or {}
    return {
        "id": f"web_{abs(hash(pdf_url))}",
        "jurisdiction": item.get("jurisdiction", ""),
        "body": "",  # not always parseable from web scraper
        "meeting_date": extract_meeting_date(pdf_url, text),
        "title": item.get("link_text", ""),
        "item_type": classification.get("type", ""),
        "confidence": classification.get("confidence", ""),
        "url": pdf_url,
        "agenda_text": text[:2000],
        "source": "web",
        "scraped_at": item.get("scraped_at", datetime.utcnow().isoformat() + "Z"),
    }


def collect_rows() -> list[dict]:
    rows = []
    if not AGENDA_DIR.exists():
        print(f"  [WARN] {AGENDA_DIR} doesn't exist", file=sys.stderr)
        return rows

    for path in sorted(AGENDA_DIR.glob("*.json")):
        try:
            data = json.load(open(path))
        except Exception as e:
            print(f"  [WARN] couldn't read {path.name}: {e}", file=sys.stderr)
            continue

        # PMN format: {public_body, jurisdiction, notices: [...]}
        if isinstance(data, dict) and "notices" in data:
            body = data.get("public_body", "")
            juris = data.get("jurisdiction", "")
            for notice in data["notices"]:
                rows.append(normalize_pmn_record(notice, body, juris))

        # Web scraper format: list of {type, jurisdiction, ...}
        elif isinstance(data, list):
            for item in data:
                if item.get("type") == "agenda_pdf":
                    rows.append(normalize_agenda_pdf(item))
                # skip "notice" type (just messages about why a juris failed)

        print(f"  Loaded {path.name}: {len(rows)} cumulative rows", file=sys.stderr)
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    seen = {}
    for r in rows:
        key = (r["jurisdiction"], r["url"], r["meeting_date"])
        # Newer scrape wins
        if key not in seen or r["scraped_at"] > seen[key]["scraped_at"]:
            seen[key] = r
    return list(seen.values())


def main():
    rows = dedupe(collect_rows())
    rows.sort(key=lambda r: (r["jurisdiction"], r["meeting_date"] or "0000", r["title"]))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"✓ Wrote {len(rows)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
