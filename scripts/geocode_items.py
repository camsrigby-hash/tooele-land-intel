"""
geocode_items.py — geocode agenda_items_split.csv → items_geocoded.csv

Strategies (in order):
  1. Parcel ID regex match in title/location → UGRC centroid lookup
  2. Location field → Nominatim (1 req/sec)
  3. Street address regex in title → Nominatim
  4. Haiku 4.5 address extraction → Nominatim (capped at 100 calls)

Output: data/items_geocoded.csv (same columns + lat, lng, geocode_source, geocode_confidence)
"""

import csv
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
INPUT_CSV  = ROOT / "data" / "agenda_items_split.csv"
OUTPUT_CSV = ROOT / "data" / "items_geocoded.csv"
COSTS_CSV  = ROOT / "data" / "api_costs.csv"

TOOELE_PARCELS_URL = "https://tcgisws.tooeleco.gov/server/rest/services/Parcels/MapServer/0"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA  = "TooeleLandIntel/1.0 geocode_items.py (github.com/camsrigby-hash)"

HAIKU_CAP = 100
NOMINATIM_DELAY = 1.1  # seconds — Nominatim requires <= 1 req/sec

# Tooele County parcel ID pattern: NN-NNN-N-NNNN or NN-NNN-NN-NNNN
PARCEL_RE = re.compile(r"\b(\d{2}-\d{3}-\d{1,2}-\d{4})\b")
STREET_RE  = re.compile(r"\b(\d{3,5}\s+\w[\w\s]{2,30}(?:St|Ave|Dr|Rd|Blvd|Ln|Way|Ct|Cir|Pl|Loop|Hwy|Highway))\b", re.I)

# Tooele Valley rough bounding box — reject Nominatim hits outside it
TOOELE_BBOX = (-112.8, 40.2, -111.8, 41.0)  # (lon_min, lat_min, lon_max, lat_max)


def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _in_bbox(lon: float, lat: float) -> bool:
    return TOOELE_BBOX[0] <= lon <= TOOELE_BBOX[2] and TOOELE_BBOX[1] <= lat <= TOOELE_BBOX[3]


def geocode_parcel_id(session: requests.Session, parcel_id: str) -> tuple[float, float] | None:
    """Look up (lon, lat) centroid for a Tooele County parcel ID."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from arcgis import get_parcel_centroid
    result = get_parcel_centroid(
        TOOELE_PARCELS_URL,
        where=f"Parcel_ID = '{parcel_id}'",
    )
    return result


def geocode_nominatim(session: requests.Session, query: str) -> tuple[float, float] | None:
    """Look up (lon, lat) via Nominatim. Returns None if outside Tooele bbox."""
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
        "viewbox": f"{TOOELE_BBOX[0]},{TOOELE_BBOX[3]},{TOOELE_BBOX[2]},{TOOELE_BBOX[1]}",
        "bounded": 0,  # allow results slightly outside bbox, filter manually
    }
    time.sleep(NOMINATIM_DELAY)
    try:
        resp = session.get(
            NOMINATIM_URL, params=params, timeout=15,
            headers={"User-Agent": NOMINATIM_UA},
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        r = results[0]
        lon, lat = float(r["lon"]), float(r["lat"])
        if not _in_bbox(lon, lat):
            log.debug("Nominatim hit outside bbox: %s → (%.4f, %.4f)", query[:50], lon, lat)
            return None
        return (lon, lat)
    except Exception as e:
        log.warning("Nominatim failed for %r: %s", query[:50], e)
        return None


def extract_address_haiku(api_key: str, title: str, description: str, location: str) -> str | None:
    """Use Haiku 4.5 to extract a geocodable address from agenda item text."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    text = f"Title: {title}\nDescription: {description or ''}\nLocation field: {location or ''}"
    prompt = (
        "Extract a precise postal address or cross-street from this Utah planning agenda item. "
        "Return ONLY the address string (e.g. '1234 Main St, Grantsville, UT' or 'SR-138 & 2000 W, Erda, UT'). "
        "If no geocodable address exists, return 'NONE'.\n\n" + text
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        addr = msg.content[0].text.strip()
        return None if addr.upper() == "NONE" or not addr else addr
    except Exception as e:
        log.warning("Haiku extraction failed: %s", e)
        return None


def append_cost(script: str, model: str, calls: int, input_tokens: int, output_tokens: int, cost_usd: float, items: int) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with open(COSTS_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([ts, script, model, input_tokens, output_tokens, cost_usd, items])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not INPUT_CSV.exists():
        log.error("Input CSV not found: %s", INPUT_CSV)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # Load existing geocoded output for incremental runs (skip already-geocoded rows)
    existing: dict[str, dict] = {}
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                lat = row.get("lat", "").strip()
                if lat and lat not in ("", "nan", "None"):
                    existing[row["id"]] = row

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        log.info("No rows in input CSV.")
        return

    log.info("Loaded %d items; %d already geocoded.", len(rows), len(existing))

    session = _make_session()
    haiku_calls = 0
    haiku_input_tokens = 0
    haiku_output_tokens = 0
    stats = {"parcel": 0, "nominatim": 0, "haiku": 0, "none": 0}

    output_rows: list[dict] = []

    for row in rows:
        item_id = row.get("id", "")

        # Re-use previously geocoded result
        if item_id in existing:
            prev = existing[item_id]
            output_rows.append({**row,
                                 "lat": prev["lat"],
                                 "lng": prev["lng"],
                                 "geocode_source": prev["geocode_source"],
                                 "geocode_confidence": prev["geocode_confidence"]})
            continue

        lat = lng = geocode_source = geocode_confidence = ""
        title_text = row.get("title", "") or ""
        loc        = row.get("location", "") or ""
        desc       = row.get("description", "") or ""

        # --- Strategy 1: parcel ID in title or location field ---
        parcel_id: str | None = None
        for text in (loc, title_text, desc):
            m = PARCEL_RE.search(text)
            if m:
                parcel_id = m.group(1)
                break

        if parcel_id:
            coords = geocode_parcel_id(session, parcel_id)
            if coords:
                lng, lat = str(coords[0]), str(coords[1])
                geocode_source, geocode_confidence = "parcel_id", "0.95"
                stats["parcel"] += 1
                log.info("[%s] parcel_id=%s → (%.4f, %.4f)", item_id, parcel_id, coords[1], coords[0])

        # --- Strategy 2: location field → Nominatim ---
        if not lat and loc and len(loc.strip()) > 5:
            query = loc if "utah" in loc.lower() or "ut" in loc.lower() else loc + ", Tooele County, UT"
            coords = geocode_nominatim(session, query)
            if coords:
                lng, lat = str(coords[0]), str(coords[1])
                geocode_source, geocode_confidence = "nominatim", "0.75"
                stats["nominatim"] += 1
                log.info("[%s] nominatim(loc) → (%.4f, %.4f)", item_id, coords[1], coords[0])

        # --- Strategy 3: street address in title → Nominatim ---
        if not lat:
            m = STREET_RE.search(title_text)
            if m:
                coords = geocode_nominatim(session, m.group(1) + ", Tooele County, UT")
                if coords:
                    lng, lat = str(coords[0]), str(coords[1])
                    geocode_source, geocode_confidence = "nominatim_title", "0.65"
                    stats["nominatim"] += 1
                    log.info("[%s] nominatim(title) → (%.4f, %.4f)", item_id, coords[1], coords[0])

        # --- Strategy 4: Haiku address extraction → Nominatim (capped) ---
        if not lat and api_key and haiku_calls < HAIKU_CAP:
            agenda_text = row.get("agenda_text", "") or ""
            if len(title_text) > 10 or len(desc) > 20 or len(agenda_text) > 50:
                addr = extract_address_haiku(api_key, title_text, desc, loc)
                haiku_calls += 1
                # rough token estimate: ~60 input, ~15 output per call
                haiku_input_tokens  += 60
                haiku_output_tokens += 15
                if addr:
                    coords = geocode_nominatim(session, addr + ", Utah, USA")
                    if coords:
                        lng, lat = str(coords[0]), str(coords[1])
                        geocode_source, geocode_confidence = "haiku_nominatim", "0.70"
                        stats["haiku"] += 1
                        log.info("[%s] haiku→nominatim '%s' → (%.4f, %.4f)", item_id, addr[:50], coords[1], coords[0])

        if not lat:
            stats["none"] += 1

        output_rows.append({**row,
                             "lat": lat,
                             "lng": lng,
                             "geocode_source": geocode_source,
                             "geocode_confidence": geocode_confidence})

    # Write output CSV
    fieldnames = list(rows[0].keys())
    for col in ("lat", "lng", "geocode_source", "geocode_confidence"):
        if col not in fieldnames:
            fieldnames.append(col)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    plotted = stats["parcel"] + stats["nominatim"] + stats["haiku"]
    log.info(
        "Done. %d/%d items geocoded (parcel=%d nominatim=%d haiku=%d none=%d)",
        plotted, len(rows), stats["parcel"], stats["nominatim"], stats["haiku"], stats["none"],
    )
    log.info("Wrote %d rows → %s", len(output_rows), OUTPUT_CSV)

    if haiku_calls > 0:
        cost = round(haiku_input_tokens * 0.00000080 + haiku_output_tokens * 0.000004, 6)
        append_cost("geocode_items", "claude-haiku-4-5-20251001",
                    haiku_calls, haiku_input_tokens, haiku_output_tokens, cost, plotted)
        log.info("Logged %d Haiku calls / $%.6f to api_costs.csv", haiku_calls, cost)


if __name__ == "__main__":
    main()
