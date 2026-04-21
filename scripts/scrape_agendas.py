#!/usr/bin/env python3
"""
Scrape planning commission and city council agendas from:
  - Tooele County
  - Grantsville City
  - Erda City

Outputs JSON list of agenda items with PDFs extracted.

Usage:
    python scripts/scrape_agendas.py
    python scripts/scrape_agendas.py --jurisdiction grantsville
    python scripts/scrape_agendas.py --output data/agendas.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from classify import classify_agenda_item
from pdf_extract import extract_pdf_text

ROOT = Path(__file__).parent.parent
CFG_PATH = ROOT / "data" / "jurisdictions.yaml"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TooeleLandIntel/1.0 (public records research)"
})
TIMEOUT = 30


def load_cfg() -> dict:
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


def find_pdf_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf") or "pdf" in href.lower():
            links.append(urljoin(base_url, href))
    return links


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def parse_agenda_items(soup: BeautifulSoup, pdf_urls: list[str], source_url: str, jurisdiction: str) -> list[dict]:
    items = []

    # Try to extract structured agenda items — look for numbered lists or headings
    for tag in soup.find_all(["li", "p", "h3", "h4"]):
        text = tag.get_text(separator=" ", strip=True)
        if len(text) < 20 or len(text) > 1000:
            continue
        # Skip navigation / boilerplate
        if any(kw in text.lower() for kw in ["click here", "home", "contact us", "login", "search"]):
            continue

        classification = classify_agenda_item(text)
        if classification["type"] != "other":
            items.append({
                "text": text,
                "classification": classification,
                "source_url": source_url,
                "jurisdiction": jurisdiction,
                "scraped_at": datetime.utcnow().isoformat() + "Z",
            })

    # Also create one item per PDF found
    for pdf_url in pdf_urls:
        pdf_text = extract_pdf_text(pdf_url)
        classification = classify_agenda_item(pdf_text[:500] if pdf_text else "")
        items.append({
            "type": "pdf_exhibit",
            "pdf_url": pdf_url,
            "pdf_text_excerpt": pdf_text[:500] if pdf_text else None,
            "classification": classification,
            "source_url": source_url,
            "jurisdiction": jurisdiction,
            "scraped_at": datetime.utcnow().isoformat() + "Z",
        })

    return items


def scrape_jurisdiction(name: str, jcfg: dict) -> list[dict]:
    print(f"Scraping {jcfg['name']}...", file=sys.stderr)
    all_items = []

    for url_key in ("agenda_url", "planning_url", "council_url"):
        url = jcfg.get(url_key)
        if not url:
            continue
        print(f"  Fetching {url}", file=sys.stderr)
        soup = fetch_page(url)
        if not soup:
            continue
        pdf_links = find_pdf_links(soup, url)
        print(f"  Found {len(pdf_links)} PDF(s)", file=sys.stderr)
        items = parse_agenda_items(soup, pdf_links, url, jcfg["name"])
        all_items.extend(items)

    return all_items


def main():
    parser = argparse.ArgumentParser(description="Scrape Tooele area planning agendas.")
    parser.add_argument("--jurisdiction", choices=["tooele_county", "grantsville", "erda"],
                        help="Scrape only this jurisdiction (default: all)")
    parser.add_argument("--output", default="-", help="Output JSON file path (default: stdout)")
    args = parser.parse_args()

    cfg = load_cfg()
    jurisdictions = cfg["jurisdictions"]

    if args.jurisdiction:
        jurisdictions = {args.jurisdiction: jurisdictions[args.jurisdiction]}

    results = []
    for jname, jcfg in jurisdictions.items():
        items = scrape_jurisdiction(jname, jcfg)
        results.extend(items)
        print(f"  -> {len(items)} item(s) from {jcfg['name']}", file=sys.stderr)

    output = json.dumps(results, indent=2)
    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output)
        print(f"Wrote {len(results)} items to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
