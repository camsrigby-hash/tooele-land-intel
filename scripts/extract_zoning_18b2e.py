"""Phase 18b-2e NLS replacement — REST extractions for SS / Lehi / Eagle Mountain.

Fetches zone polygons / parcel-level features from city ArcGIS endpoints and
writes schema-v2 GeoJSONs to data/zoning/current/ and data/zoning/future/.

Layers extracted:
  SS zoning    → data/zoning/current/saratoga_springs_ut_zoning.geojson   (~369 polys)
  SS FLU       → data/zoning/future/saratoga_springs_gp.geojson            (~33  polys)
  Lehi zoning  → data/zoning/current/lehi_ut_zoning.geojson                (~585 polys)
  Lehi GP      → data/zoning/future/lehi_gp.geojson                        (~429 polys)
  EM zoning    → data/zoning/current/eagle_mountain_ut_zoning.geojson      (~22,184 parcels)

Run from tooele-land-intel repo root:
    py -3 scripts/extract_zoning_18b2e.py

Design decisions (pre-locked per PHASE_18b-2e_SCOPING.md):
  - Lehi GP: normalize by Descriptio (not Code); strip double-space typo; flag 4 miscoded
    features with provenance_note='miscoded_candidate_for_review'.
  - Eagle Mountain zoning: General_Zoning (coded domain) = canonical zone_code;
    Zoning (free text) preserved as zone_current_raw; Current_Landuse preserved as current_landuse.
  - Eagle Mountain FLU: OUT OF SCOPE (Prompt C, Cam-KMZ overlay).
  - SS FLU flu_currency_note: 'Ord 25-75 Dec 2 2025 was Water Element only; FLU layer is current adopted'.
  - All layers: source_method='arcgis_rest', confidence='rest_api' (SD-28 tier 1 / green dot).
"""

import json
import math
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

CURRENT_DIR = Path("data/zoning/current")
FUTURE_DIR  = Path("data/zoning/future")
EXTRACTION_DATE = "2026-05-24"
REQUEST_DELAY   = 0.5   # polite rate limit between paginated requests

# Expected feature counts from scoping report pre-verification
EXPECTED_COUNTS = {
    "ss_zoning":  369,
    "ss_flu":      33,
    "lehi_zoning": 585,
    "lehi_gp":     429,
    "em_zoning":   22184,
}

# Expected centroid bounds (city center lat/lon) for gross validation
CITY_CENTERS = {
    "ss_zoning":   (40.3488, -111.9038),
    "ss_flu":      (40.3488, -111.9038),
    "lehi_zoning": (40.3916, -111.8508),
    "lehi_gp":     (40.3916, -111.8508),
    "em_zoning":   (40.2891, -112.0025),
}
MAX_CENTROID_KM = 15.0

# ── Zone code normalization tables ────────────────────────────────────────────

# Saratoga Springs zone codes → canonical taxonomy value
SS_ZONE_NORM: dict[str, str] = {
    "A":     "Agriculture/Rural",
    "RA-5":  "Agriculture/Rural",
    "RR":    "Agriculture/Rural",
    "R1-40": "Residential-Low",
    "R1-20": "Residential-Low",
    "R1-10": "Residential-Low",
    "R1-9":  "Residential-Low",
    "R2-8":  "Residential-Medium",
    "R3-6":  "Residential-Medium",
    "MF-10": "Residential-High",
    "MF-14": "Residential-High",
    "MF-18": "Residential-High",
    "MR":    "Residential-High",
    "NC":    "Commercial-Neighborhood",
    "MU":    "Mixed-Use",
    "MW":    "Mixed-Use",
    "PC":    "Planned/Mixed-Use",
    "BP":    "Industrial/Flex",
    "IC":    "Industrial/Flex",
    "CC":    "Commercial-General",
    "RC":    "Commercial-General",
    "HC":    "Commercial-General",
    "OW":    "Industrial/Flex",
    "I":     "Industrial/Flex",
    "PSBL":  "Public/Institutional",
}

# Lehi zone codes → canonical taxonomy value (covers services5 Zone field)
LEHI_ZONE_NORM: dict[str, str] = {
    # Single-family residential (low density)
    "R-1-6":  "Residential-Low",
    "R-1-8":  "Residential-Medium",
    "R-1-10": "Residential-Low",
    "R-1-12": "Residential-Low",
    "R-1-15": "Residential-Low",
    "R-1-20": "Residential-Low",
    "R-1-40": "Residential-Low",
    # Two-family residential
    "R-2-6":  "Residential-Medium",
    "R-2-8":  "Residential-Medium",
    "R-2-10": "Residential-Medium",
    # Multifamily / attached
    "R-3":    "Residential-High",
    "R-6":    "Residential-High",
    "TH-5":   "Residential-Townhome",
    "TH-8":   "Residential-Townhome",
    "TH-12":  "Residential-Townhome",
    "R-MF":   "Residential-High",
    "MF-14":  "Residential-High",
    "MF-20":  "Residential-High",
    # Agricultural / rural
    "A-5":    "Agriculture/Rural",
    "A-20":   "Agriculture/Rural",
    "RA":     "Agriculture/Rural",
    "A-1":    "Agriculture/Rural",
    # Commercial
    "CC":     "Commercial-General",
    "NC":     "Commercial-Neighborhood",
    "HC":     "Commercial-General",
    "C-H":    "Commercial-General",
    "RC":     "Commercial-General",
    "CO":     "Commercial-Office",
    "C":      "Commercial-General",
    "SC":     "Commercial-General",
    "CR":     "Commercial-Recreation",
    # Mixed / planned
    "MU":     "Mixed-Use",
    "MXD":    "Mixed-Use",
    "PC":     "Planned/Mixed-Use",
    # Industrial / business park
    "BP":     "Industrial/Flex",
    "LI":     "Industrial/Flex",
    "HI":     "Industrial/Flex",
    "H/I":    "Industrial/Flex",
    "C-I":    "Industrial/Flex",
    "MFG":    "Industrial/Flex",
    "T-M":    "Industrial/Flex",
    "T/M":    "Industrial/Flex",
    # Public / institutional
    "PF":     "Public/Institutional",
    "OS":     "Open Space/Public",
    # TOD
    "TOD":    "Mixed-Use",
    # Additional codes found in services5 Lehi_Zoning (April 2026 upload)
    "R-1-22": "Residential-Low",    # single-family, 22k sqft min
    "R-1-Flex": "Residential-Low",  # flexible SFR district
    "R-2":    "Residential-Medium", # two-family residential
    "R-2.5":  "Residential-Medium", # two-family variant
    "RA-1":   "Agriculture/Rural",  # rural agricultural, 1-acre min
}

# Eagle Mountain General_Zoning coded domain values → canonical taxonomy value
EM_GENERAL_ZONE_NORM: dict[str, str] = {
    "Residential":             "Residential-Low",      # conservative: EM is mostly SFR
    "Agriculture":             "Agriculture/Rural",
    "Open Space":              "Open Space/Public",
    "Under Review":            "Other/Unknown",
    "Commercial":              "Commercial-General",
    "Business Park / Industrial":   "Industrial/Flex",
    "Business Park / Light Industry": "Industrial/Flex",
    "Other":                   "Other/Unknown",
}

# Lehi GP — 4 miscoded features identified in de-dup investigation
# Key: (Code, stripped_descriptio) → True if miscoded
LEHI_GP_MISCODED: set[tuple] = {
    (4,  "Light Density Residential"),   # Link=VLDR.jpg (Very Low Density), Code contradicts
    (4,  "Low Density Residential"),     # double-space typo version — after stripping becomes this
    (14, "Mixed-Use"),                   # Link=C.jpg (Commercial), outlier 1.89 ac
    (20, "Medium Density Residential"),  # Code=20 wrong; Link+Descriptio = MDR
}
# Note: (4, "Low Density Residential") covers BOTH the clean "Low Density Residential" features
# (29 legit) AND the double-space typo after stripping. The double-space one is flagged because
# its Link=HDR.jpg contradicts Code 4. We identify it by its original raw_descriptio with double space.
# So we need special handling: flag ONLY the double-space variant, not all 29 legit LDR features.
# Implementation: track the raw descriptio before stripping.
LEHI_GP_MISCODED_RAW: set[tuple] = {
    (4,  "Light Density Residential"),   # raw matches exactly
    (4,  "Low  Density Residential"),    # raw has double space — this specific one is miscoded
    (14, "Mixed-Use"),
    (20, "Medium Density Residential"),
}


# ── HTTP session ──────────────────────────────────────────────────────────────

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
    session.mount("http://",  adapter)
    session.headers.update({"User-Agent": "TooeleLandIntel/1.0"})
    return session


SESSION = make_session()


def fetch_layer_meta(url: str) -> dict:
    resp = SESSION.get(url, params={"f": "json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def resolve_coded_domain(meta: dict, field_name: str) -> dict[str, str]:
    """Return {code: label} for a codedValue domain on the given field."""
    for field in meta.get("fields", []):
        if field.get("name") == field_name:
            domain = field.get("domain") or {}
            if domain.get("type") == "codedValue":
                return {str(cv["code"]): cv["name"] for cv in domain.get("codedValues", [])}
    return {}


def query_all_features(url: str, out_fields: str, *, page_size: int = 1000) -> list[dict]:
    """Paginate through all features; request geometry in EPSG:4326."""
    all_features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where":             "1=1",
            "outFields":         out_fields,
            "returnGeometry":    "true",
            "outSR":             "4326",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
            "f":                 "json",
        }
        resp = SESSION.get(f"{url}/query", params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error from {url}: {data['error']}")
        features = data.get("features", [])
        all_features.extend(features)
        if not data.get("exceededTransferLimit", False) or len(features) < page_size:
            break
        offset += len(features)
        print(f"    … page offset={offset:,} ({len(all_features):,} so far)", flush=True)
        time.sleep(REQUEST_DELAY)
    return all_features


# ── Geometry conversion ───────────────────────────────────────────────────────

def rings_to_geojson(esri_geom: dict) -> dict | None:
    """Convert Esri polygon rings to GeoJSON Polygon/MultiPolygon."""
    rings = esri_geom.get("rings")
    if not rings:
        return None

    def signed_area(ring: list) -> float:
        n = len(ring)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += ring[i][0] * ring[j][1]
            area -= ring[j][0] * ring[i][1]
        return area / 2.0

    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": [rings[0]]}

    outers, holes = [], []
    for ring in rings:
        (outers if signed_area(ring) > 0 else holes).append(ring)

    if not outers:
        return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}
    if len(outers) == 1:
        return {"type": "Polygon", "coordinates": [outers[0]] + holes}

    polys = [[outer] for outer in outers]
    for hole in holes:
        polys[0].append(hole)
    return {"type": "MultiPolygon", "coordinates": polys}


# ── Validation helpers ────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def centroid_of_fc(features: list[dict]) -> tuple[float, float] | None:
    lats, lons = [], []
    for feat in features:
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


def validate_and_print(layer_key: str, features: list[dict]) -> bool:
    n = len(features)
    expected  = EXPECTED_COUNTS[layer_key]
    clat, clon = CITY_CENTERS[layer_key]
    centroid  = centroid_of_fc(features)
    dist_km   = haversine_km(clat, clon, centroid[0], centroid[1]) if centroid else float("inf")

    count_ok    = (n == expected)
    centroid_ok = (dist_km <= MAX_CENTROID_KM)

    print(f"\n{'='*55}", flush=True)
    print(f"  {layer_key.upper()}", flush=True)
    print(f"{'='*55}", flush=True)
    print(f"  Features:  {n:6,d}  (expected {expected:,})  {'PASS' if count_ok else 'WARN — count differs'}")
    print(f"  Centroid dist: {dist_km:.2f} km  {'PASS' if centroid_ok else 'FAIL'}")

    zone_counts: dict[str, int] = {}
    zone_key = "zone_code" if "zoning" in layer_key else "gp_zone_code"
    for feat in features:
        z = feat.get("properties", {}).get(zone_key, "")
        zone_counts[z] = zone_counts.get(z, 0) + 1
    print(f"  Zone types ({len(zone_counts)}):")
    for zone, cnt in sorted(zone_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"    {cnt:5d}  {zone!r}")
    if len(zone_counts) > 20:
        print(f"    … ({len(zone_counts) - 20} more)")

    return centroid_ok  # count can differ slightly if city updated data


# ── City ingest functions ─────────────────────────────────────────────────────

def ingest_ss_zoning() -> list[dict]:
    """Saratoga Springs current zoning — Zoning/MapServer/1, field ZONECLASS."""
    url = ("https://gis.saratogaspringscity.com/arcgisweb/rest/services"
           "/Planning/Zoning/MapServer/1")
    print("\nFetching SS Zoning metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print("Querying SS Zoning features (outSR=4326)...")
    raw = query_all_features(url, "ZONECLASS,ZONEDESC,CONDITIONS,LASTUPDATE")
    print(f"  Retrieved {len(raw)} features")

    features: list[dict] = []
    unmapped: set[str] = set()
    for feat in raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson(geom_raw)
        if not geom:
            continue
        attrs = feat.get("attributes", {})
        zone_code = (attrs.get("ZONECLASS") or "").strip()
        zone_desc = (attrs.get("ZONEDESC") or "").strip()
        conditions = (attrs.get("CONDITIONS") or "").strip() or None
        last_update = attrs.get("LASTUPDATE")
        norm = SS_ZONE_NORM.get(zone_code, "Other/Unknown")
        if norm == "Other/Unknown" and zone_code:
            unmapped.add(zone_code)
        props = {
            "zone_code":             zone_code,
            "zone_description":      zone_desc or zone_code,
            "zone_class_normalized": norm,
            "jurisdiction":          "saratoga_springs_ut",
            "source_rest_url":       url,
            "source_method":         "arcgis_rest",
            "extraction_method":     "arcgis_rest",
            "extraction_date":       EXTRACTION_DATE,
            "confidence":            "rest_api",
        }
        if conditions:
            props["zone_conditions"] = conditions
        if last_update is not None:
            props["last_update_epoch_ms"] = last_update
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    if unmapped:
        print(f"  WARN: unmapped SS zone codes (Other/Unknown): {sorted(unmapped)}")
    return features


def ingest_ss_flu() -> list[dict]:
    """Saratoga Springs FLU — LandUse/MapServer/2, field LANDUSEDESC."""
    url = ("https://gis.saratogaspringscity.com/arcgisweb/rest/services"
           "/Planning/LandUse/MapServer/2")
    flu_currency_note = (
        "Ord 25-75 Dec 2 2025 was Water Element only; FLU layer is current adopted"
    )
    print("\nFetching SS FLU metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print("Querying SS FLU features (outSR=4326)...")
    raw = query_all_features(url, "LANDUSEDESC")
    print(f"  Retrieved {len(raw)} features")

    features: list[dict] = []
    for feat in raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson(geom_raw)
        if not geom:
            continue
        attrs  = feat.get("attributes", {})
        lu_val = (attrs.get("LANDUSEDESC") or "").strip()
        props = {
            "city_name":          "Saratoga Springs",
            "city_slug":          "saratoga_springs",
            "gp_zone_code":       lu_val,
            "gp_zone_description": lu_val,
            "jurisdiction":       "Saratoga Springs City",
            "source_rest_url":    url,
            "source_method":      "arcgis_rest",
            "extraction_method":  "arcgis_rest",
            "extraction_date":    EXTRACTION_DATE,
            "future_layer_type":  "flu",
            "confidence":         "rest_api",
            "flu_currency_note":  flu_currency_note,
        }
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return features


def ingest_lehi_zoning() -> list[dict]:
    """Lehi current zoning — Lehi_Zoning FeatureServer/0 (services5), field Zone."""
    url = ("https://services5.arcgis.com/rObWD7PYeLl9jJPT/arcgis/rest/services"
           "/Lehi_Zoning/FeatureServer/0")
    print("\nFetching Lehi Zoning metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print("Querying Lehi Zoning features (outSR=4326)...")
    raw = query_all_features(url, "Zone,Acres")
    print(f"  Retrieved {len(raw)} features")

    features: list[dict] = []
    unmapped: set[str] = set()
    for feat in raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson(geom_raw)
        if not geom:
            continue
        attrs     = feat.get("attributes", {})
        zone_code = (attrs.get("Zone") or "").strip()
        acreage   = attrs.get("Acres")
        norm = LEHI_ZONE_NORM.get(zone_code)
        if norm is None:
            norm = "Other/Unknown"
            if zone_code:
                unmapped.add(zone_code)
        props = {
            "zone_code":             zone_code,
            "zone_description":      zone_code,
            "zone_class_normalized": norm,
            "jurisdiction":          "lehi_ut",
            "source_rest_url":       url,
            "source_method":         "arcgis_rest",
            "extraction_method":     "arcgis_rest",
            "extraction_date":       EXTRACTION_DATE,
            "confidence":            "rest_api",
        }
        if acreage is not None:
            props["acreage"] = acreage
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    if unmapped:
        print(f"  WARN: unmapped Lehi zone codes (Other/Unknown): {sorted(unmapped)}")
        print("  -> Add these to gp_taxonomy.yaml current_zoning.lehi for next D1 load.")
    return features


def ingest_lehi_gp() -> list[dict]:
    """Lehi GP — Lehi_General_Plan FeatureServer/0 (services5), field Descriptio.

    De-dup rules (pre-locked per PHASE_18b-2e_SCOPING.md de-dup investigation):
    - Normalize by Descriptio, not Code.
    - Strip double-space typo: 'Low  Density Residential' → 'Low Density Residential'.
    - Flag 4 miscoded features with provenance_note='miscoded_candidate_for_review'.
      Identification: raw_descriptio + Code pair in LEHI_GP_MISCODED_RAW set.
    """
    url = ("https://services5.arcgis.com/rObWD7PYeLl9jJPT/arcgis/rest/services"
           "/Lehi_General_Plan/FeatureServer/0")
    print("\nFetching Lehi GP metadata...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    print("Querying Lehi GP features (outSR=4326)...")
    raw = query_all_features(url, "Descriptio,Code,Acres,Link")
    print(f"  Retrieved {len(raw)} features")

    features: list[dict] = []
    miscoded_count = 0
    for feat in raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson(geom_raw)
        if not geom:
            continue
        attrs          = feat.get("attributes", {})
        raw_descriptio = (attrs.get("Descriptio") or "").strip()
        code           = attrs.get("Code")
        acreage        = attrs.get("Acres")
        link           = (attrs.get("Link") or "").strip() or None

        # Strip double-space typo (per scoping doc)
        descriptio = " ".join(raw_descriptio.split())

        # Identify miscoded features by (Code, raw_descriptio) pair
        is_miscoded = (code, raw_descriptio) in LEHI_GP_MISCODED_RAW
        if is_miscoded:
            miscoded_count += 1

        props = {
            "city_name":          "Lehi",
            "city_slug":          "lehi",
            "gp_zone_code":       descriptio,
            "gp_zone_description": descriptio,
            "jurisdiction":       "Lehi City",
            "source_rest_url":    url,
            "source_method":      "arcgis_rest",
            "extraction_method":  "arcgis_rest",
            "extraction_date":    EXTRACTION_DATE,
            "future_layer_type":  "gp",
            "confidence":         "rest_api",
            "lehi_gp_code":       code,
        }
        if raw_descriptio != descriptio:
            props["raw_descriptio"] = raw_descriptio
        if acreage is not None:
            props["acreage"] = acreage
        if link:
            props["lehi_gp_link"] = link
        if is_miscoded:
            props["provenance_note"] = "miscoded_candidate_for_review"

        features.append({"type": "Feature", "geometry": geom, "properties": props})

    print(f"  Flagged {miscoded_count} miscoded features with provenance_note")
    if miscoded_count != 4:
        print(f"  WARN: expected 4 miscoded features, got {miscoded_count} — verify de-dup logic")
    return features


def ingest_em_zoning() -> list[dict]:
    """Eagle Mountain zoning — EMC_Zoning_View FeatureServer/0 (services1), 22k parcel-level.

    Canonical zone_code = General_Zoning (coded domain, clean).
    Zoning (free text) preserved as zone_current_raw.
    Current_Landuse (coded domain) preserved as current_landuse.
    """
    url = ("https://services1.arcgis.com/OZXOaoaD8hmdOtqR/arcgis/rest/services"
           "/EMC_Zoning_View/FeatureServer/0")
    print("\nFetching Eagle Mountain Zoning metadata (coded domain resolution)...")
    meta = fetch_layer_meta(url)
    print(f"  Layer: {meta.get('name')} | SR: {meta.get('extent', {}).get('spatialReference')}")
    gen_zone_domain = resolve_coded_domain(meta, "General_Zoning")
    cur_lu_domain   = resolve_coded_domain(meta, "Current_Landuse")
    print(f"  General_Zoning domain ({len(gen_zone_domain)} values): {gen_zone_domain}")
    print(f"  Current_Landuse domain ({len(cur_lu_domain)} values): {cur_lu_domain}")
    print(f"Querying Eagle Mountain Zoning features (outSR=4326, 22k parcels — paginating)...")
    raw = query_all_features(
        url,
        "Zoning,General_Zoning,Current_Landuse,PARCELID,MDA_Name,Historic_Code,Municipal_Code",
        page_size=2000,
    )
    print(f"  Retrieved {len(raw)} features total")

    features: list[dict] = []
    unmapped_gen: set[str] = set()
    for feat in raw:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        geom = rings_to_geojson(geom_raw)
        if not geom:
            continue
        attrs = feat.get("attributes", {})

        # Resolve coded domains to labels
        gen_zone_code = str(attrs.get("General_Zoning", "") or "")
        gen_zone_label = gen_zone_domain.get(gen_zone_code, gen_zone_code) if gen_zone_code else ""

        cur_lu_code  = str(attrs.get("Current_Landuse", "") or "")
        cur_lu_label = cur_lu_domain.get(cur_lu_code, cur_lu_code) if cur_lu_code else ""

        zoning_raw   = (attrs.get("Zoning") or "").strip()
        parcel_id    = (attrs.get("PARCELID") or "").strip() or None
        mda_name     = (attrs.get("MDA_Name") or "").strip() or None
        historic_code = (attrs.get("Historic_Code") or "").strip() or None
        muni_code    = (attrs.get("Municipal_Code") or "").strip() or None

        # Normalize using the label, not the numeric code
        norm = EM_GENERAL_ZONE_NORM.get(gen_zone_label, "Other/Unknown")
        if norm == "Other/Unknown" and gen_zone_label:
            unmapped_gen.add(gen_zone_label)

        props = {
            "zone_code":             gen_zone_label,    # canonical: General_Zoning label
            "zone_description":      gen_zone_label,
            "zone_class_normalized": norm,
            "zone_current_raw":      zoning_raw or None, # free-text Zoning field preserved
            "current_landuse":       cur_lu_label or None,
            "jurisdiction":          "eagle_mountain_ut",
            "source_rest_url":       url,
            "source_method":         "arcgis_rest",
            "extraction_method":     "arcgis_rest",
            "extraction_date":       EXTRACTION_DATE,
            "confidence":            "rest_api",
        }
        if parcel_id:
            props["parcel_id"] = parcel_id
        if mda_name:
            props["mda_name"] = mda_name
        if historic_code:
            props["historic_code"] = historic_code
        if muni_code:
            props["municipal_code"] = muni_code

        features.append({"type": "Feature", "geometry": geom, "properties": props})

    if unmapped_gen:
        print(f"  WARN: unmapped General_Zoning labels (Other/Unknown): {sorted(unmapped_gen)}")
    return features


# ── Main ──────────────────────────────────────────────────────────────────────

def write_geojson(path: Path, features: list[dict]) -> None:
    fc = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    size_kb = path.stat().st_size // 1024
    print(f"  Written: {path}  ({len(features):,} features, {size_kb:,} KB)")


def main() -> None:
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    FUTURE_DIR.mkdir(parents=True, exist_ok=True)

    layers = [
        ("ss_zoning",   ingest_ss_zoning,   CURRENT_DIR / "saratoga_springs_ut_zoning.geojson"),
        ("ss_flu",      ingest_ss_flu,       FUTURE_DIR  / "saratoga_springs_gp.geojson"),
        ("lehi_zoning", ingest_lehi_zoning,  CURRENT_DIR / "lehi_ut_zoning.geojson"),
        ("lehi_gp",     ingest_lehi_gp,      FUTURE_DIR  / "lehi_gp.geojson"),
        ("em_zoning",   ingest_em_zoning,    CURRENT_DIR / "eagle_mountain_ut_zoning.geojson"),
    ]

    results: dict[str, dict] = {}
    for key, fn, out_path in layers:
        print(f"\n{'-'*55}", flush=True)
        try:
            features = fn()
            ok = validate_and_print(key, features)
            write_geojson(out_path, features)
            results[key] = {"ok": ok, "n": len(features), "path": str(out_path)}
        except Exception as exc:
            import traceback
            print(f"\nERROR extracting {key}: {exc}", file=sys.stderr)
            traceback.print_exc()
            results[key] = {"ok": False, "error": str(exc)}
        time.sleep(REQUEST_DELAY)

    print("\n\nEXTRACTION SUMMARY", flush=True)
    print("=" * 55, flush=True)
    all_ok = True
    for key, res in results.items():
        status = "PASS" if res.get("ok") else "FAIL"
        n = res.get("n", "ERR")
        print(f"  {key:15s}  {str(n):>7}  {status}")
        if not res.get("ok"):
            all_ok = False

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
