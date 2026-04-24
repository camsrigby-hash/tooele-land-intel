"""
scraper.py — Utah PMN Notice Scraper
Fetches planning commission & city council notices from utah.gov/pmn
Downloads associated PDFs for AI analysis.
"""

import os
import time
import logging
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from pathlib import Path
from config import PMN_BODIES, LOOKBACK_MONTHS, PDF_DIR, LOG_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/scraper.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

BASE_URL   = "https://www.utah.gov/pmn"
HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible; GrowthSignalBot/1.0)"}
RATE_LIMIT = 1.5  # seconds between requests

def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch a URL and return parsed HTML."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        time.sleep(RATE_LIMIT)
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return None

def get_notices_for_body(body_id: str, body_meta: dict) -> list[dict]:
    """
    Scrape all recent notices for a given PMN public body.
    Returns list of notice dicts with metadata + PDF URLs.
    """
    url  = f"{BASE_URL}/sitemap/publicbody/{body_id}.html"
    soup = fetch_page(url)
    if not soup:
        return []

    cutoff = datetime.now() - timedelta(days=30 * LOOKBACK_MONTHS)
    notices = []

    # Find the notices table
    table = soup.find("table")
    if not table:
        log.warning(f"No notice table found for body {body_id} ({body_meta['city']})")
        return []

    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Title + notice link
        title_cell = cells[0]
        notice_link = title_cell.find("a")
        if not notice_link:
            continue
        title      = notice_link.get_text(strip=True)
        notice_url = "https://www.utah.gov" + notice_link["href"] if notice_link["href"].startswith("/pmn/") else (BASE_URL + notice_link["href"] if notice_link["href"].startswith("/") else notice_link["href"])

        # Date
        date_text = cells[1].get_text(strip=True)
        try:
            event_date = datetime.strptime(date_text[:10], "%Y/%m/%d")
        except ValueError:
            continue

        if event_date < cutoff:
            continue  # too old

        # PDF attachments
        pdfs = []
        attach_cell = cells[2] if len(cells) > 2 else None
        if attach_cell:
            for link in attach_cell.find_all("a"):
                href = link.get("href", "")
                if href.endswith(".pdf"):
                    # Avoid doubling the /pmn prefix
                    if href.startswith("/pmn/"):
                        pdf_url = "https://www.utah.gov" + href
                    elif href.startswith("/"):
                        pdf_url = BASE_URL + href
                    else:
                        pdf_url = href
                    pdf_name = link.get_text(strip=True)
                    pdfs.append({"name": pdf_name, "url": pdf_url})

        notices.append({
            "body_id":    body_id,
            "city":       body_meta["city"],
            "county":     body_meta["county"],
            "body_type":  body_meta["body_type"],
            "title":      title,
            "event_date": event_date.strftime("%Y-%m-%d"),
            "notice_url": notice_url,
            "pdfs":       pdfs,
        })

    log.info(f"  {body_meta['city']} {body_meta['body_type']}: {len(notices)} notices found")
    return notices

def download_pdf(pdf_url: str, city: str, filename: str) -> str | None:
    """Download a PDF and save to disk. Returns local path or None."""
    # Sanitize filename
    safe_city = city.replace(" ", "_")
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    safe_name = safe_name[:80]  # truncate
    local_dir  = Path(PDF_DIR) / safe_city
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / f"{safe_name}.pdf"

    if local_path.exists():
        log.debug(f"  Skipping (cached): {local_path}")
        return str(local_path)

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        time.sleep(RATE_LIMIT)
        log.info(f"  Downloaded: {local_path}")
        return str(local_path)
    except Exception as e:
        log.warning(f"  PDF download failed ({pdf_url}): {e}")
        return None

def scrape_all_bodies(download_pdfs: bool = True) -> list[dict]:
    """
    Main entry point. Scrapes all configured PMN bodies.
    Returns flat list of all notices with local PDF paths.
    """
    log.info(f"Starting PMN scrape — {len(PMN_BODIES)} bodies, {LOOKBACK_MONTHS} month lookback")
    all_notices = []

    for body_id, meta in PMN_BODIES.items():
        log.info(f"Scraping: {meta['city']} — {meta['body_type']} (ID: {body_id})")
        notices = get_notices_for_body(body_id, meta)

        if download_pdfs:
            for notice in notices:
                local_pdfs = []
                for pdf in notice["pdfs"]:
                    local_path = download_pdf(pdf["url"], meta["city"], pdf["name"])
                    if local_path:
                        local_pdfs.append({
                            "name":       pdf["name"],
                            "url":        pdf["url"],
                            "local_path": local_path,
                        })
                notice["pdfs"] = local_pdfs

        all_notices.extend(notices)

    log.info(f"Scrape complete. Total notices: {len(all_notices)}")
    return all_notices


if __name__ == "__main__":
    notices = scrape_all_bodies(download_pdfs=True)
    log.info(f"\nSummary: {len(notices)} total notices across {len(PMN_BODIES)} bodies")
