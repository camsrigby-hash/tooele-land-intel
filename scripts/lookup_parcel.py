#!/usr/bin/env python3
"""
Tooele Land Intel - parcel lookup.

Usage:
    python scripts/lookup_parcel.py 01-440-0-0019
    python scripts/lookup_parcel.py 01-440-0-0019 --pretty
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from arcgis import query_layer, query_by_point, query_by_point_radius, get_parcel_centroid

ROOT = Path(__file__).parent.parent
CFG_PATH = ROOT / "data" / "jurisdictions.yaml"


def load_cfg() -> dict:
    with open(CFG_PATH) as f:
        cfg = yaml.safe_load(f)
    base = cfg["arcgis_base"]
    def expand(val):
        if isinstance(val, str):
            return val.replace("{arcgis_base}", base)
        if isinstance(val, dict):
            return {k: expand(v) for k, v in val.items()}
        return val
    return expand(cfg)


def lookup_parcel_attributes(cfg: dict, parcel_id: str) -> dict:
    pf = cfg["parcels"]["fields"]
    rows = query_layer(
        cfg["parcels"]["url"],
        where=f"{pf['parcel_id']} = '{parcel_id}'",
    )
    if not rows:
        raise ValueError(f"Parcel {parcel_id!r} not found in Tooele County parcel layer.")
    return rows[0]


def lookup_zoning(cfg: dict, lon: float, lat: float) -> dict:
    """Spatial intersect against all zoning layers, return first hit."""
    zoning_cfg = cfg["zoning"]
    zf = zoning_cfg["fields"]
    layers = zoning_cfg["layers"]

    for layer_name, layer_id in layers.items():
        url = f"{zoning_cfg['url']}/{layer_id}"
        rows = query_by_point(url, lon, lat)
        if rows:
            r = rows[0]
            # NOTE: landuse_code intentionally dropped - source field stores
            # a Google Doc URL, not a code. See jurisdictions.yaml.
            return {
                "zone_code": r.get(zf["zone_code"]),
                "description": r.get(zf["description"]),
                "jurisdiction_layer": layer_name,
                "jurisdiction": r.get(zf["jurisdiction"]),
                "ordinance": r.get(zf["ordinance"]),
            }
    return {}


def lookup_general_plan(cfg: dict, lon: float, lat: float) -> dict:
    """Look up the 2022 Tooele County GP designation at (lon, lat).

    Strict point-in-polygon first. If the centroid falls in an unassigned
    hole in the GP layer, fall back to a radius search and return all
    distinct designations within fallback_radius_m meters.
    """
    gp = cfg["general_plan"]
    gf = gp["fields"]
    radius_m = gp.get("fallback_radius_m", 250)

    # 1) Strict hit at the centroid.
    rows = query_by_point(gp["url"], lon, lat)
    if rows:
        r = rows[0]
        return {
            "landuse_code": r.get(gf["landuse_code"]),
            "name": r.get(gf["name"]),
            "notes": r.get(gf["notes"]),
            "match": "centroid",
        }

    # 2) Fallback: list every distinct designation within radius_m meters.
    nearby = query_by_point_radius(gp["url"], lon, lat, radius_m)
    if not nearby:
        return {}

    seen = {}
    for r in nearby:
        key = (r.get(gf["landuse_code"]), r.get(gf["name"]))
        if key not in seen:
            seen[key] = {
                "landuse_code": r.get(gf["landuse_code"]),
                "name": r.get(gf["name"]),
                "notes": r.get(gf["notes"]),
            }

    return {
        "match": "nearby",
        "radius_m": radius_m,
        "note": (
            f"Parcel centroid falls in an unassigned area of the 2022 "
            f"Tooele County GP. Listing designations within {radius_m}m."
        ),
        "designations": list(seen.values()),
    }


def resolve_jurisdiction(zoning_layer: str, jurisdiction_field: str) -> str:
    layer_map = {
        "erda": "Erda City",
        "grantsville": "Grantsville City",
        "lake_point": "Lake Point",
        "municipal": "Tooele City",
        "unincorporated": "Tooele County (Unincorporated)",
    }
    return jurisdiction_field or layer_map.get(zoning_layer, "Unknown")


def main():
    parser = argparse.ArgumentParser(description="Look up a Tooele County parcel.")
    parser.add_argument("parcel_id", help="Parcel ID, e.g. 01-440-0-0019")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    cfg = load_cfg()
    parcel_id = args.parcel_id.strip()

    # 1. Parcel attributes
    attrs = lookup_parcel_attributes(cfg, parcel_id)
    pf = cfg["parcels"]["fields"]

    # 2. Get centroid for spatial lookups
    centroid = get_parcel_centroid(
        cfg["parcels"]["url"],
        where=f"{pf['parcel_id']} = '{parcel_id}'",
    )

    zoning = {}
    general_plan = {}
    if centroid:
        lon, lat = centroid
        zoning = lookup_zoning(cfg, lon, lat)
        general_plan = lookup_general_plan(cfg, lon, lat)

    jurisdiction = resolve_jurisdiction(
        zoning.get("jurisdiction_layer", ""),
        zoning.get("jurisdiction", ""),
    )

    # The 2022 County GP has partial coverage. If neither the strict intersect
    # nor the radius fallback returned anything, we're truly outside coverage.
    if not general_plan:
        general_plan = {
            "note": (
                f"No 2022 County GP coverage within radius of this parcel. "
                f"For {jurisdiction}, check the city's own general plan document."
            )
        }

    result = {
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

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))


if __name__ == "__main__":
    main()
