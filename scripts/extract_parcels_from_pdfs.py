"""
extract_parcels_from_pdfs.py — Phase 12 geocoding fix.

Reads the FULL agenda-item PDF body (not just title/location) and uses Haiku 4.5
to extract every geographic identifier a developer could use to find the parcel
on a map: parcel IDs, legal descriptions, street addresses, cross-streets.

Then resolves to (lon, lat) via:
  1. parcel_ids → UGRC Tooele County MapServer (existing pattern in arcgis.py)
  2. street_addresses → Nominatim
  3. cross_streets → Nominatim ("X and Y, <city>, UT")
  4. legal_descriptions → Nominatim (subdivision name + city)

Updates data/items_geocoded.csv in-place for items that previously had no lat/lng.
Also writes data/parcel_resolutions.csv as an audit log (one row per Haiku call).

Cost: ~518 items × ~3000 input tokens × Haiku 4.5 = ~$2 backfill, marginal weekly.

Run:
  ANTHROPIC_API_KEY=... python scripts/extract_parcels_from_pdfs.py
  ANTHROPIC_API_KEY=... python scripts/extract_parcels_from_pdfs.py --dry-run
  ANTHROPIC_API_KEY=... python scripts/extract_parcels_from_pdfs.py --limit 50
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
INPUT_CSV  = ROOT / "data" / "items_geocoded.csv"
FALLBACK_CSV = ROOT / "data" / "agenda_items_split.csv"
OUTPUT_CSV = ROOT / "data" / "items_geocoded.csv"
RESOLUTIONS_CSV = ROOT / "data" / "parcel_resolutions.csv"
COSTS_CSV  = ROOT / "data" / "api_costs.csv"
HEARTBEAT_DIR = ROOT / "data" / "cron_status"

MODEL = "claude-haiku-4-5-20251001"
COST_PER_INPUT_TOK  = 1.0 / 1_000_000   # Haiku 4.5 input
COST_PER_OUTPUT_TOK = 5.0 / 1_000_000   # Haiku 4.5 output

# Tooele Valley + Wasatch Front rough bbox — reject Nominatim hits outside it
WASATCH_BBOX = (-112.8, 40.0, -111.5, 41.5)

TOOELE_PARCELS_URL = "https://tcgisws.tooeleco.gov/server/rest/services/Parcels/MapServer/0"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA  = "TooeleLandIntel/1.0 extract_parcels_from_pdfs.py (github.com/camsrigby-hash)"
NOMINATIM_DELAY = 1.1

# Jurisdiction → likely county (used for parcel ID disambiguation + Nominatim hints)
COUNTY_BY_JURISDICTION = {
    "Erda": "Tooele County",
    "Grantsville": "Tooele County",
    "Tooele City": "Tooele County",
    "Stansbury Park": "Tooele County",
    "Lake Point": "Tooele County",
    "Lehi": "Utah County",
    "Saratoga Springs": "Utah County",
    "Eagle Mountain": "Utah County",
    "American Fork": "Utah County",
    "Vineyard": "Utah County",
    "Spanish Fork": "Utah County",
    "South Jordan": "Salt Lake County",
    "Herriman": "Salt Lake County",
    "Bluffdale": "Salt Lake County",
    "Draper": "Salt Lake County",
}

# Tooele County parcel ID pattern (used for direct UGRC lookup; other counties
# fall back to Nominatim address resolution since their MapServers aren't wired).
TOOELE_PARCEL_RE = re.compile(r"\b(\d{2}-\d{3}-\d{1,2}-\d{4})\b")

EXTRACTION_PROMPT = """You are a parser for Utah city/county planning agenda items. Your job is to extract every geographic identifier a developer could use to find the parcel on a map.

Read the agenda item text below. Return ONLY a JSON object with this exact shape — no markdown fences, no preamble, no commentary:

{
  "parcel_ids": [<string>, ...],
  "legal_descriptions": [<string>, ...],
  "street_addresses": [<string>, ...],
  "cross_streets": [<string>, ...],
  "confidence": "high" | "medium" | "low"
}

EXTRACTION RULES:

1. parcel_ids — Utah parcel/APN strings, formatted exactly as they appear (preserve hyphens, dots, leading zeros).
   - Common Utah formats: "NN-NNN-N-NNNN" (Tooele), "NN-NNN-NNNN" (Davis/Weber), "NN:NNN:NNNN" (some), or 7–15 digit numeric strings.
   - Look for these prefixes and capture the value that follows: "Parcel No.", "Parcel #", "Parcel ID", "Tax ID", "APN", "Sidwell Number", "Tax Parcel", "Assessor Parcel".
   - DO NOT include section/township/range numbers here — those go in legal_descriptions.
   - DO NOT include zoning codes (R-1, RM-1, A-20, MU-1, HIR, etc.).

2. legal_descriptions — any of:
   - "Lot N, Block M, [Subdivision Name]" or "Lot N of [Subdivision Name]"
   - "Section X, Township Y N/S, Range Z E/W" references (rural/agricultural land)
   - Subdivision/plat/development project names that uniquely identify the area (e.g. "Erda Estates Subdivision", "Oquirrh Point Phase 1")
   Preserve the full phrase as written.

3. street_addresses — actual numbered addresses or numbered street references.
   Examples: "1234 Main St", "approximately 2000 W", "5500 South 700 East, Lehi".
   Include city if stated in the text.

4. cross_streets — "X and Y" or "intersection of X and Y" formulations naming two named/numbered roads.
   Examples: "SR-138 and 2000 W", "5600 S & 4000 W", "intersection of Main Street and Center".
   ONLY include if BOTH roads are named/numbered — vague references like "near Main Street" go in street_addresses.

5. confidence:
   - "high": at least one parcel ID was found, OR a complete street address with city/state, OR a precise cross-street with both roads named.
   - "medium": only a legal description with a clear subdivision name, OR a partial address.
   - "low": only a vague subdivision name with no other identifiers, or nothing concrete.

Return [] for any field where nothing is found. Return ONLY the JSON object.

Agenda item text:
"""


def _make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1.0,
                    status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
    a = HTTPAdapter(max_retries=retries)
    s.mount("https://", a)
    s.mount("http://", a)
    return s


def _in_bbox(lon: float, lat: float) -> bool:
    return WASATCH_BBOX[0] <= lon <= WASATCH_BBOX[2] and WASATCH_BBOX[1] <= lat <= WASATCH_BBOX[3]


def call_haiku(client, text: str) -> tuple[dict, int, int]:
    """Returns (extraction, input_tokens, output_tokens)."""
    # Cap text at 12k chars (~3k tokens) — the typical signal density is in the first
    # few pages, and Haiku at this size is ~$0.003/call.
    body = text[:12000]
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + body}],
    )
    raw = resp.content[0].text.strip()
    # Strip any markdown fences just in case
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip().rstrip("`").strip()
    # Find first { ... } block
    if not raw.startswith("{"):
        i = raw.find("{")
        if i >= 0:
            raw = raw[i:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Haiku JSON parse failed: %s — raw=%r", e, raw[:200])
        data = {}
    extraction = {
        "parcel_ids":         [str(x).strip() for x in data.get("parcel_ids", [])         if x],
        "legal_descriptions": [str(x).strip() for x in data.get("legal_descriptions", []) if x],
        "street_addresses":   [str(x).strip() for x in data.get("street_addresses", [])   if x],
        "cross_streets":      [str(x).strip() for x in data.get("cross_streets", [])      if x],
        "confidence":         data.get("confidence", "low") if data.get("confidence") in ("high","medium","low") else "low",
    }
    return extraction, resp.usage.input_tokens, resp.usage.output_tokens


def lookup_tooele_parcel(parcel_id: str) -> tuple[float, float] | None:
    """Tooele County UGRC centroid lookup via existing arcgis helper."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from arcgis import get_parcel_centroid
    return get_parcel_centroid(TOOELE_PARCELS_URL, where=f"Parcel_ID = '{parcel_id}'")


def nominatim(session: requests.Session, query: str) -> tuple[float, float] | None:
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
        "viewbox": f"{WASATCH_BBOX[0]},{WASATCH_BBOX[3]},{WASATCH_BBOX[2]},{WASATCH_BBOX[1]}",
        "bounded": 0,
    }
    time.sleep(NOMINATIM_DELAY)
    try:
        resp = session.get(NOMINATIM_URL, params=params, timeout=15,
                           headers={"User-Agent": NOMINATIM_UA})
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        lon, lat = float(results[0]["lon"]), float(results[0]["lat"])
        if not _in_bbox(lon, lat):
            return None
        return (lon, lat)
    except Exception as e:
        log.debug("Nominatim failed for %r: %s", query[:60], e)
        return None


def resolve(session: requests.Session, jurisdiction: str, extraction: dict) -> tuple[tuple[float, float] | None, str | None]:
    """Try resolution strategies in order. Returns ((lon,lat), via) or (None, None)."""
    county = COUNTY_BY_JURISDICTION.get(jurisdiction, "Utah")
    city_hint = jurisdiction or ""

    # Strategy 1: parcel IDs → UGRC (Tooele only for now; other counties fall through)
    if county == "Tooele County":
        for pid in extraction["parcel_ids"]:
            if TOOELE_PARCEL_RE.fullmatch(pid):
                coords = lookup_tooele_parcel(pid)
                if coords:
                    return coords, "parcel_id"

    # Strategy 2: street addresses → Nominatim
    for addr in extraction["street_addresses"]:
        q = addr if (city_hint.lower() in addr.lower() or "ut" in addr.lower()) else f"{addr}, {city_hint}, UT"
        coords = nominatim(session, q)
        if coords:
            return coords, "address"

    # Strategy 3: cross-streets → Nominatim
    for cs in extraction["cross_streets"]:
        q = f"{cs}, {city_hint}, UT" if city_hint else f"{cs}, {county}, UT"
        coords = nominatim(session, q)
        if coords:
            return coords, "cross_street"

    # Strategy 4: legal descriptions / subdivision names → Nominatim
    for legal in extraction["legal_descriptions"]:
        q = f"{legal}, {city_hint}, UT" if city_hint else f"{legal}, {county}, UT"
        coords = nominatim(session, q)
        if coords:
            return coords, "legal"

    return None, None


def write_heartbeat(workflow: str, status: str, items: int, duration_ms: int, notes: str = "") -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "workflow_name": workflow,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "duration_ms": duration_ms,
        "items_processed": items,
        "notes": notes,
    }
    (HEARTBEAT_DIR / f"{workflow}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_cost(model: str, calls: int, in_tok: int, out_tok: int, items: int) -> None:
    cost = round(in_tok * COST_PER_INPUT_TOK + out_tok * COST_PER_OUTPUT_TOK, 6)
    is_new = not COSTS_CSV.exists()
    with open(COSTS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp","script","model","input_tokens","output_tokens","cost_usd","items"])
        w.writerow([
            datetime.now(timezone.utc).isoformat(),
            "extract_parcels_from_pdfs", model, in_tok, out_tok, cost, items,
        ])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Don't call Haiku; just count items needing extraction")
    p.add_argument("--limit", type=int, default=0, help="Stop after N Haiku calls (0 = unlimited)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    started = time.time()

    src = INPUT_CSV if INPUT_CSV.exists() else FALLBACK_CSV
    if not src.exists():
        log.error("No input CSV found (looked at %s and %s)", INPUT_CSV, FALLBACK_CSV)
        return 1

    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        log.warning("Input CSV empty: %s", src)
        return 0

    fieldnames = list(rows[0].keys())
    for col in ("lat","lng","geocode_source","geocode_confidence"):
        if col not in fieldnames:
            fieldnames.append(col)

    needs_extraction = [r for r in rows if not (r.get("lat") or "").strip() or (r.get("lat") or "").strip() in ("nan","None")]
    log.info("Loaded %d rows from %s; %d need extraction.", len(rows), src.name, len(needs_extraction))

    if args.dry_run:
        # Show breakdown by jurisdiction
        from collections import Counter
        breakdown = Counter(r.get("jurisdiction", "?") for r in needs_extraction)
        for j, n in sorted(breakdown.items(), key=lambda x: -x[1]):
            log.info("  %-22s  %d", j, n)
        log.info("DRY RUN — no Haiku calls made.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set; cannot run extraction.")
        return 1

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    session = _make_session()

    resolved = 0
    haiku_calls = 0
    in_tok = 0
    out_tok = 0
    resolutions: list[dict] = []

    by_id = {r["id"]: r for r in rows}

    for r in needs_extraction:
        if args.limit and haiku_calls >= args.limit:
            log.info("Hit --limit %d; stopping.", args.limit)
            break

        text = r.get("agenda_text") or r.get("description") or ""
        if len(text) < 200:
            continue

        item_id = r["id"]
        jurisdiction = r.get("jurisdiction", "")
        log.info("[%d/%d] %s (%s)", haiku_calls + 1, len(needs_extraction), item_id, jurisdiction)

        try:
            extraction, in_t, out_t = call_haiku(client, text)
        except Exception as e:
            log.warning("  Haiku call failed: %s", e)
            continue

        haiku_calls += 1
        in_tok += in_t
        out_tok += out_t

        coords, via = resolve(session, jurisdiction, extraction)
        resolution_row = {
            "agenda_item_id":     item_id,
            "parcel_ids":         json.dumps(extraction["parcel_ids"]),
            "legal_descriptions": json.dumps(extraction["legal_descriptions"]),
            "street_addresses":   json.dumps(extraction["street_addresses"]),
            "cross_streets":      json.dumps(extraction["cross_streets"]),
            "confidence":         extraction["confidence"],
            "resolved_lat":       coords[1] if coords else "",
            "resolved_lng":       coords[0] if coords else "",
            "resolved_via":       via or "",
            "extracted_at":       datetime.now(timezone.utc).isoformat(),
        }
        resolutions.append(resolution_row)

        if coords:
            r["lat"] = str(coords[1])
            r["lng"] = str(coords[0])
            r["geocode_source"] = f"haiku_pdf_{via}"
            r["geocode_confidence"] = {"high": "0.85", "medium": "0.70", "low": "0.55"}[extraction["confidence"]]
            by_id[item_id] = r
            resolved += 1
            log.info("  → (%.4f, %.4f) via %s", coords[1], coords[0], via)
        else:
            log.info("  → unresolved (parcels=%d addrs=%d cross=%d legal=%d)",
                     len(extraction["parcel_ids"]), len(extraction["street_addresses"]),
                     len(extraction["cross_streets"]), len(extraction["legal_descriptions"]))

    # Write updated items_geocoded.csv
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # Append parcel_resolutions.csv
    res_fields = ["agenda_item_id","parcel_ids","legal_descriptions","street_addresses",
                  "cross_streets","confidence","resolved_lat","resolved_lng","resolved_via","extracted_at"]
    is_new = not RESOLUTIONS_CSV.exists()
    with open(RESOLUTIONS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=res_fields, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerows(resolutions)

    if haiku_calls > 0:
        append_cost(MODEL, haiku_calls, in_tok, out_tok, resolved)

    duration_ms = int((time.time() - started) * 1000)
    write_heartbeat(
        "extract-parcels",
        "success" if haiku_calls > 0 else "partial",
        resolved,
        duration_ms,
        f"{haiku_calls} Haiku calls, {resolved}/{haiku_calls} resolved",
    )

    log.info("Done: %d Haiku calls, %d resolved (%.1f%%) in %.1fs",
             haiku_calls, resolved, (resolved/haiku_calls*100 if haiku_calls else 0), (time.time()-started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
