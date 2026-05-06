"""
fetch_census_acs.py — Phase 13b-6a: Census ACS Block-Group Income Pull

Fetches ACS 2023 5-year B19013_001E (median household income) at block-group
level for all 7 Utah counties, joins to TIGERweb block-group boundaries, and
writes tooele-land-intel/data/raw/census_acs_blockgroups.csv.

Cache strategy:
  - 365-day TTL: skip block groups whose parcel_enrichment_log row for
    source='census_acs' has enriched_at within 365 days.
  - The cache check requires CENSUS_CACHE_DB env var pointing to a local
    SQLite file that mirrors the relevant parcel_enrichment_log rows.
    If not set, cache is skipped (full fetch).

Usage:
  python scripts/fetch_census_acs.py [--county FIPS [FIPS ...]] [--dry-run]

  --county: one or more 3-digit county FIPS codes (e.g. 045 035)
            default: all 7 Utah counties
  --dry-run: probe ACS endpoint and count records, but do not write CSV

Environment:
  CENSUS_API_KEY (optional but recommended): free key from
    https://api.census.gov/data/key_signup.html
    Without a key, requests are throttled to 500/day per IP.
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

STATE_FIPS = "49"  # Utah

# 7-county FIPS (3-digit county portion only)
COUNTIES = {
    "045": "Tooele",
    "035": "Salt Lake",
    "049": "Utah",
    "011": "Davis",
    "057": "Weber",
    "051": "Wasatch",
    "003": "Box Elder",
}

ACS_BASE = "https://api.census.gov/data/2023/acs/acs5"
ACS_FALLBACK = "https://api.census.gov/data/2022/acs/acs5"
ACS_VARIABLE = "B19013_001E"
CENSUS_NULL_SENTINEL = -666666666

# Layer 5 = ACS 2024 Block Groups (12-digit GEOID: state[2]+county[3]+tract[6]+bg[1])
# Layer 4 = Census Tracts (11-digit, wrong for block-group matching)
TIGERWEB_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/Tracts_Blocks/MapServer/5/query"
)
TIGERWEB_PAGE_SIZE = 500

CACHE_TTL_DAYS = 365
REQUEST_DELAY = 0.3

DATA_DIR = Path("data/raw")
CACHE_DIR = Path("data/cache/census_acs")
OUTPUT_FILE = DATA_DIR / "census_acs_blockgroups.csv"

CSV_COLUMNS = [
    "geoid",
    "state_fips",
    "county_fips",
    "tract",
    "block_group",
    "median_income",
    "boundary_geojson",
    "fetched_at",
]

# ── HTTP Session ──────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "TooeleLandIntel/1.0 (Phase 13b-6a)"})
    return session

# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_fresh_geoids() -> set:
    """
    Load geoids that are within the 365-day cache TTL from the local
    parcel_enrichment_log cache file (if available).
    Returns a set of geoids to skip.
    """
    cache_db = os.environ.get("CENSUS_CACHE_DB")
    if not cache_db or not Path(cache_db).exists():
        return set()

    try:
        import sqlite3
        cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)).isoformat()
        conn = sqlite3.connect(cache_db)
        rows = conn.execute(
            "SELECT parcel_id FROM parcel_enrichment_log "
            "WHERE source='census_acs' AND status='ok' AND enriched_at > ?",
            (cutoff,)
        ).fetchall()
        conn.close()
        fresh = {r[0] for r in rows}
        log.info(f"Cache: {len(fresh):,} fresh geoids (within {CACHE_TTL_DAYS} days)")
        return fresh
    except Exception as e:
        log.warning(f"Cache load failed: {e} — proceeding without cache")
        return set()

# ── ACS Fetch ─────────────────────────────────────────────────────────────────

def fetch_acs_county(session: requests.Session, county_fips: str,
                     api_key: Optional[str] = None,
                     consecutive_failures: list = None) -> List[Dict]:
    """
    Fetch ACS B19013_001E for all block groups in a county.
    Returns list of dicts with keys: geoid, state_fips, county_fips,
    tract, block_group, median_income.
    """
    if consecutive_failures is None:
        consecutive_failures = [0]

    params = {
        "get": f"{ACS_VARIABLE},NAME",
        "for": "block group:*",
        "in": f"state:{STATE_FIPS} county:{county_fips}",
    }
    if api_key:
        params["key"] = api_key

    # Try 2023 first, fall back to 2022
    data = None
    for base_url in [ACS_BASE, ACS_FALLBACK]:
        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(base_url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            consecutive_failures[0] = 0
            break
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404 and base_url == ACS_BASE:
                log.warning(f"ACS 2023 not found for county {county_fips}, trying 2022...")
                continue
            consecutive_failures[0] += 1
            if consecutive_failures[0] >= 10:
                raise RuntimeError("Circuit breaker: 10 consecutive Census API failures")
            log.error(f"ACS fetch error for county {county_fips}: {e}")
            return []
        except Exception as e:
            consecutive_failures[0] += 1
            if consecutive_failures[0] >= 10:
                raise RuntimeError("Circuit breaker: 10 consecutive Census API failures")
            log.error(f"ACS fetch exception for county {county_fips}: {e}")
            return []

    if data is None or len(data) < 2:
        log.warning(f"No ACS data for county {county_fips}")
        return []

    headers = data[0]
    var_idx = headers.index(ACS_VARIABLE)
    state_idx = headers.index("state")
    county_idx = headers.index("county")
    tract_idx = headers.index("tract")
    bg_idx = headers.index("block group")

    rows = []
    for row in data[1:]:
        state = row[state_idx]
        county = row[county_idx]
        tract = row[tract_idx]
        bg = row[bg_idx]
        geoid = f"{state}{county}{tract}{bg}"

        raw_income = row[var_idx]
        try:
            income_val = int(raw_income)
            if income_val == CENSUS_NULL_SENTINEL or income_val < 0:
                income_val = None
        except (ValueError, TypeError):
            income_val = None

        rows.append({
            "geoid": geoid,
            "state_fips": state,
            "county_fips": county,
            "tract": tract,
            "block_group": bg,
            "median_income": income_val,
        })

    log.info(f"  County {county_fips} ({COUNTIES.get(county_fips, '?')}): {len(rows):,} block groups")
    return rows

# ── TIGERweb Boundary Fetch ───────────────────────────────────────────────────

def fetch_tigerweb_boundaries(session: requests.Session, county_fips: str) -> Dict[str, str]:
    """
    Fetch block-group boundary GeoJSON from TIGERweb for a county.
    Returns dict mapping geoid -> GeoJSON geometry string.
    Implements exponential backoff: 1s, 2s, 4s, 8s, then halt.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"tiger_{county_fips}.json"

    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = json.load(f)
            log.info(f"  TIGERweb cache hit for county {county_fips}: {len(cached):,} boundaries")
            return cached
        except Exception:
            pass

    boundaries = {}
    offset = 0
    backoff_delays = [1, 2, 4, 8]

    while True:
        params = {
            "where": f"STATE='{STATE_FIPS}' AND COUNTY='{county_fips}'",
            "outFields": "GEOID",
            "returnGeometry": "true",
            "f": "geojson",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": TIGERWEB_PAGE_SIZE,
        }

        attempt = 0
        data = None
        while attempt <= len(backoff_delays):
            try:
                time.sleep(REQUEST_DELAY)
                resp = session.get(TIGERWEB_URL, params=params, timeout=90)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < len(backoff_delays):
                    delay = backoff_delays[attempt]
                    log.warning(f"  TIGERweb error (county {county_fips}, offset {offset}), "
                                f"retry in {delay}s: {e}")
                    time.sleep(delay)
                    attempt += 1
                else:
                    log.error(f"  TIGERweb halted for county {county_fips} after backoff: {e}")
                    log.info(f"  Fallback: try https://www2.census.gov/geo/tiger/TIGER2023/BG/")
                    if boundaries:
                        _save_tiger_cache(cache_file, boundaries)
                    return boundaries

        if data is None:
            break

        features = data.get("features", [])
        if not features:
            break

        for feat in features:
            geoid = feat.get("properties", {}).get("GEOID")
            geom = feat.get("geometry")
            if geoid and geom:
                boundaries[geoid] = json.dumps(geom, separators=(',', ':'))

        if len(features) < TIGERWEB_PAGE_SIZE:
            break

        offset += TIGERWEB_PAGE_SIZE

    log.info(f"  TIGERweb county {county_fips}: {len(boundaries):,} boundaries fetched")
    _save_tiger_cache(cache_file, boundaries)
    return boundaries

def _save_tiger_cache(cache_file: Path, boundaries: Dict[str, str]):
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(boundaries, f)
    except Exception as e:
        log.warning(f"TIGERweb cache write failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def run(counties: List[str], dry_run: bool = False):
    api_key = os.environ.get("CENSUS_API_KEY")
    if api_key:
        log.info("Census API key found — rate limits relaxed")
    else:
        log.warning("No CENSUS_API_KEY set — throttled to 500 calls/day per IP")

    session = _make_session()
    fresh_geoids = _load_fresh_geoids()
    consecutive_failures = [0]

    all_rows = []
    per_county_counts = {}
    start_time = time.time()

    for county_fips in counties:
        county_name = COUNTIES.get(county_fips, county_fips)
        log.info(f"=== {county_name} (FIPS {STATE_FIPS}-{county_fips}) ===")

        # Fetch ACS income data
        acs_rows = fetch_acs_county(session, county_fips, api_key, consecutive_failures)

        if not acs_rows:
            log.warning(f"No ACS rows for county {county_fips} — skipping")
            per_county_counts[county_fips] = 0
            continue

        if dry_run:
            log.info(f"Dry run: {len(acs_rows):,} block groups found for {county_name}")
            per_county_counts[county_fips] = len(acs_rows)
            continue

        # Filter out fresh cache hits
        fresh_count = sum(1 for r in acs_rows if r["geoid"] in fresh_geoids)
        if fresh_count > 0:
            log.info(f"  Skipping {fresh_count:,} fresh cache hits for {county_name}")
        acs_rows = [r for r in acs_rows if r["geoid"] not in fresh_geoids]

        if not acs_rows:
            log.info(f"  All block groups for {county_name} are cache-fresh — skipping")
            per_county_counts[county_fips] = 0
            continue

        # Fetch TIGERweb boundaries
        log.info(f"  Fetching TIGERweb boundaries for {county_name}...")
        boundaries = fetch_tigerweb_boundaries(session, county_fips)

        # Merge
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        no_boundary = 0

        for row in acs_rows:
            geoid = row["geoid"]
            boundary = boundaries.get(geoid)
            if not boundary:
                no_boundary += 1
                log.debug(f"  No boundary for geoid {geoid}")

            all_rows.append({
                "geoid": geoid,
                "state_fips": row["state_fips"],
                "county_fips": row["county_fips"],
                "tract": row["tract"],
                "block_group": row["block_group"],
                "median_income": row["median_income"] if row["median_income"] is not None else "",
                "boundary_geojson": boundary or "",
                "fetched_at": fetched_at,
            })

        merged = len(acs_rows)
        log.info(f"  Merged: {merged:,} rows ({no_boundary:,} without boundary)")
        per_county_counts[county_fips] = merged

    if dry_run:
        total = sum(per_county_counts.values())
        log.info(f"=== Dry run complete: {total:,} block groups across {len(counties)} counties ===")
        for fips, count in per_county_counts.items():
            log.info(f"  {COUNTIES.get(fips, fips)} ({fips}): {count:,}")
        return per_county_counts

    # Write CSV
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Writing {len(all_rows):,} rows to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    elapsed = round(time.time() - start_time, 1)
    total = len(all_rows)
    income_count = sum(1 for r in all_rows if r["median_income"] != "")
    boundary_count = sum(1 for r in all_rows if r["boundary_geojson"] != "")
    income_pct = round(income_count / total * 100, 1) if total > 0 else 0
    boundary_pct = round(boundary_count / total * 100, 1) if total > 0 else 0

    log.info("=== SUMMARY ===")
    log.info(f"Total block groups: {total:,}")
    log.info(f"Median income populated: {income_count:,} ({income_pct}%)")
    log.info(f"Boundary GeoJSON populated: {boundary_count:,} ({boundary_pct}%)")
    log.info(f"Elapsed: {elapsed}s")
    log.info("Per-county counts:")
    for fips, count in per_county_counts.items():
        log.info(f"  {COUNTIES.get(fips, fips)} ({fips}): {count:,}")

    return per_county_counts


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Fetch Census ACS block-group income data.")
    parser.add_argument(
        "--county",
        nargs="+",
        default=list(COUNTIES.keys()),
        help="3-digit county FIPS code(s). Default: all 7 Utah counties.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe ACS endpoints and count records, but do not write CSV.",
    )
    args = parser.parse_args()

    # Validate county FIPS
    invalid = [c for c in args.county if c not in COUNTIES]
    if invalid:
        log.error(f"Unknown county FIPS: {invalid}. Valid: {list(COUNTIES.keys())}")
        sys.exit(1)

    run(args.county, args.dry_run)
