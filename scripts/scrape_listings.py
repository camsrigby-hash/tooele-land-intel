#!/usr/bin/env python3
"""
scrape_listings.py — Phase 15a CRE land listing ingestion.

Pulls active for-sale land listings from CREXI and Land.com for the Wasatch
Front + Tooele Valley counties and writes CSVs into data/raw using the Phase 15a
schema. The scraper is intentionally low-volume, source-isolated, and resilient:
source failures are logged and still produce an empty CSV with headers so the
weekly GitHub Actions run can continue.

LoopNet is intentionally excluded.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("scrape_listings")

COUNTIES = ["Tooele", "Salt Lake", "Utah", "Davis", "Weber", "Wasatch", "Box Elder"]
COUNTY_TOKENS = {c.lower() for c in COUNTIES} | {c.lower().replace(" ", "-") for c in COUNTIES}
UTAH_CITY_HINTS = {
    "tooele", "erda", "grantsville", "lake point", "stansbury", "salt lake", "sandy",
    "west valley", "south jordan", "herriman", "lehi", "provo", "orem", "spanish fork",
    "saratoga springs", "american fork", "layton", "farmington", "clearfield", "ogden",
    "roy", "pleasant view", "heber", "midway", "park city", "brigham", "tremonton",
}
LISTING_COLUMNS = [
    "address", "list_price", "price_per_acre", "acres", "zoning_class",
    "listing_status", "listing_date", "source", "link", "scraped_at",
]
REQUEST_DELAY_SECONDS = 1.5
TIMEOUT_SECONDS = 30
USER_AGENT = (
    "tooele-land-intel/phase15a (+https://github.com/camsrigby-hash/tooele-land-intel; "
    "weekly low-volume public listing scraper)"
)

CREXI_URLS = [
    "https://www.crexi.com/properties?types%5B%5D=Land&locations%5B%5D=Utah",
    *[
        "https://www.crexi.com/properties?types%5B%5D=Land&locations%5B%5D="
        + c.replace(" ", "%20") + "%20County%2C%20UT"
        for c in COUNTIES
    ],
]
LANDCOM_URLS = [
    "https://www.land.com/Utah/all-land/",
    *["https://www.land.com/" + c.replace(" ", "-") + "-County-UT/all-land/" for c in COUNTIES],
]


@dataclass(frozen=True)
class Listing:
    address: str | None
    list_price: int | None
    price_per_acre: float | None
    acres: float | None
    zoning_class: str | None
    listing_status: str
    listing_date: str | None
    source: str
    link: str | None
    scraped_at: str

    def as_row(self) -> dict[str, Any]:
        return {col: getattr(self, col) for col in LISTING_COLUMNS}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return s


def fetch(session: requests.Session, url: str) -> str:
    LOG.info("Fetching %s", url)
    time.sleep(REQUEST_DELAY_SECONDS)
    r = session.get(url, timeout=TIMEOUT_SECONDS)
    r.raise_for_status()
    return r.text


def parse_money(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(round(value))
    text = str(value).strip()
    if not text or text.lower() in {"contact", "call", "auction", "undisclosed"}:
        return None
    mult = 1
    if re.search(r"\b(k|thousand)\b", text, re.I):
        mult = 1_000
    if re.search(r"\b(m|mm|million)\b", text, re.I):
        mult = 1_000_000
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return None
    return int(round(float(nums[0]) * mult))


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    nums = re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(nums[0]) if nums else None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,-")
    return text or None


def normalize_date(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text[:20], fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def is_scope_text(text: str) -> bool:
    t = text.lower()
    return " ut" in t or ", ut" in t or "utah" in t or any(tok in t for tok in COUNTY_TOKENS | UTAH_CITY_HINTS)


def is_land_text(text: str) -> bool:
    t = text.lower()
    excluded = ("single family", "home", "apartment", "office building", "retail building", "warehouse")
    return ("land" in t or "acre" in t or "lot" in t) and not any(x in t for x in excluded)


def extract_json_objects(soup: BeautifulSoup) -> list[Any]:
    objs: list[Any] = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        candidates: list[str] = []
        if script.get("type") == "application/ld+json":
            candidates.append(raw)
        m = re.search(r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", str(script), re.S)
        if m:
            candidates.append(m.group(1))
        if any(tok in raw.lower() for tok in ("acre", "listprice", "askingprice", "price")):
            # Pull assignment-style JSON blobs conservatively.
            candidates.append(raw)
        for candidate in candidates:
            candidate = candidate.strip().rstrip(";")
            try:
                objs.append(json.loads(candidate))
                continue
            except Exception:
                pass
            # Some sites embed JSON after an assignment. Try the largest object span.
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                try:
                    objs.append(json.loads(candidate[start : end + 1]))
                except Exception:
                    pass
    return objs


def walk(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item)


def pick(d: dict[str, Any], names: Iterable[str]) -> Any:
    lower = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def flatten_address(value: Any) -> str | None:
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
        return clean_text(", ".join(str(p) for p in parts if p))
    return clean_text(value)


def listing_from_dict(d: dict[str, Any], source: str, base_url: str, scraped_at: str) -> Listing | None:
    blob = json.dumps(d, ensure_ascii=False, default=str)
    if not (is_land_text(blob) and is_scope_text(blob)):
        return None
    price = parse_money(pick(d, ["listPrice", "askingPrice", "price", "amount", "priceValue"]))
    acres = parse_float(pick(d, ["acres", "acreage", "lotSize", "lotSizeAcres", "landArea", "size"]))
    if acres is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+/-\s*)?(?:acres?|ac\b)", blob, re.I)
        acres = float(m.group(1)) if m else None
    if acres is None or acres < 0.5 or acres > 500:
        return None
    link = pick(d, ["url", "href", "link", "canonicalUrl", "propertyUrl"])
    if isinstance(link, dict):
        link = pick(link, ["url", "href"])
    link = urljoin(base_url, str(link)) if link else base_url
    address = flatten_address(pick(d, ["address", "displayAddress", "location", "name", "title"]))
    ppa = parse_float(pick(d, ["pricePerAcre", "price_per_acre", "pricePerSqft"]))
    if ppa is None and price and acres:
        ppa = round(price / acres, 2)
    status_raw = clean_text(pick(d, ["status", "listingStatus", "availability"])) or "active"
    status = "pending" if "pending" in status_raw.lower() else "active"
    listing_date = normalize_date(pick(d, ["listingDate", "datePosted", "createdAt", "publishedAt"]))
    zoning = clean_text(pick(d, ["zoning", "zoningClass", "zoning_class"]))
    return Listing(address, price, ppa, acres, zoning, status, listing_date, source, link, scraped_at)


def parse_cards(soup: BeautifulSoup, source: str, base_url: str, scraped_at: str) -> list[Listing]:
    rows: list[Listing] = []
    selectors = ["article", "[class*=card]", "[class*=Card]", "[data-testid*=card]", "[data-testid*=listing]"]
    seen_blocks: set[str] = set()
    for sel in selectors:
        for node in soup.select(sel):
            text = clean_text(node.get_text(" ")) or ""
            if len(text) < 30 or text in seen_blocks or not (is_land_text(text) and is_scope_text(text)):
                continue
            seen_blocks.add(text)
            acres = None
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+/-\s*)?(?:acres?|ac\b)", text, re.I)
            if m:
                acres = float(m.group(1))
            if acres is None or acres < 0.5 or acres > 500:
                continue
            price = parse_money(text)
            link_node = node.find("a", href=True)
            link = urljoin(base_url, link_node["href"]) if link_node else base_url
            title = clean_text(link_node.get_text(" ") if link_node else text[:100])
            ppa = round(price / acres, 2) if price and acres else None
            rows.append(Listing(title, price, ppa, acres, None, "active", None, source, link, scraped_at))
    return rows


def scrape_source(source: str, urls: list[str], max_pages: int) -> list[Listing]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session = make_session()
    rows: list[Listing] = []
    errors: list[str] = []
    for url in urls[:max_pages]:
        try:
            text = fetch(session, url)
            soup = BeautifulSoup(text, "lxml")
            for obj in extract_json_objects(soup):
                for d in walk(obj):
                    listing = listing_from_dict(d, source, url, scraped_at)
                    if listing:
                        rows.append(listing)
            rows.extend(parse_cards(soup, source, url, scraped_at))
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            LOG.warning("%s failed for %s: %s", source, url, exc)
    if errors:
        LOG.info("%s completed with %d fetch/parse errors", source, len(errors))
    return dedupe(rows)


def dedupe(rows: list[Listing]) -> list[Listing]:
    seen: set[str] = set()
    out: list[Listing] = []
    for row in rows:
        key = (row.link or "") + "|" + (row.address or "") + "|" + str(row.acres or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def write_csv(path: Path, rows: list[Listing]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LISTING_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row.as_row())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape Phase 15a active land listings.")
    ap.add_argument("--out-dir", default="data/raw", help="Output directory, default data/raw")
    ap.add_argument("--date", default=date.today().isoformat(), help="Output date stamp YYYY-MM-DD")
    ap.add_argument("--max-pages", type=int, default=8, help="Maximum seed URLs per source")
    ap.add_argument("--source", choices=["all", "crexi", "landcom"], default="all")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    out_dir = Path(args.out_dir)
    summary: dict[str, int] = {}

    if args.source in {"all", "crexi"}:
        try:
            rows = scrape_source("crexi", CREXI_URLS, args.max_pages)
        except Exception as exc:
            LOG.exception("CREXI source failed completely: %s", exc)
            rows = []
        write_csv(out_dir / f"listings_crexi_{args.date}.csv", rows)
        summary["crexi"] = len(rows)

    if args.source in {"all", "landcom"}:
        try:
            rows = scrape_source("landcom", LANDCOM_URLS, args.max_pages)
        except Exception as exc:
            LOG.exception("Land.com source failed completely: %s", exc)
            rows = []
        write_csv(out_dir / f"listings_landcom_{args.date}.csv", rows)
        summary["landcom"] = len(rows)

    for source, count in summary.items():
        LOG.info("SUMMARY source=%s row_count=%s errors=see-log", source, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
