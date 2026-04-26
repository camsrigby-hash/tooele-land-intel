#!/usr/bin/env python3
"""
scrape_utah_pmn.py

Scraper for the Utah Public Notice Website (utah.gov/pmn), the statewide
clearinghouse for agendas, staff reports, and meeting minutes posted under
the Open and Public Meetings Act (Utah Code 52-4-103(7)).

Every Utah municipality, county, and special district posts here, which makes
this the right source for jurisdictions like Erda whose own websites (e.g.,
Squarespace) have no agenda pages.

This module is the PMN companion to scrape_agendas.py. It produces JSON
records with the same top-level shape so the existing classifier can ingest
the output without branching logic.

URL structure (verified 2026-04-20):
    Public body index:  https://www.utah.gov/pmn/sitemap/publicbody/{body_id}.html
    Notice detail page: https://www.utah.gov/pmn/sitemap/notice/{notice_id}.html
    File attachments:   https://www.utah.gov/pmn/files/{file_id}.{ext}

Usage examples:
    # Scrape a single public body (Erda Planning Commission = 7563)
    python scrape_utah_pmn.py --body-id 7563

    # Scrape all bodies declared in jurisdictions.yaml with pmn_body_id set
    python scrape_utah_pmn.py --jurisdictions jurisdictions.yaml

    # Limit to notices with event dates in the last 12 months
    python scrape_utah_pmn.py --body-id 7563 --months-back 12

    # Write output somewhere other than ./output
    python scrape_utah_pmn.py --body-id 7563 --output-dir data/pmn
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import yaml  # only needed if --jurisdictions is used
except ImportError:
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PMN_BASE = "https://www.utah.gov"
PUBLICBODY_URL = PMN_BASE + "/pmn/sitemap/publicbody/{body_id}.html"
NOTICE_URL = PMN_BASE + "/pmn/sitemap/notice/{notice_id}.html"

DEFAULT_USER_AGENT = (
    "land-dev-tool/0.1 (+https://github.com/ "
    "contact via repo; Utah PMN scraper; compliant with utah.gov ToS)"
)
DEFAULT_REQUEST_DELAY_SECONDS = 1.0  # be polite
DEFAULT_TIMEOUT_SECONDS = 30

# The PMN site calls it "Upcoming Notices" but in practice the table lists
# both upcoming and recent-past notices for the body.
NOTICES_TABLE_HEADER = "Upcoming Notices"

SOURCE_NAME = "utah_pmn"

# Known attachment categories (used for downstream routing to the PDF
# classifier vs. audio/minutes handlers). Anything else falls through as
# "other".
ATTACHMENT_CATEGORIES = {
    "Public Information Handout",
    "Meeting Minutes",
    "Audio Recording",
    "Presentation",
    "Staff Report",
    "Other",
}

logger = logging.getLogger("scrape_utah_pmn")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Attachment:
    file_name: str
    category: str
    url: str
    date_added: str | None = None  # raw string as displayed on the page
    file_id: str | None = None     # parsed from /pmn/files/{id}.ext when possible


@dataclass
class Notice:
    notice_id: str
    notice_url: str
    title: str
    event_date: str | None           # raw "March 10, 2026 07:00 PM"
    event_date_iso: str | None       # parsed ISO-8601 if parse succeeded
    notice_types: list[str] = field(default_factory=list)
    agenda_text: str = ""            # the Description/Agenda block — this is
                                     # what the classifier actually reads
    meeting_location: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    posted_on: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    canceled: bool = False           # inferred from title (***CANCELED***)


@dataclass
class ScrapeResult:
    """Top-level record written to disk. Shape mirrors scrape_agendas.py so
    the existing classifier can consume it unchanged.

    If scrape_agendas.py uses a different key name for any of these, the
    adapter at the bottom of this file (`to_classifier_dict`) is the single
    place to change."""
    source: str                  # always "utah_pmn"
    jurisdiction: str            # human-readable, e.g. "Erda"
    public_body: str             # e.g. "Erda Planning Commission"
    body_id: str                 # e.g. "7563"
    body_url: str
    scraped_at: str              # ISO-8601 UTC
    notices: list[Notice] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class PmnClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self._last_request_at: float | None = None

    def get(self, url: str) -> str:
        self._throttle()
        logger.debug("GET %s", url)
        resp = self.session.get(url, timeout=self.timeout_seconds)
        resp.raise_for_status()
        self._last_request_at = time.monotonic()
        return resp.text

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_NOTICE_ID_RE = re.compile(r"/pmn/sitemap/notice/(\d+)\.html")
_FILE_ID_RE = re.compile(r"/pmn/files/(\d+)\.")


def parse_body_page(html: str, body_id: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Parse the publicbody page. Returns (body_metadata, notice_summaries).

    body_metadata keys: public_body, entity
    Each notice summary has: notice_id, title, event_date_raw, notice_url,
    and a pre-parsed list of attachment dicts from the summary table."""
    soup = BeautifulSoup(html, "html.parser")

    # Body metadata lives in a <dl>-style definition list under
    # "General Information". We extract by label rather than by position
    # so the parser survives minor reordering.
    body_meta = {"public_body": "", "entity": ""}
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        value = dd.get_text(strip=True)
        if label == "Public Body Name":
            body_meta["public_body"] = value
        elif label == "Entity Name":
            body_meta["entity"] = value

    # Find the "Upcoming Notices" table. The page has multiple tables
    # (Board contacts, etc.), so anchor by the preceding header.
    notices_table = _find_table_after_header(soup, NOTICES_TABLE_HEADER)

    summaries: list[dict[str, Any]] = []
    if notices_table is None:
        logger.warning("No 'Upcoming Notices' table found on body %s", body_id)
        return body_meta, summaries

    for row in notices_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:  # header row or empty
            continue

        title_cell, date_cell = cells[0], cells[1]
        link = title_cell.find("a")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        match = _NOTICE_ID_RE.search(href)
        if not match:
            continue
        notice_id = match.group(1)
        title = link.get_text(strip=True)
        event_date_raw = date_cell.get_text(strip=True)

        # Attachments cell is optional (3rd column, if present).
        attachments_summary: list[dict[str, Any]] = []
        if len(cells) >= 3:
            for a in cells[2].find_all("a"):
                attachments_summary.append({
                    "file_name": a.get_text(strip=True),
                    "url": urljoin(PMN_BASE, a.get("href", "")),
                })

        summaries.append({
            "notice_id": notice_id,
            "title": title,
            "event_date_raw": event_date_raw,
            "notice_url": urljoin(PMN_BASE, href),
            "attachments_summary": attachments_summary,
        })

    return body_meta, summaries


def parse_notice_page(html: str, notice_id: str, notice_url: str) -> Notice:
    """Parse a single notice detail page. This is the page we actually send
    to the classifier — the Description/Agenda field already contains the
    structured agenda text in most cases, so we don't always need the PDF."""
    soup = BeautifulSoup(html, "html.parser")

    fields: dict[str, str] = {}
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        # Preserve line breaks in the agenda block; collapse elsewhere.
        if label == "Description/Agenda":
            fields[label] = dd.get_text("\n", strip=True)
        else:
            fields[label] = dd.get_text(" ", strip=True)

    title = _first_h1_after_crumb(soup) or fields.get("Notice Title", "")
    event_date_raw = fields.get("Event Start Date & Time")
    event_date_iso = _try_parse_event_date(event_date_raw) if event_date_raw else None

    notice_types_raw = fields.get("Notice Type(s)", "")
    notice_types = [t.strip() for t in re.split(r"[,/;]", notice_types_raw) if t.strip()]

    attachments = _parse_attachments_table(soup)

    return Notice(
        notice_id=notice_id,
        notice_url=notice_url,
        title=title,
        event_date=event_date_raw,
        event_date_iso=event_date_iso,
        notice_types=notice_types,
        agenda_text=fields.get("Description/Agenda", ""),
        meeting_location=fields.get("Meeting Location"),
        contact_name=fields.get("Contact Name"),
        contact_email=fields.get("Contact Email"),
        posted_on=fields.get("Notice Posted On"),
        attachments=attachments,
        canceled="CANCELED" in title.upper(),
    )


def _find_table_after_header(soup: BeautifulSoup, header_text: str):
    """Find the first <table> that appears after an <h?> containing header_text."""
    for header in soup.find_all(re.compile(r"^h[1-6]$")):
        if header_text.lower() in header.get_text(strip=True).lower():
            table = header.find_next("table")
            if table is not None:
                return table
    # Fallback: some PMN pages emit the header as plain <p><strong>...
    for tag in soup.find_all(["p", "div"]):
        if header_text.lower() in tag.get_text(strip=True).lower():
            table = tag.find_next("table")
            if table is not None:
                return table
    return None


def _first_h1_after_crumb(soup: BeautifulSoup) -> str | None:
    # The notice page has a site-wide <h1>Utah.gov</h1> plus the notice title
    # as an <h1> lower down. We want the last h1 that isn't "Utah.gov".
    candidates = [h1.get_text(strip=True) for h1 in soup.find_all("h1")]
    for text in reversed(candidates):
        if text and text.lower() != "utah.gov":
            return text
    return None


def _parse_attachments_table(soup: BeautifulSoup) -> list[Attachment]:
    table = _find_table_after_header(soup, "Download Attachments")
    out: list[Attachment] = []
    if table is None:
        return out
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        file_name = link.get_text(strip=True)
        url = urljoin(PMN_BASE, link.get("href", ""))
        category = cells[1].get_text(strip=True) if len(cells) > 1 else "Other"
        date_added = cells[2].get_text(strip=True) if len(cells) > 2 else None
        m = _FILE_ID_RE.search(url)
        file_id = m.group(1) if m else None
        out.append(Attachment(
            file_name=file_name,
            category=category if category in ATTACHMENT_CATEGORIES else category or "Other",
            url=url,
            date_added=date_added,
            file_id=file_id,
        ))
    return out


def _try_parse_event_date(raw: str) -> str | None:
    """PMN formats event dates like 'March 10, 2026 07:00 PM'. Return ISO-8601
    (no timezone — the site doesn't specify one explicitly). Returns None
    on parse failure rather than raising."""
    raw = raw.strip()
    for fmt in ("%B %d, %Y %I:%M %p", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def scrape_body(
    body_id: str,
    client: PmnClient,
    months_back: int | None = None,
    max_notices: int | None = None,
    jurisdiction_label: str | None = None,
) -> ScrapeResult:
    body_url = PUBLICBODY_URL.format(body_id=body_id)
    logger.info("Fetching body page: %s", body_url)
    html = client.get(body_url)
    body_meta, summaries = parse_body_page(html, body_id)

    # Optional date filter. We filter against the raw event date since that's
    # what the summary table gives us. The table dates are formatted
    # YYYY/MM/DD HH:MM AM/PM (different from the detail page!).
    cutoff = None
    if months_back is not None and months_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=months_back * 30)

    notices: list[Notice] = []
    for summary in summaries:
        if cutoff is not None:
            parsed = _parse_summary_date(summary["event_date_raw"])
            if parsed is not None and parsed < cutoff:
                logger.debug("Skipping pre-cutoff notice %s", summary["notice_id"])
                continue

        if max_notices is not None and len(notices) >= max_notices:
            break

        try:
            page_html = client.get(summary["notice_url"])
            notice = parse_notice_page(page_html, summary["notice_id"], summary["notice_url"])
        except requests.RequestException as exc:
            logger.warning("Failed to fetch notice %s: %s", summary["notice_id"], exc)
            continue
        except Exception as exc:  # parser errors
            logger.exception("Failed to parse notice %s: %s", summary["notice_id"], exc)
            continue

        notices.append(notice)

    return ScrapeResult(
        source=SOURCE_NAME,
        jurisdiction=jurisdiction_label or body_meta.get("entity", "") or body_id,
        public_body=body_meta.get("public_body", ""),
        body_id=body_id,
        body_url=body_url,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        notices=notices,
    )


def _parse_summary_date(raw: str) -> datetime | None:
    # Body-page summary table uses 'YYYY/MM/DD HH:MM AM/PM'
    for fmt in ("%Y/%m/%d %I:%M %p", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def load_jurisdictions(path: Path) -> list[dict[str, Any]]:
    if yaml is None:
        sys.exit("PyYAML is required to use --jurisdictions. "
                 "Install with: pip install pyyaml")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    # Accept either a top-level list or the same shape as the existing
    # jurisdictions.yaml (a dict keyed by jurisdiction name).
    if isinstance(data, list):
        raw_entries: Iterable[dict[str, Any]] = data
    elif isinstance(data, dict):
        raw_entries = ({"name": k, **(v or {})} for k, v in data.items())
    else:
        sys.exit("jurisdictions.yaml must be a list or dict at top level")

    entries: list[dict[str, Any]] = []
    for entry in raw_entries:
        body_id = entry.get("pmn_body_id")
        if not body_id:
            continue
        entries.append({
            "name": entry.get("name", str(body_id)),
            "pmn_body_id": str(body_id),
        })
    return entries


def to_classifier_dict(result: ScrapeResult) -> dict[str, Any]:
    """Adapter that produces the JSON shape the existing classifier expects.

    If scrape_agendas.py writes a different top-level key than 'notices'
    (e.g., 'agendas' or 'items'), change it here and the rest of the code
    stays untouched."""
    d = asdict(result)
    # The classifier in scrape_agendas.py reads agenda text under the key
    # 'agenda_text' on each item — which matches what we emit — so no other
    # remapping is needed today.
    return d


def write_output(result: ScrapeResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"pmn_{result.body_id}.json"
    path = output_dir / filename
    with path.open("w", encoding="utf-8") as fh:
        json.dump(to_classifier_dict(result), fh, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scrape the Utah Public Notice Website for a planning body.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--body-id",
        help="A single PMN public body ID (e.g. 7563 for Erda Planning Commission).",
    )
    group.add_argument(
        "--jurisdictions",
        type=Path,
        help="Path to jurisdictions.yaml; scrapes every entry with pmn_body_id set.",
    )
    p.add_argument(
        "--jurisdiction-label",
        default=None,
        help="Override the jurisdiction label in the output JSON (canonical city name). "
             "Only applies when --body-id is used. Falls back to PMN entity name if omitted.",
    )
    p.add_argument(
        "--months-back",
        type=int,
        default=24,
        help="Only include notices whose event date is within this many months "
             "(default: 24). Set to 0 for no filter.",
    )
    p.add_argument(
        "--max-notices",
        type=int,
        default=None,
        help="Cap the number of notices processed per body (default: no cap).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory to write JSON output to (default: ./output).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help=f"Seconds to wait between requests (default: {DEFAULT_REQUEST_DELAY_SECONDS}).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase log verbosity (-v, -vv).",
    )
    return p


def _configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    _configure_logging(args.verbose)

    client = PmnClient(delay_seconds=args.delay)
    months_back = args.months_back if args.months_back and args.months_back > 0 else None

    if args.body_id:
        targets = [{"name": args.jurisdiction_label, "pmn_body_id": args.body_id}]
    else:
        targets = load_jurisdictions(args.jurisdictions)
        if not targets:
            logger.error("No entries with pmn_body_id found in %s", args.jurisdictions)
            return 1

    for t in targets:
        try:
            result = scrape_body(
                body_id=t["pmn_body_id"],
                client=client,
                months_back=months_back,
                max_notices=args.max_notices,
                jurisdiction_label=t.get("name"),
            )
        except requests.RequestException as exc:
            logger.error("Body %s: HTTP error: %s", t["pmn_body_id"], exc)
            continue
        except Exception as exc:
            logger.exception("Body %s: unexpected error: %s", t["pmn_body_id"], exc)
            continue

        path = write_output(result, args.output_dir)
        print(f"Wrote {len(result.notices)} notices for "
              f"{result.public_body or t['pmn_body_id']} -> {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
