"""
Agendas Watch — entry point.

Walks each jurisdiction's agendas page, finds linked PDFs, downloads any
not previously seen, extracts text, classifies items, and appends rows to
data/agenda_items.csv. Records seen-URLs in data/seen_pdfs.txt so we
don't reprocess.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from classify import classify, extract, load_config, split_into_items
from pdf_extract import fetch_pdf_text

DATA_DIR = Path("data")
CSV_PATH = DATA_DIR / "agenda_items.csv"
SEEN_PATH = DATA_DIR / "seen_pdfs.txt"

CSV_COLUMNS = [
    "scraped_at",
    "jurisdiction",
    "body",
    "agenda_url",
    "pdf_url",
    "pdf_hash",
    "item_index",
    "label",
    "parcels",
    "addresses",
    "acreage",
    "density_du_per_acre",
    "snippet",
]

UA = {"User-Agent": "tooele-land-intel/1.0 (research; contact via repo issues)"}


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    return set(SEEN_PATH.read_text().splitlines())


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text("\n".join(sorted(seen)))


def find_pdf_links(page_url: str) -> list[str]:
    """Pull all .pdf hrefs from an agendas listing page."""
    try:
        r = requests.get(page_url, headers=UA, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  ! failed to fetch {page_url}: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf") or "agenda" in href.lower():
            out.append(urljoin(page_url, href))
    # dedupe, preserve order
    return list(dict.fromkeys(out))


def process_pdf(
    pdf_url: str,
    jurisdiction_key: str,
    body: str,
    agenda_url: str,
    config: dict,
    writer: csv.DictWriter,
) -> None:
    try:
        text = fetch_pdf_text(pdf_url)
    except Exception as e:
        print(f"    ! failed to extract {pdf_url}: {e}", file=sys.stderr)
        return

    pdf_hash = hashlib.sha1(pdf_url.encode()).hexdigest()[:10]
    items = split_into_items(text)
    if not items:
        # Treat the whole document as one item rather than dropping it.
        items = [text[:2000]]

    rules = config["classifier"]["rules"]
    patterns = config["extraction"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for idx, item_text in enumerate(items):
        label = classify(item_text, rules)
        ex = extract(item_text, patterns)
        # We only record items that look land-use relevant. Drop "other" and
        # "ordinance"/"public_hearing_other" with no parcel and no address.
        is_landuse = label not in ("other",) or ex["parcels"] or ex["addresses"]
        if not is_landuse:
            continue

        writer.writerow({
            "scraped_at": now,
            "jurisdiction": jurisdiction_key,
            "body": body,
            "agenda_url": agenda_url,
            "pdf_url": pdf_url,
            "pdf_hash": pdf_hash,
            "item_index": idx,
            "label": label,
            "parcels": ";".join(ex["parcels"]),
            "addresses": ";".join(ex["addresses"]),
            "acreage": ex["acreage"] if ex["acreage"] is not None else "",
            "density_du_per_acre": ex["density_du_per_acre"] if ex["density_du_per_acre"] is not None else "",
            "snippet": re.sub(r"\s+", " ", item_text)[:300],
        })


def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    config = load_config()
    seen = load_seen()

    new_pdfs = 0

    file_exists = CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()

        for key, juris in config["jurisdictions"].items():
            print(f"\n== {juris['name']} ==")
            for body_cfg in juris["bodies"]:
                page_url = body_cfg["url"]
                print(f"  {body_cfg['body']}: {page_url}")
                pdfs = find_pdf_links(page_url)
                for pdf_url in pdfs:
                    if pdf_url in seen:
                        continue
                    print(f"    + new: {pdf_url}")
                    process_pdf(pdf_url, key, body_cfg["body"], page_url, config, writer)
                    seen.add(pdf_url)
                    new_pdfs += 1

    save_seen(seen)
    print(f"\nDone. {new_pdfs} new PDFs processed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
