#!/usr/bin/env python3
"""
Tooele Land Intel - opportunity analysis.

Extends the base parcel report with multi-strategy rezone opportunity scoring:
  1. General Plan context within 1 mile (all designations, not just nearest)
  2. Zoning context within 1 mile across all jurisdiction layers
  3. Corridor detection (SR-36 / SR-138 Erda Way / Midvalley Highway)
  4. Five strategies scored 0-5:
       - Residential upzone
       - Commercial / highway corridor
       - Manufacturing / Distribution
       - Planned Community (PC)
       - Hold (baseline)

Each strategy score comes from four components (0-2 each, total /8 -> /5):
  gp_alignment:    Does a supportive GP designation exist within 1mi?
  zone_precedent:  Is that target zone already present within 1mi?
  corridor_access: On a major highway corridor (within 500m)?
  size_fit:         Is acreage appropriate for the strategy?

Each strategy also lists "unknowns" - the diligence items the tool cannot
answer yet (water rights, sewer availability, political temperature, etc.).

Usage:
    # Standalone - runs the base lookup internally:
    python scripts/analyze_opportunity.py 01-440-0-0019 --pretty

    # Pipe from existing tool:
    python scripts/lookup_parcel.py 01-440-0-0019 \\
        | python scripts/analyze_opportunity.py - --pretty

    # From a saved base report:
    python scripts/analyze_opportunity.py --file output/parcel_01-440-0-0019.json --pretty
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arcgis import query_by_point_radius, get_parcel_centroid  # noqa: E402
from lookup_parcel import (  # noqa: E402
    load_cfg,
    lookup_parcel_attributes,
    lookup_zoning,
    lookup_general_plan,
    resolve_jurisdiction,
)

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------
BUFFER_METERS = 1609  # 1 mile
CORRIDOR_PROXIMITY_M = 500

# Rough corridor segments for Tooele Valley. Each entry is either:
#   {"lon": X, "lat_min": A, "lat_max": B}  - vertical (N-S) segment
#   {"lat": Y, "lon_min": A, "lon_max": B}  - horizontal (E-W) segment
# This is an approximation; confirm visually for any flagged parcel.
MAJOR_CORRIDORS: dict[str, list[dict]] = {
    "SR-36": [
        {"lon": -112.298, "lat_min": 40.50, "lat_max": 40.72},
    ],
    "SR-138 / Erda Way": [
        {"lat": 40.605, "lon_min": -112.46, "lon_max": -112.28},
    ],
    "Midvalley Highway": [
        {"lon": -112.35, "lat_min": 40.50, "lat_max": 40.68},
    ],
}


# ----------------------------------------------------------------------------
# Geometry / classification helpers
# ----------------------------------------------------------------------------

def _meters_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance in meters."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def classify_zone(zone_code: str | None) -> str:
    """Bucket a zone code into a category for strategy scoring."""
    if not zone_code:
        return "unknown"
    z = str(zone_code).upper().strip()
    if z.startswith("A-"):
        return "agricultural"
    if z.startswith("RR-"):
        return "rural_residential"
    if z.startswith(("R-", "MR", "MFR", "SFR")):
        return "residential"
    if z.startswith("C-") or z == "NC":
        return "commercial"
    if z in ("MD", "M-G", "M-1", "M-2", "LI", "HI", "MG", "IND"):
        return "industrial"
    if z == "PC":
        return "planned_community"
    if z == "MU":
        return "mixed_use"
    return "other"


def classify_gp(code: str | None, name: str | None) -> str:
    """Bucket a 2022 Tooele County GP designation into a category."""
    code_u = (code or "").upper()
    name_u = (name or "").upper()
    if "RESIDENTIAL" in name_u or code_u in ("HIR", "MIR", "LIR"):
        if code_u == "HIR" or "HIGH" in name_u:
            return "residential_high"
        if code_u == "MIR" or "MEDIUM" in name_u:
            return "residential_medium"
        return "residential_low"
    if code_u == "EMP" or "EMPLOYMENT" in name_u:
        return "employment"
    if "COMMERCIAL" in name_u or "RETAIL" in name_u:
        return "commercial"
    if "INDUSTRIAL" in name_u or "MANUFACTURING" in name_u:
        return "industrial"
    if any(k in name_u for k in ("CENTER", "MIXED", "VILLAGE", "TOWN")):
        return "mixed_use_center"
    if "AGRIC" in name_u or "RURAL" in name_u:
        return "rural_agricultural"
    if "OPEN" in name_u or "PARK" in name_u:
        return "open_space"
    return "other"


def detect_corridors(lon: float, lat: float) -> list[dict]:
    """Return list of major corridors the parcel is within CORRIDOR_PROXIMITY_M of."""
    hits: list[dict] = []
    for name, segs in MAJOR_CORRIDORS.items():
        for seg in segs:
            if "lon" in seg and "lat_min" in seg:
                if seg["lat_min"] <= lat <= seg["lat_max"]:
                    d = _meters_between(lon, lat, seg["lon"], lat)
                    if d <= CORRIDOR_PROXIMITY_M:
                        hits.append({"name": name, "distance_m": round(d)})
                        break
            elif "lat" in seg and "lon_min" in seg:
                if seg["lon_min"] <= lon <= seg["lon_max"]:
                    d = _meters_between(lon, lat, lon, seg["lat"])
                    if d <= CORRIDOR_PROXIMITY_M:
                        hits.append({"name": name, "distance_m": round(d)})
                        break
    return hits


# ----------------------------------------------------------------------------
# Buffer queries
# ----------------------------------------------------------------------------

def collect_gp_context(cfg: dict, lon: float, lat: float) -> dict:
    """List unique GP designations within BUFFER_METERS."""
    gp = cfg["general_plan"]
    gf = gp["fields"]
    try:
        rows = query_by_point_radius(gp["url"], lon, lat, BUFFER_METERS)
    except Exception as e:
        return {"error": str(e), "designations": []}

    seen: dict[str, dict] = {}
    for r in rows:
        code = r.get(gf["landuse_code"])
        name = r.get(gf["name"])
        if not code:
            continue
        key = str(code)
        if key not in seen:
            seen[key] = {
                "landuse_code": code,
                "name": name,
                "category": classify_gp(code, name),
                "polygon_count": 0,
            }
        seen[key]["polygon_count"] += 1

    return {"designations": sorted(seen.values(), key=lambda d: str(d["landuse_code"]))}


def collect_zoning_context(cfg: dict, lon: float, lat: float) -> dict:
    """List unique zones within BUFFER_METERS across every jurisdiction layer."""
    z = cfg["zoning"]
    zf = z["fields"]
    base_url = z["url"]
    layers = z["layers"]

    result: dict = {}
    for layer_name, layer_id in layers.items():
        url = f"{base_url}/{layer_id}"
        try:
            rows = query_by_point_radius(url, lon, lat, BUFFER_METERS)
        except Exception as e:
            result[layer_name] = {"error": str(e)}
            continue

        seen: dict[str, dict] = {}
        for r in rows:
            code = r.get(zf["zone_code"])
            desc = r.get(zf["description"])
            if not code:
                continue
            key = str(code)
            if key not in seen:
                seen[key] = {
                    "zone_code": code,
                    "description": desc,
                    "category": classify_zone(code),
                    "polygon_count": 0,
                }
            seen[key]["polygon_count"] += 1

        result[layer_name] = sorted(seen.values(), key=lambda x: str(x["zone_code"]))
    return result


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

def _flatten_zoning(zoning_context: dict) -> list[dict]:
    flat: list[dict] = []
    for jurisdiction, items in zoning_context.items():
        if isinstance(items, list):
            for it in items:
                entry = dict(it)
                entry["jurisdiction_layer"] = jurisdiction
                flat.append(entry)
    return flat


def _first_where(items: list[dict], predicate) -> dict | None:
    for it in items:
        if predicate(it):
            return it
    return None


def score_opportunities(gp_context: dict, zoning_context: dict,
                        corridor_hits: list[dict], acres: float) -> list[dict]:
    gp_list = gp_context.get("designations", []) if isinstance(gp_context, dict) else []
    zones = _flatten_zoning(zoning_context)
    on_corridor = bool(corridor_hits)

    def norm(raw: int) -> float:
        return round((raw / 8.0) * 5.0, 1)

    strategies: list[dict] = []

    # ---- Residential upzone ----
    res_gp = _first_where(gp_list, lambda g: str(g.get("category", "")).startswith("residential"))
    res_zone = _first_where(zones, lambda z: z.get("category") in ("residential", "rural_residential"))
    gp_s = 2 if res_gp else 0
    zn_s = 2 if res_zone else 0
    co_s = 1
    sz_s = 2 if acres >= 5 else (1 if acres >= 2 else 0)
    strategies.append({
        "strategy": "Residential upzone (Ag/RR -> MIR/HIR equivalent)",
        "score_0_5": norm(gp_s + zn_s + co_s + sz_s),
        "components": {"gp_alignment": gp_s, "zone_precedent": zn_s,
                       "corridor_access": co_s, "size_fit": sz_s},
        "notes": (
            f"Residential GP in 1mi buffer: {res_gp['landuse_code'] if res_gp else 'none'}. "
            f"Residential zoning in 1mi buffer: {res_zone['zone_code'] if res_zone else 'none'}. "
            "Headwind: recent Erda denials cite septic/well concerns and neighbor opposition "
            "(e.g. DR Horton RR-5 -> RR-1 at Bates Canyon was returned unfavorable)."
        ),
        "unknowns": [
            "sewer/water availability",
            "school capacity",
            "current council appetite for density rezones",
        ],
    })

    # ---- Commercial / corridor ----
    com_gp = _first_where(gp_list, lambda g: g.get("category") in
                          ("employment", "commercial", "mixed_use_center"))
    com_zone = _first_where(zones, lambda z: z.get("category") in ("commercial", "mixed_use"))
    gp_s = 2 if com_gp else 0
    zn_s = 2 if com_zone else 0
    co_s = 2 if on_corridor else 0
    sz_s = 2 if 2 <= acres <= 40 else (1 if acres < 80 else 0)
    strategies.append({
        "strategy": "Commercial / highway corridor (Ag -> C-G / C-H / NC)",
        "score_0_5": norm(gp_s + zn_s + co_s + sz_s),
        "components": {"gp_alignment": gp_s, "zone_precedent": zn_s,
                       "corridor_access": co_s, "size_fit": sz_s},
        "notes": (
            f"Corridor: {', '.join(c['name'] for c in corridor_hits) if corridor_hits else 'none within 500m'}. "
            f"Employment/commercial GP in buffer: {com_gp['landuse_code'] if com_gp else 'none'}. "
            "Tailwind: Erda GP explicitly documents shortage of employment/retail land "
            "and calls for intensity at major corridors/intersections."
        ),
        "unknowns": [
            "traffic counts on corridor",
            "utility sizing for commercial loads",
            "competing retail sites within 3-mile trade area",
        ],
    })

    # ---- Manufacturing / Distribution ----
    ind_gp = _first_where(gp_list, lambda g: g.get("category") in ("industrial", "employment"))
    ind_zone = _first_where(zones, lambda z: z.get("category") == "industrial")
    gp_s = 2 if ind_gp else 0
    zn_s = 2 if ind_zone else 0
    co_s = 2 if on_corridor else 1
    sz_s = 2 if acres >= 5 else (1 if acres >= 2 else 0)
    strategies.append({
        "strategy": "Manufacturing / Distribution (Ag -> MD)",
        "score_0_5": norm(gp_s + zn_s + co_s + sz_s),
        "components": {"gp_alignment": gp_s, "zone_precedent": zn_s,
                       "corridor_access": co_s, "size_fit": sz_s},
        "notes": (
            f"Industrial GP in buffer: {ind_gp['landuse_code'] if ind_gp else 'none'}. "
            f"Industrial zoning in buffer: {ind_zone['zone_code'] if ind_zone else 'none'}. "
            "Precedent: Walters Ranch 160-acre A-20 -> MD application filed Sept 2025 near Tooele Valley Airport."
        ),
        "unknowns": [
            "proximity to airport/rail",
            "truck route access",
            "neighborhood compatibility / dust & noise buffers",
        ],
    })

    # ---- Planned Community (PC) ----
    pc_zone = _first_where(zones, lambda z: z.get("category") == "planned_community")
    gp_s = 1  # PC is GP-permissive by design
    zn_s = 2 if pc_zone else 0
    co_s = 1 if on_corridor else 0
    sz_s = 2 if acres >= 40 else (1 if acres >= 20 else 0)
    size_label = (
        "well-suited" if acres >= 40 else
        ("borderline" if acres >= 20 else "likely under minimum")
    )
    strategies.append({
        "strategy": "Planned Community / PC district (mixed use w/ development agreement)",
        "score_0_5": norm(gp_s + zn_s + co_s + sz_s),
        "components": {"gp_alignment": gp_s, "zone_precedent": zn_s,
                       "corridor_access": co_s, "size_fit": sz_s},
        "notes": (
            "Erda adopted a PC zoning framework in 2024 (Six Mile Ranch precedent). "
            f"At {acres} acres, parcel is {size_label} for a standalone PC; "
            "assemblage with neighbors may be required."
        ),
        "unknowns": [
            "assemblage feasibility",
            "developer partner availability",
            "25% open-space carve-out impact on yield",
        ],
    })

    # ---- Hold ----
    strategies.append({
        "strategy": "Hold / do nothing (baseline)",
        "score_0_5": 2.5,
        "components": {"baseline": 2.5},
        "notes": (
            "Baseline for comparison. Viable if carry costs are low and "
            "surrounding area is upzoning on its own momentum."
        ),
        "unknowns": [
            "annual carry cost",
            "tax reassessment trajectory",
            "owner motivation / price flexibility",
        ],
    })

    strategies.sort(key=lambda s: s["score_0_5"], reverse=True)
    return strategies


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------

def enrich(cfg: dict, base_report: dict) -> dict:
    """Take a base parcel JSON (from lookup_parcel.py) and add opportunity_analysis."""
    centroid = base_report.get("centroid_lon_lat")
    if not centroid or len(centroid) != 2:
        return {**base_report, "opportunity_analysis": {"error": "missing centroid_lon_lat"}}
    lon, lat = centroid[0], centroid[1]

    acres = base_report.get("acres") or 0
    try:
        acres = float(acres)
    except (TypeError, ValueError):
        acres = 0.0

    gp_context = collect_gp_context(cfg, lon, lat)
    zoning_context = collect_zoning_context(cfg, lon, lat)
    corridor_hits = detect_corridors(lon, lat)
    strategies = score_opportunities(gp_context, zoning_context, corridor_hits, acres)

    top = strategies[0] if strategies else None
    runner_up = strategies[1] if len(strategies) > 1 else None
    if top and runner_up:
        headline = (
            f"Top: {top['strategy']} (score {top['score_0_5']}/5); "
            f"runner-up: {runner_up['strategy']} (score {runner_up['score_0_5']}/5)"
        )
    elif top:
        headline = top["strategy"]
    else:
        headline = "no strategies scored"

    return {
        **base_report,
        "opportunity_analysis": {
            "buffer_radius_m": BUFFER_METERS,
            "buffer_radius_miles": round(BUFFER_METERS / 1609.34, 2),
            "corridors": corridor_hits,
            "general_plan_context": gp_context,
            "zoning_context": zoning_context,
            "strategies_ranked": strategies,
            "headline": headline,
        },
    }


def run_base_lookup(cfg: dict, parcel_id: str) -> dict:
    """Same logic as lookup_parcel.main() but returns the dict instead of printing."""
    attrs = lookup_parcel_attributes(cfg, parcel_id)
    pf = cfg["parcels"]["fields"]

    centroid = get_parcel_centroid(
        cfg["parcels"]["url"],
        where=f"{pf['parcel_id']} = '{parcel_id}'",
    )

    zoning: dict = {}
    general_plan: dict = {}
    if centroid:
        lon, lat = centroid
        zoning = lookup_zoning(cfg, lon, lat)
        general_plan = lookup_general_plan(cfg, lon, lat)

    jurisdiction = resolve_jurisdiction(
        zoning.get("jurisdiction_layer", ""),
        zoning.get("jurisdiction", ""),
    )

    if not general_plan:
        general_plan = {
            "note": (
                f"No 2022 County GP coverage within radius of this parcel. "
                f"For {jurisdiction}, check the city's own general plan document."
            )
        }

    return {
        "parcel_id": parcel_id,
        "owner": attrs.get(pf["owner"]),
        "all_owners": attrs.get(pf["all_owners"]),
        "acres": attrs.get(pf["acres_tax"]),
        "acres_geo": attrs.get(pf["acres_geo"]),
        "situs_address": attrs.get(pf["situs_address"]) or None,
        "area_name": attrs.get(pf["area_name"]),
        "section_twp_range": attrs.get(pf["section_twp_range"]),
        "subdivision": attrs.get(pf["subdivision"]) or None,
        "year_built": attrs.get(pf["year_built"]) or None,
        "total_market_value": attrs.get(pf["total_market"]),
        "property_codes": attrs.get(pf["property_codes"]),
        "jurisdiction": jurisdiction,
        "zoning": zoning,
        "general_plan": general_plan,
        "centroid_lon_lat": list(centroid) if centroid else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze rezone / development opportunity for a Tooele County parcel."
    )
    parser.add_argument(
        "parcel_id",
        nargs="?",
        help="Parcel ID (e.g. 01-440-0-0019), or '-' to read base JSON from stdin.",
    )
    parser.add_argument("--file", help="Path to a base parcel JSON file.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    cfg = load_cfg()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            base = json.load(f)
    elif args.parcel_id == "-":
        base = json.loads(sys.stdin.read())
    elif args.parcel_id:
        base = run_base_lookup(cfg, args.parcel_id.strip())
    else:
        parser.error("Provide a parcel_id, '-' to read from stdin, or --file <path>.")

    enriched = enrich(cfg, base)
    indent = 2 if args.pretty else None
    print(json.dumps(enriched, indent=indent))


if __name__ == "__main__":
    main()
