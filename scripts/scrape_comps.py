#!/usr/bin/env python3
"""
scrape_comps.py — Phase 15a county land comps ingestion.

County recorder systems across Tooele, Salt Lake, Utah, Davis, Weber, Wasatch,
and Box Elder use inconsistent public interfaces, and several document systems
are interactive or document-index oriented rather than bulk sale-price APIs.
This script therefore implements a per-county adapter shell plus a transparent,
always-on fallback that derives land comp proxy rows from checked-in UGRC LIR /
assessor parcel extracts. The fallback is deliberately labelled in the `source`
field as `ugrc_lir_assessor_fallback:<county>` so downstream Phase 15b can ingest
stable rows without mistaking them for recorder-verified deeds.

The adapter interface is intentionally small: when a county recorder endpoint
with bulk sale consideration becomes available, replace `fetch_recorder_rows`
for that county and keep the output schema unchanged.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import logging
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

LOG = logging.getLogger("scrape_comps")

COUNTIES = ["tooele", "salt_lake", "utah", "davis", "weber", "wasatch", "box_elder"]
COUNTY_DISPLAY = {
    "tooele": "Tooele",
    "salt_lake": "Salt Lake",
    "utah": "Utah",
    "davis": "Davis",
    "weber": "Weber",
    "wasatch": "Wasatch",
    "box_elder": "Box Elder",
}
COUNTY_URL_NAMES = {
    "tooele": "Tooele",
    "salt_lake": "SaltLake",
    "utah": "Utah",
    "davis": "Davis",
    "weber": "Weber",
    "wasatch": "Wasatch",
    "box_elder": "BoxElder",
}
ARCGIS_BASE = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services"
COMPS_COLUMNS = [
    "address", "sale_date", "sale_price", "price_per_acre", "acres",
    "zoning_class", "source", "link", "scraped_at",
]
USER_AGENT = "tooele-land-intel/phase15a county-comps (+https://github.com/camsrigby-hash/tooele-land-intel)"

RECORDER_SOURCES = {
    "tooele": "https://tooeleco.org/government/elected-officials/county-recorder/",
    "salt_lake": "https://slco.org/recorder/",
    "utah": "https://www.utahcounty.gov/LandRecords/Index.asp",
    "davis": "https://www.daviscountyutah.gov/recorder",
    "weber": "https://www.webercountyutah.gov/Recorder_Surveyor/",
    "wasatch": "https://docs.wasatch.utah.gov/PublicAccess/sample-cq/index.html",
    "box_elder": "https://www.boxeldercountyut.gov/recorder.htm",
}


@dataclass(frozen=True)
class Comp:
    address: str | None
    sale_date: str
    sale_price: int | None
    price_per_acre: float | None
    acres: float | None
    zoning_class: str | None
    source: str
    link: str | None
    scraped_at: str

    def as_row(self) -> dict[str, Any]:
        return {col: getattr(self, col) for col in COMPS_COLUMNS}


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return None
        return float(value)
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(nums[0]) if nums else None


def parse_int(value: Any) -> int | None:
    f = parse_float(value)
    return int(round(f)) if f and f > 0 else None


def open_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return path.open("r", newline="", encoding="utf-8")


def candidate_parcel_files(raw_dir: Path, county: str) -> list[Path]:
    return [p for p in [raw_dir / f"parcels_{county}.csv", raw_dir / f"parcels_{county}.csv.gz"] if p.exists()]


def is_land_candidate(row: dict[str, str]) -> bool:
    acres = parse_float(row.get("acreage") or row.get("acres"))
    if acres is None or acres < 0.5 or acres > 500:
        return False
    primary_res = (row.get("primary_res") or "").strip().upper()
    house_count = parse_int(row.get("house_count")) or 0
    bldg_sqft = parse_int(row.get("bldg_sqft")) or 0
    prop_class = (row.get("prop_class") or "").lower()
    if primary_res == "Y":
        return False
    if bldg_sqft > 2500 or house_count > 1:
        return False
    if any(tok in prop_class for tok in ("residential", "condo", "apartment")) and bldg_sqft > 0:
        return False
    return True


def row_to_comp(row: dict[str, str], county: str, scraped_at: str) -> Comp | None:
    acres = parse_float(row.get("acreage") or row.get("acres"))
    if acres is None or acres <= 0:
        return None
    land_value = parse_int(row.get("land_market_value"))
    total_value = parse_int(row.get("total_market_value"))
    sale_price = land_value or total_value
    if not sale_price:
        return None
    address_parts = [row.get("parcel_address"), row.get("parcel_city"), COUNTY_DISPLAY[county] + " County, UT"]
    address = ", ".join(str(p).strip() for p in address_parts if p and str(p).strip()) or None
    ppa = round(sale_price / acres, 2) if sale_price and acres else None
    # Annual assessor values are not true sale dates; use the current assessment year
    # and label the source transparently as fallback in the source field.
    sale_date = f"{date.today().year}-01-01"
    parcel_id = (row.get("parcel_id") or "").strip()
    link = RECORDER_SOURCES.get(county)
    if county == "utah" and parcel_id:
        serial = re.sub(r"\D", "", parcel_id)
        if serial:
            link = f"https://www.utahcounty.gov/landrecords/property.asp?av_serial={serial}"
    zoning = row.get("prop_class") or None
    return Comp(address, sale_date, sale_price, ppa, acres, zoning, f"ugrc_lir_assessor_fallback:{county}", link, scraped_at)


def fallback_from_local_csv(raw_dir: Path, county: str, limit: int, scraped_at: str) -> list[Comp]:
    rows: list[Comp] = []
    for path in candidate_parcel_files(raw_dir, county):
        LOG.info("Reading fallback parcels for %s from %s", county, path)
        with open_csv(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not is_land_candidate(row):
                    continue
                comp = row_to_comp(row, county, scraped_at)
                if comp:
                    rows.append(comp)
                if len(rows) >= limit:
                    return rows
    return rows


def fallback_from_arcgis(county: str, limit: int, scraped_at: str) -> list[Comp]:
    """Small live fallback used primarily when a county CSV is not checked in."""
    service = f"{ARCGIS_BASE}/Parcels_{COUNTY_URL_NAMES[county]}_LIR/FeatureServer/0/query"
    params = {
        "where": "PARCEL_ACRES >= 0.5 AND PARCEL_ACRES <= 500 AND PRIMARY_RES <> 'Y'",
        "outFields": "PARCEL_ID,PARCEL_ADD,PARCEL_CITY,PARCEL_ACRES,PROP_CLASS,PRIMARY_RES,HOUSE_CNT,BLDG_SQFT,TOTAL_MKT_VALUE,LAND_MKT_VALUE",
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": str(limit * 5),
    }
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        resp = session.get(service, params=params, timeout=45)
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as exc:
        LOG.warning("ArcGIS fallback failed for %s: %s", county, exc)
        return []
    out: list[Comp] = []
    for feat in features:
        attrs = {k.lower(): v for k, v in (feat.get("attributes") or {}).items()}
        row = {
            "parcel_id": attrs.get("parcel_id"),
            "parcel_address": attrs.get("parcel_add"),
            "parcel_city": attrs.get("parcel_city"),
            "acreage": attrs.get("parcel_acres"),
            "prop_class": attrs.get("prop_class"),
            "primary_res": attrs.get("primary_res"),
            "house_count": attrs.get("house_cnt"),
            "bldg_sqft": attrs.get("bldg_sqft"),
            "total_market_value": attrs.get("total_mkt_value"),
            "land_market_value": attrs.get("land_mkt_value"),
        }
        if not is_land_candidate(row):
            continue
        comp = row_to_comp(row, county, scraped_at)
        if comp:
            out.append(comp)
        if len(out) >= limit:
            break
    return out


def fetch_recorder_rows(county: str, limit: int, scraped_at: str) -> list[Comp]:
    """County recorder adapter placeholder.

    Current public recorder pages do not expose a uniform unauthenticated bulk
    sale-consideration API. Returning an empty list triggers the labelled assessor
    fallback below. This function is the replacement point for county-specific
    adapters in Phase 15a-2 follow-on hardening.
    """
    LOG.info("Recorder adapter for %s currently uses fallback; source portal=%s", county, RECORDER_SOURCES[county])
    return []


def scrape_county(county: str, raw_dir: Path, limit: int, scraped_at: str) -> list[Comp]:
    rows = fetch_recorder_rows(county, limit, scraped_at)
    if rows:
        return rows[:limit]
    rows = fallback_from_local_csv(raw_dir, county, limit, scraped_at)
    if rows:
        return rows[:limit]
    rows = fallback_from_arcgis(county, limit, scraped_at)
    if rows:
        return rows[:limit]
    LOG.warning("No recorder or fallback comp rows available for %s", county)
    return []


def dedupe(rows: Iterable[Comp]) -> list[Comp]:
    seen: set[str] = set()
    out: list[Comp] = []
    for row in rows:
        key = f"{row.address}|{row.sale_price}|{row.acres}|{row.source}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def write_csv(path: Path, rows: list[Comp]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COMPS_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row.as_row())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape Phase 15a county recorder land comps.")
    ap.add_argument("--out-dir", default="data/raw")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--county", choices=["all", *COUNTIES], default="all")
    ap.add_argument("--limit-per-county", type=int, default=25)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counties = COUNTIES if args.county == "all" else [args.county]

    failures = 0
    for county in counties:
        try:
            rows = dedupe(scrape_county(county, raw_dir, args.limit_per_county, scraped_at))
        except Exception as exc:
            failures += 1
            LOG.exception("County %s failed completely: %s", county, exc)
            rows = []
        path = out_dir / f"comps_recorder_{county}_{args.date}.csv"
        write_csv(path, rows)
        LOG.info("SUMMARY source=recorder county=%s row_count=%d errors=%d output=%s", county, len(rows), 0 if rows else 1, path)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
