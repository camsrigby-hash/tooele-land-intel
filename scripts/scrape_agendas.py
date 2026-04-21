#!/usr/bin/env python3
"""
Scrape planning commission and city council agendas from:
  - Tooele County  (tooeleco.org — Tyler Meeting Manager SPA; we extract
                    what we can from the landing pages)
  - Grantsville City  (grantsvilleut.gov — direct PDF table)
  - Erda City         (no agenda pages as of 2026-04)

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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

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
TIMEOUT = 20

# Keywords that indicate a link is an actual agenda or minutes document
_AGENDA_KEYWORDS = re.compile(
    r'\b(agenda|minutes|packet|notice|hearing|resolution|ordinance)\b', re.I
)
# Keywords that indicate a sidebar/nav PDF — skip these
_NAV_KEYWORDS = re.compile(
    r'\b(county code|fee schedule|privacy policy|rent roll|pid policy'
    r'|employment|visitor|contact|home|login)\b', re.I
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cfg() -> dict:
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def is_agenda_link(text: str, href: str) -> bool:
    combined = f"{text} {href}"
    return bool(_AGENDA_KEYWORDS.search(combined)) and not _NAV_KEYWORDS.search(combined)


def safe_urljoin(base: str, href: str) -> str:
    """Join base + href, encoding any bare spaces in the path portion."""
    full = urljoin(base, href)
    # Encode spaces that slipped through (common in CivicPlus/CMS document hrefs)
    parsed = urlparse(full)
    safe_path = quote(parsed.path, safe="/:@!$&'()*+,;=%")
    return parsed._replace(path=safe_path).geturl()


# Match a 4-digit year in an href to filter for recent documents
_YEAR_RE = re.compile(r'/(202[3-9]|203\d)/')


def find_agenda_pdf_links(soup: BeautifulSoup, base_url: str, recent_only: bool = True) -> list[dict]:
    """Return [{url, link_text}] for links that look like actual agenda PDFs.

    recent_only: when True, only return links whose href contains a year >= 2023.
    This avoids fetching hundreds of archived PDFs.
    """
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(separator=" ", strip=True)
        if not href:
            continue
        if recent_only and not _YEAR_RE.search(href):
            continue
        full_url = safe_urljoin(base_url, href)
        if is_agenda_link(text, href):
            results.append({"url": full_url, "link_text": text})
    return results


def make_item(pdf_url: str, link_text: str, source_url: str, jurisdiction: str) -> dict:
    pdf_text = extract_pdf_text(pdf_url)
    snippet = (pdf_text or "")[:500]
    classification = classify_agenda_item(link_text + " " + snippet)
    return {
        "type": "agenda_pdf",
        "pdf_url": pdf_url,
        "link_text": link_text,
        "pdf_text_excerpt": snippet or None,
        "classification": classification,
        "source_url": source_url,
        "jurisdiction": jurisdiction,
        "scraped_at": now_iso(),
    }


# ── Jurisdiction-specific scrapers ───────────────────────────────────────────

def scrape_tooele_county(jcfg: dict) -> list[dict]:
    """
    Tooele County uses Tyler Meeting Manager (JS SPA) for agendas.
    We scrape the landing pages and filter to agenda-keyword PDF links only.
    """
    items = []
    for url_key in ("agenda_url", "council_url"):
        url = jcfg.get(url_key)
        if not url:
            continue
        print(f"  Fetching {url}", file=sys.stderr)
        soup = fetch_page(url)
        if not soup:
            continue
        links = find_agenda_pdf_links(soup, url)
        print(f"  Found {len(links)} agenda-keyword link(s)", file=sys.stderr)
        for link in links:
            items.append(make_item(link["url"], link["link_text"], url, jcfg["name"]))

    if not items:
        # Tyler Meeting Manager SPA — agendas require JavaScript rendering.
        items.append({
            "type": "notice",
            "message": (
                "Tooele County agendas are served by Tyler Meeting Manager "
                "(JavaScript SPA). Full scraping requires a headless browser. "
                "View directly at: "
                "https://tooelecountyut.meetingmanager.tylerapp.com/"
                "404401tooelecountyut/tylermm/calendar"
            ),
            "jurisdiction": jcfg["name"],
            "scraped_at": now_iso(),
        })
    return items


def scrape_grantsvilleut(jcfg: dict, max_pdfs: int = 20) -> list[dict]:
    """
    Grantsville publishes PDFs in a two-column date/link table on grantsvilleut.gov.
    Each row: col 1 = date + meeting type, col 2 = Agenda / Packet / Minutes links.

    The site (Revize CMS) uses root-relative hrefs like 'Document_Center/...' from
    pages in subdirectories — we resolve against the domain root, not the page URL.
    Links are listed newest-first; max_pdfs caps how many we fetch.
    """
    items = []
    for url_key in ("agenda_url", "planning_url"):
        url = jcfg.get(url_key)
        if not url:
            continue
        print(f"  Fetching {url}", file=sys.stderr)
        soup = fetch_page(url)
        if not soup:
            continue

        # Revize CMS: hrefs like 'Document_Center/...' are root-relative
        parsed = urlparse(url)
        link_base = f"{parsed.scheme}://{parsed.netloc}/"
        links = find_agenda_pdf_links(soup, link_base)
        # Links are listed newest-first; cap to avoid fetching years of archives
        links = links[:max_pdfs]
        print(f"  Processing {len(links)} recent agenda link(s)", file=sys.stderr)
        for link in links:
            # Only fetch PDF text for actual .pdf hrefs
            if link["url"].lower().endswith(".pdf") or ".pdf?" in link["url"].lower():
                items.append(make_item(link["url"], link["link_text"], url, jcfg["name"]))
            else:
                items.append({
                    "type": "agenda_link",
                    "url": link["url"],
                    "link_text": link["link_text"],
                    "classification": classify_agenda_item(link["link_text"]),
                    "source_url": url,
                    "jurisdiction": jcfg["name"],
                    "scraped_at": now_iso(),
                })
    return items


def scrape_unavailable(jcfg: dict) -> list[dict]:
    print(f"  No agenda URLs configured for {jcfg['name']}", file=sys.stderr)
    return [{
        "type": "notice",
        "message": f"{jcfg['name']} does not yet have public agenda pages (as of 2026-04).",
        "jurisdiction": jcfg["name"],
        "scraped_at": now_iso(),
    }]


_SCRAPERS = {
    "tooele_county": scrape_tooele_county,
    "grantsvilleut": scrape_grantsvilleut,
    "unavailable": scrape_unavailable,
}


def scrape_jurisdiction(name: str, jcfg: dict, max_pdfs: int = 20) -> list[dict]:
    print(f"Scraping {jcfg['name']}...", file=sys.stderr)
    scraper_type = jcfg.get("scraper_type", "tooele_county")
    scraper = _SCRAPERS.get(scraper_type, scrape_tooele_county)
    if scraper_type == "grantsvilleut":
        items = scraper(jcfg, max_pdfs=max_pdfs)
    else:
        items = scraper(jcfg)
    print(f"  -> {len(items)} item(s) from {jcfg['name']}", file=sys.stderr)
    return items


def main():
    parser = argparse.ArgumentParser(description="Scrape Tooele area planning agendas.")
    parser.add_argument("--jurisdiction", choices=["tooele_county", "grantsville", "erda"],
                        help="Scrape only this jurisdiction (default: all)")
    parser.add_argument("--max-pdfs", type=int, default=20,
                        help="Max PDFs to fetch per page (default: 20, newest first)")
    parser.add_argument("--output", default="-", help="Output JSON file path (default: stdout)")
    args = parser.parse_args()

    cfg = load_cfg()
    jurisdictions = cfg["jurisdictions"]

    if args.jurisdiction:
        jurisdictions = {args.jurisdiction: jurisdictions[args.jurisdiction]}

    results = []
    for jname, jcfg in jurisdictions.items():
        items = scrape_jurisdiction(jname, jcfg, max_pdfs=args.max_pdfs)
        results.extend(items)

    output = json.dumps(results, indent=2)
    if args.output == "-":
        print(output)
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output)
        print(f"Wrote {len(results)} items to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
