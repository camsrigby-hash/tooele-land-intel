"""Phase 18b-2c REST ingest — Vineyard, Grantsville, Bluffdale, Draper.

Fetches GP FLU polygons from ArcGIS FeatureServer endpoints, reprojects to
EPSG:4326 (via outSR=4326 param), resolves coded domains, and writes
schema-v2 GeoJSONs to data/zoning/future/.

Run from tooele-land-intel repo root:
    py -3 scripts/ingest_gp_flu_rest_18b2c.py
"""

import json
import math
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

OUT_DIR = Path("data/zoning/future")
EXTRACTION_DATE = "2026-05-16"
REQUEST_DELAY = 0.5  # polite rate limit between paginated requests

# Expected feature counts from pre-check inventory
EXPECTED_COUNTS = {
    "vineyard": 36,
    "grantsville": 51,
    "bluffdale": 94,
    "draper": 62,
}

# City center coords for centroid validation (lat, lon)
CITY_CENTERS = {
    "vineyard": (40.2994, -111.7495),
    "grantsville": (40.6000, -112.4668),
    "bluffdale": (40.4799, -111.9388),
    "draper": (40.5246, -111.8638),
}

MAX_CENTROID_KM = 10.0  # generous threshold for district-level GP maps


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "TooeleLandIntel/1.0"})
    return session


SESSION = make_session()


def fetch_layer_meta(url: str) -> dict:
    resp = SESSION.get(url, params={"f": "json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def resolve_coded_domain(meta: dict, field_name: str) -> dict[str, str]:
    """Return {code: label} map for a coded-value domain on the given field."""
    for field in meta.get("fields", []):
        if field.get("name") == field_name:
            domain = field.get("domain") or {}
            if domain.get("type") == "codedValue":
                return {str(cv["code"]): cv["name"] for cv in domain.get("codedValues", [])}
    return {}


def query_all_features(url: str, flu_field: str, extra_fields: list[str] | None = None) -> list[dict]:
    """Paginate through all features, requesting geometry in EPSG:4326."""
    all_features = []
    offset = 0
    page_size = 1000
    out_fields = flu_field
    if extra_fields:
        out_fields = f"{flu_field},{','.join(extra_fields)}"

    while True:
        params = {
            "where": "1=1",
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "f": "json",
        }
        resp = SESSION.get(f"{url}/query", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        features = data.get("features", [])
        all_features.extend(features)
        if not data.get("exceededTransferLimit", False) or len(features) < page_size:
            break
        offset += len(features)
        time.sleep(REQUEST_DELAY)

    return all_features


def rings_to_geojson_geometry(esri_geom: dict) -> dict | None:
    """Convert Esri polygon geometry (rings) to GeoJSON Polygon or MultiPolygon."""
    rings = esri_geom.get("rings")
    if not rings:
        return None

    # Separate outer rings (CW) from holes (CCW) by signed area
    def signed_area(ring):
        n = len(ring)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += ring[i][0] * ring[j][1]
            area -= ring[j][0] * ring[i][1]
        return area / 2.0

    # GeoJSON exterior rings are CCW, holes are CW — Esri is the opposite
    # We'll just trust the ring order from the server and wrap them as-is.
    # Single ring → Polygon; multiple rings → MultiPolygon (one per outer ring)
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": [rings[0]]}

    # Heuristic: first ring is outer, subsequent are holes (common for Esri)
    # If there are multiple outer rings, produce MultiPolygon
    outers = []
    holes = []
    for ring in rings:
        area = signed_area(ring)
        if area > 0:  # CCW in standard math = outer in GeoJSON
            outers.append(ring)
        else:
            holes.append(ring)

    if not outers:
        # Fallback: treat all as outer rings of separate single-ring polygons
        # MultiPolygon coords: [polygon, ...] where polygon = [ring, ...]
        return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}

    if len(outers) == 1:
        poly_rings = [outers[0]] + holes
        return {"type": "Polygon", "coordinates": poly_rings}

    # Multiple outer rings — assign holes to nearest outer (simplified: attach all holes to first)
    # polys: list of polygons, each polygon = [outer_ring, optional_holes...]
    polys = [[outer] for outer in outers]
    for hole in holes:
        polys[0].append(hole)
    # MultiPolygon coords: [polygon1, polygon2, ...] — polys is already in that shape
    return {"type": "MultiPolygon", "coordinates": polys}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def centroid_of_feature_collection(geojson_features: list[dict]) -> tuple[float, float] | None:
    """Compute naive centroid (mean of all ring vertices) of all features."""
    lats, lons = [], []
    for feat in geojson_features:
        geom = feat.get("geometry")
        if not geom:
            continue
        if geom["type"] == "Polygon":
            for ring in geom["coordinates"]:
                for pt in ring:
                    lons.append(pt[0])
                    lats.append(pt[1])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["coordinates"]:
                for ring in poly:
                    for pt in ring:
                        lons.append(pt[0])
                        lats.append(pt[1])
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def build_geojson(features_raw: list[dict], flu_field: str, city_slug: str,
                  city_name: str, jurisdiction: str, source_url: str,
                  domain_map: dict[str, str] | None = None,
                  extra_prop_fn=None) -> dict:
    """Build a GeoJSON FeatureCollection in schema v2."""
    geojson_features = []
    for feat in features_raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson_geometry(geom_raw)
        if not geom:
            continue
        attrs = feat.get("attributes", {})
        raw_code = attrs.get(flu_field)
        if raw_code is None:
            raw_code = ""
        # Resolve coded domain to label if applicable
        if domain_map:
            label = domain_map.get(str(raw_code), str(raw_code))
        else:
            label = str(raw_code) if raw_code is not None else ""

        props = {
            "city_name": city_name,
            "city_slug": city_slug,
            "confidence": "rest_api",
            "extraction_date": EXTRACTION_DATE,
            "extraction_method": "arcgis_rest",
            "future_layer_type": "flu",
            "gp_zone_code": str(raw_code),
            "gp_zone_description": label,
            "jurisdiction": jurisdiction,
            "source_rest_url": source_url,
        }
        if extra_prop_fn:
            props.update(extra_prop_fn(attrs))

        geojson_features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {"type": "FeatureCollection", "features": geojson_features}


def validate_and_report(city_slug: str, geojson: dict) -> bool:
    """Print validation summary. Returns True if all checks pass."""
    n = len(geojson["features"])
    expected = EXPECTED_COUNTS[city_slug]
    center_lat, center_lon = CITY_CENTERS[city_slug]

    centroid = centroid_of_feature_collection(geojson["features"])
    if centroid:
        dist_km = haversine_km(center_lat, center_lon, centroid[0], centroid[1])
    else:
        dist_km = float("inf")

    count_ok = n == expected
    centroid_ok = dist_km <= MAX_CENTROID_KM

    print(f"\n{'='*50}")
    print(f"  {city_slug.upper()}")
    print(f"{'='*50}")
    print(f"  Features:  {n:3d}  (expected {expected}) {'PASS' if count_ok else 'FAIL'}")
    print(f"  Centroid dist to city center: {dist_km:.2f} km  {'PASS' if centroid_ok else 'FAIL'}")
    if centroid:
        print(f"  Centroid: lat={centroid[0]:.4f}, lon={centroid[1]:.4f}")

    # Zone breakdown
    zone_counts: dict[str, int] = {}
    for feat in geojson["features"]:
        z = feat["properties"].get("gp_zone_description", "")
        zone_counts[z] = zone_counts.get(z, 0) + 1
    print(f"  Zone types ({len(zone_counts)}):")
    for zone, cnt in sorted(zone_counts.items(), key=lambda x: -x[1]):
        print(f"    {cnt:3d}  {zone}")

    return count_ok and centroid_ok


# ─── City ingest functions ────────────────────────────────────────────────────

def ingest_vineyard() -> dict:
    url = "https://services.arcgis.com/QdlehUncXjEmQYtI/arcgis/rest/services/Vineyard_Future_Land_Use_View/FeatureServer/0"
    flu_field = "Land_Use"
    print(f"\nFetching Vineyard metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print(f"Querying Vineyard features...")
    raw = query_all_features(url, flu_field, extra_fields=["Acres"])
    print(f"  Retrieved {len(raw)} raw features")

    def extra_fn(attrs):
        return {"acreage": attrs.get("Acres")}

    geojson = build_geojson(
        raw, flu_field,
        city_slug="vineyard",
        city_name="Vineyard",
        jurisdiction="Vineyard City",
        source_url=url,
        extra_prop_fn=extra_fn,
    )
    return geojson


def ingest_grantsville() -> dict:
    url = "https://services5.arcgis.com/uWdqWzgcb7gCRVuK/arcgis/rest/services/Future_Land_Use_Map/FeatureServer/81"
    flu_field = "Name"
    print(f"\nFetching Grantsville metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print(f"Querying Grantsville features...")
    raw = query_all_features(url, flu_field)
    print(f"  Retrieved {len(raw)} raw features")

    geojson = build_geojson(
        raw, flu_field,
        city_slug="grantsville",
        city_name="Grantsville",
        jurisdiction="Grantsville City",
        source_url=url,
    )
    return geojson


def ingest_bluffdale() -> dict:
    url = "https://services3.arcgis.com/ojBMkFlpg5ujUNtB/arcgis/rest/services/LandUse/FeatureServer/0"
    flu_field = "LandUse"
    print(f"\nFetching Bluffdale metadata (coded domain resolution)...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    domain_map = resolve_coded_domain(meta, flu_field)
    print(f"  Domain map ({len(domain_map)} entries): {domain_map}")
    print(f"Querying Bluffdale features...")
    raw = query_all_features(url, flu_field, extra_fields=["Type", "Acres"])
    print(f"  Retrieved {len(raw)} raw features")

    def extra_fn(attrs):
        return {
            "acreage": attrs.get("Acres"),
            "gp_zone_type": attrs.get("Type"),
        }

    geojson = build_geojson(
        raw, flu_field,
        city_slug="bluffdale",
        city_name="Bluffdale",
        jurisdiction="Bluffdale City",
        source_url=url,
        domain_map=domain_map,
        extra_prop_fn=extra_fn,
    )
    return geojson


def ingest_draper() -> dict:
    url = "https://services2.arcgis.com/nAPVXppTJAHM40Se/arcgis/rest/services/Land_Use_Public/FeatureServer/3"
    flu_field = "LAND_USE"
    print(f"\nFetching Draper metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print(f"Querying Draper features...")
    raw = query_all_features(url, flu_field, extra_fields=["ACRES", "LABEL"])
    print(f"  Retrieved {len(raw)} raw features")

    def extra_fn(attrs):
        return {
            "acreage": attrs.get("ACRES"),
            "gp_zone_label": attrs.get("LABEL"),
        }

    geojson = build_geojson(
        raw, flu_field,
        city_slug="draper",
        city_name="Draper",
        jurisdiction="Draper City",
        source_url=url,
        extra_prop_fn=extra_fn,
    )
    return geojson


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cities = [
        ("vineyard", ingest_vineyard, "vineyard_gp.geojson"),
        ("grantsville", ingest_grantsville, "grantsville_gp.geojson"),
        ("bluffdale", ingest_bluffdale, "bluffdale_gp.geojson"),
        ("draper", ingest_draper, "draper_gp.geojson"),
    ]

    results = {}
    for city_slug, ingest_fn, out_fname in cities:
        try:
            geojson = ingest_fn()
            ok = validate_and_report(city_slug, geojson)
            out_path = OUT_DIR / out_fname
            out_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
            print(f"  Written: {out_path}  ({'OK' if ok else 'VALIDATION FAIL'})")
            results[city_slug] = {"ok": ok, "n": len(geojson["features"]), "path": str(out_path)}
        except Exception as e:
            print(f"\nERROR ingesting {city_slug}: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
            results[city_slug] = {"ok": False, "error": str(e)}
        time.sleep(REQUEST_DELAY)

    print("\n\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    all_ok = True
    for slug, res in results.items():
        status = "PASS" if res.get("ok") else "FAIL"
        n = res.get("n", "ERR")
        print(f"  {slug:15s}  {n:3}  {status}")
        if not res.get("ok"):
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
