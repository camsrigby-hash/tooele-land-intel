"""
land_cover_analyzer.py — Tier 2: Satellite-Based Land Cover Classification
============================================================================
Downloads NAIP aerial imagery (1m resolution, 4-band RGBNIR) for scored
parcels, classifies land cover within each parcel boundary, and outputs a
vacancy confidence score based on what's actually on the ground.

This catches cases where UGRC data says "vacant" but there's a building,
or says "developed" but the structure has been demolished.

Classification approach:
    Uses spectral indices (NDVI, NDBI, BSI) from NAIP's 4 bands to classify
    each pixel as: vegetation, bare_soil, impervious_surface, or water.
    No ML training required — threshold-based spectral analysis.

Data source:
    NAIP via Microsoft Planetary Computer STAC catalog (free, no auth).
    Fallback: USDA NAIP WMS for older imagery.

Integration:
    Called after parcel_polygon_map.py (Tier 1). Enriches the GeoJSON with
    land_cover_* fields. Can also run standalone.

Usage:
    from land_cover_analyzer import analyze_land_cover
    enriched = analyze_land_cover(geojson_fc, county="davis")

    Or standalone:
    python land_cover_analyzer.py --input data/parcel_polygons.geojson --output data/land_cover_results.json

Dependencies:
    pip install requests numpy rasterio shapely Pillow --break-system-packages

New file — does NOT replace any existing module.
"""

import json
import logging
import time
import math
import argparse
import io
from pathlib import Path
from datetime import datetime

try:
    import numpy as np
except ImportError:
    raise ImportError("pip install numpy --break-system-packages")

try:
    import requests
except ImportError:
    raise ImportError("pip install requests")

# Optional — graceful degradation if not installed
try:
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from rasterio.io import MemoryFile
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

try:
    from shapely.geometry import shape, box, mapping
    from shapely.ops import transform
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Configuration ──────────────────────────────────────────────────────────────

# Microsoft Planetary Computer STAC (free, no auth required)
PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

# NAIP WMS fallback (USDA)
NAIP_WMS_URL = "https://gis.apfo.usda.gov/arcgis/services/NAIP/USDA_CONUS_PRIME/ImageServer/WMSServer"

# Classification thresholds (calibrated for Utah's arid/semi-arid landscape)
# These may need tuning — run on known parcels and adjust.
NDVI_VEGETATION_THRESHOLD = 0.25   # Above this = green vegetation
NDVI_BARE_SOIL_CEILING    = 0.15   # Below this with low brightness = bare soil
BRIGHTNESS_IMPERVIOUS     = 160    # High brightness + low NDVI = concrete/roof
BRIGHTNESS_BARE_SOIL      = 120    # Moderate brightness + low NDVI = bare dirt

# Pixel size for rasterization (meters)
ANALYSIS_PIXEL_SIZE = 1.0  # 1m matches NAIP resolution

# Request settings
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5
MAX_RETRIES = 3


# ── NAIP Image Acquisition ────────────────────────────────────────────────────

def get_naip_stac_url(bbox: list, target_year: int = None) -> str:
    """
    Query Planetary Computer STAC for the most recent NAIP scene covering
    the bounding box. Returns the asset URL for the RGBNIR image.

    bbox: [west, south, east, north] in WGS84
    """
    if target_year is None:
        target_year = datetime.now().year - 1  # NAIP typically 1-2 years behind

    search_url = f"{PLANETARY_COMPUTER_STAC}/search"

    # Search for NAIP items covering this bbox
    params = {
        "collections": ["naip"],
        "bbox": bbox,
        "datetime": f"{target_year - 3}-01-01T00:00:00Z/{target_year + 1}-12-31T23:59:59Z",
        "limit": 5,
        "sortby": [{"field": "datetime", "direction": "desc"}],
    }

    try:
        resp = requests.post(search_url, json=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        results = resp.json()

        features = results.get("features", [])
        if not features:
            log.warning(f"No NAIP imagery found for bbox {bbox}")
            return None

        # Get the most recent item
        item = features[0]
        # NAIP items have an 'image' asset with the COG URL
        assets = item.get("assets", {})

        # Try common asset keys
        for key in ["image", "visual", "data"]:
            if key in assets:
                href = assets[key].get("href")
                if href:
                    log.info(f"Found NAIP image: {item['id']} ({item.get('properties', {}).get('datetime', 'unknown date')})")
                    return href

        log.warning(f"NAIP item found but no image asset: {item['id']}")
        return None

    except Exception as e:
        log.warning(f"STAC query failed: {e}")
        return None


def fetch_naip_wms_tile(bbox: list, width: int = 256, height: int = 256) -> np.ndarray:
    """
    Fallback: Fetch NAIP imagery via USDA WMS service.
    Returns numpy array of shape (height, width, 4) for RGBNIR.
    Falls back to RGB-only (3 bands) if NIR unavailable.
    """
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": "0",
        "SRS": "EPSG:4326",
        "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "WIDTH": width,
        "HEIGHT": height,
        "FORMAT": "image/tiff",
    }

    try:
        resp = requests.get(NAIP_WMS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        if HAS_RASTERIO:
            with MemoryFile(resp.content) as memfile:
                with memfile.open() as dataset:
                    bands = dataset.read()  # shape: (bands, height, width)
                    return np.transpose(bands, (1, 2, 0))  # → (height, width, bands)
        elif HAS_PIL:
            img = Image.open(io.BytesIO(resp.content))
            return np.array(img)
        else:
            log.warning("Neither rasterio nor PIL available for image parsing")
            return None

    except Exception as e:
        log.warning(f"WMS fetch failed: {e}")
        return None


def fetch_naip_cog_window(cog_url: str, bbox: list, pixel_size: float = 1.0) -> np.ndarray:
    """
    Read a window from a Cloud Optimized GeoTIFF (COG) covering the parcel bbox.
    This is the preferred method — reads only the pixels needed.
    Requires rasterio with GDAL COG support.
    """
    if not HAS_RASTERIO:
        log.warning("rasterio not available — falling back to WMS")
        return None

    try:
        with rasterio.open(cog_url) as src:
            # Convert WGS84 bbox to pixel window
            from rasterio.windows import from_bounds as window_from_bounds
            window = window_from_bounds(
                bbox[0], bbox[1], bbox[2], bbox[3],
                transform=src.transform,
            )
            # Read the window (all bands)
            data = src.read(window=window)  # shape: (bands, height, width)
            return np.transpose(data, (1, 2, 0))  # → (height, width, bands)

    except Exception as e:
        log.warning(f"COG read failed for {cog_url}: {e}")
        return None


# ── Spectral Classification ───────────────────────────────────────────────────

def classify_pixels(image: np.ndarray, has_nir: bool = True) -> np.ndarray:
    """
    Classify each pixel into land cover categories using spectral indices.

    Input: numpy array (height, width, bands) where bands = RGBNIR (4) or RGB (3).
    Output: numpy array (height, width) with class codes:
        0 = no_data
        1 = vegetation (green plants, crops, lawns)
        2 = bare_soil (dirt, gravel, cleared land)
        3 = impervious (concrete, asphalt, rooftops)
        4 = water
        5 = shadow / ambiguous
    """
    h, w = image.shape[:2]
    classification = np.zeros((h, w), dtype=np.uint8)

    # Extract bands as float
    red   = image[:, :, 0].astype(np.float32)
    green = image[:, :, 1].astype(np.float32)
    blue  = image[:, :, 2].astype(np.float32)

    if has_nir and image.shape[2] >= 4:
        nir = image[:, :, 3].astype(np.float32)
    else:
        # Approximate NIR from visible bands (less accurate but functional)
        # Green channel correlates with vegetation reflectance
        nir = green * 1.3  # Rough approximation
        has_nir = False

    # ── Spectral indices ───────────────────────────────────────────────────
    # Avoid division by zero
    eps = 1e-6

    # NDVI: Normalized Difference Vegetation Index
    # High NDVI (>0.25) = healthy vegetation
    ndvi = (nir - red) / (nir + red + eps)

    # Brightness (mean of visible bands)
    brightness = (red + green + blue) / 3.0

    # NDWI: Normalized Difference Water Index (Green - NIR) / (Green + NIR)
    ndwi = (green - nir) / (green + nir + eps)

    # Bare Soil Index: ((Red + SWIR) - (NIR + Blue)) / ((Red + SWIR) + (NIR + Blue))
    # Without SWIR, approximate: ((Red + Blue) - (NIR + Green)) / (...)
    bsi_num = (red + blue) - (nir + green)
    bsi_den = (red + blue) + (nir + green) + eps
    bsi = bsi_num / bsi_den

    # ── Classification rules (order matters — first match wins) ────────────
    # No data (black pixels)
    no_data_mask = (brightness < 5)
    classification[no_data_mask] = 0

    # Water (high NDWI, low brightness)
    water_mask = (ndwi > 0.3) & (brightness < 100) & ~no_data_mask
    classification[water_mask] = 4

    # Vegetation (high NDVI)
    veg_mask = (ndvi > NDVI_VEGETATION_THRESHOLD) & ~no_data_mask & ~water_mask
    classification[veg_mask] = 1

    # Impervious surface (low NDVI, high brightness = concrete, rooftops, asphalt)
    imperv_mask = (
        (ndvi < NDVI_BARE_SOIL_CEILING) &
        (brightness > BRIGHTNESS_IMPERVIOUS) &
        ~no_data_mask & ~water_mask & ~veg_mask
    )
    classification[imperv_mask] = 3

    # Bare soil (low NDVI, moderate brightness, higher BSI)
    bare_mask = (
        (ndvi < NDVI_BARE_SOIL_CEILING) &
        (brightness > 40) &
        (brightness <= BRIGHTNESS_IMPERVIOUS) &
        ~no_data_mask & ~water_mask & ~veg_mask & ~imperv_mask
    )
    classification[bare_mask] = 2

    # Dark impervious (asphalt — low brightness, low NDVI, not water)
    dark_imperv = (
        (ndvi < 0.1) &
        (brightness > 20) & (brightness <= 80) &
        (ndwi < 0.1) &
        ~no_data_mask & ~water_mask & ~veg_mask
    )
    classification[dark_imperv] = 3

    # Everything else = shadow/ambiguous
    remaining = (classification == 0) & ~no_data_mask
    classification[remaining] = 5

    return classification


def compute_land_cover_stats(classification: np.ndarray) -> dict:
    """
    Compute percentage breakdown of land cover classes within the parcel.
    """
    total_pixels = np.sum(classification > 0)  # Exclude no_data
    if total_pixels == 0:
        return {
            "vegetation_pct": 0, "bare_soil_pct": 0,
            "impervious_pct": 0, "water_pct": 0,
            "shadow_pct": 0, "total_pixels": 0,
            "has_data": False,
        }

    return {
        "vegetation_pct":  round(np.sum(classification == 1) / total_pixels * 100, 1),
        "bare_soil_pct":   round(np.sum(classification == 2) / total_pixels * 100, 1),
        "impervious_pct":  round(np.sum(classification == 3) / total_pixels * 100, 1),
        "water_pct":       round(np.sum(classification == 4) / total_pixels * 100, 1),
        "shadow_pct":      round(np.sum(classification == 5) / total_pixels * 100, 1),
        "total_pixels":    int(total_pixels),
        "has_data":        True,
    }


def compute_vacancy_confidence(stats: dict, ugrc_vacancy: str = "unknown") -> dict:
    """
    Translate land cover stats into a vacancy confidence score.
    Combines satellite evidence with UGRC's attribute-based classification.

    Returns:
        lc_vacancy_confidence: 0.0-1.0 (satellite-only)
        combined_vacancy_confidence: 0.0-1.0 (satellite + UGRC)
        lc_vacancy_status: string classification
    """
    if not stats.get("has_data"):
        return {
            "lc_vacancy_confidence": 0.0,
            "combined_vacancy_confidence": 0.0,
            "lc_vacancy_status": "no_imagery",
            "lc_note": "No satellite imagery available for this parcel",
        }

    bare  = stats["bare_soil_pct"]
    veg   = stats["vegetation_pct"]
    imperv = stats["impervious_pct"]
    undeveloped = bare + veg  # Total non-impervious land

    # ── Satellite-only vacancy confidence ──────────────────────────────────
    if imperv < 5 and undeveloped > 85:
        lc_conf = 0.95
        lc_status = "confirmed_vacant"
        note = f"Satellite confirms vacant: {bare:.0f}% bare soil, {veg:.0f}% vegetation, <5% impervious"
    elif imperv < 15 and undeveloped > 70:
        lc_conf = 0.80
        lc_status = "likely_vacant"
        note = f"Mostly undeveloped: {undeveloped:.0f}% bare/vegetation, {imperv:.0f}% impervious"
    elif imperv < 30 and undeveloped > 50:
        lc_conf = 0.55
        lc_status = "partially_developed"
        note = f"Partially developed: {imperv:.0f}% impervious — may have small structures or gravel"
    elif imperv >= 30 and imperv < 60:
        lc_conf = 0.25
        lc_status = "substantially_developed"
        note = f"Substantial impervious cover ({imperv:.0f}%) — likely has buildings/parking"
    else:
        lc_conf = 0.05
        lc_status = "fully_developed"
        note = f"Heavily developed: {imperv:.0f}% impervious surface"

    # ── Combine with UGRC attribute classification ─────────────────────────
    # UGRC and satellite agreeing = high confidence
    # UGRC and satellite disagreeing = flag for manual review
    ugrc_weight = 0.4
    sat_weight = 0.6

    ugrc_conf_map = {
        "vacant": 0.95,
        "ag_vacant": 0.85,
        "ag_improved": 0.60,
        "underutilized": 0.50,
        "developed_new": 0.05,
        "developed_old": 0.15,
        "unknown": 0.50,
    }
    ugrc_conf = ugrc_conf_map.get(ugrc_vacancy, 0.50)
    combined = (ugrc_conf * ugrc_weight) + (lc_conf * sat_weight)

    # Flag disagreements
    disagreement = abs(ugrc_conf - lc_conf) > 0.4
    if disagreement:
        note += f" ⚠️ UGRC says '{ugrc_vacancy}' but satellite disagrees — MANUAL REVIEW"

    return {
        "lc_vacancy_confidence": round(lc_conf, 2),
        "combined_vacancy_confidence": round(combined, 2),
        "lc_vacancy_status": lc_status,
        "lc_note": note,
        "lc_disagreement": disagreement,
        "lc_vegetation_pct": stats["vegetation_pct"],
        "lc_bare_soil_pct": stats["bare_soil_pct"],
        "lc_impervious_pct": stats["impervious_pct"],
    }


# ── Per-Parcel Analysis ───────────────────────────────────────────────────────

def analyze_single_parcel(geometry: dict, parcel_id: str,
                          ugrc_vacancy: str = "unknown",
                          naip_cog_url: str = None) -> dict:
    """
    Analyze a single parcel's land cover from satellite imagery.

    Args:
        geometry: GeoJSON geometry (Polygon or MultiPolygon) in WGS84
        parcel_id: For logging
        ugrc_vacancy: Vacancy status from Tier 1 classification
        naip_cog_url: Pre-fetched COG URL (avoids redundant STAC queries)

    Returns:
        Dict with land cover stats and vacancy confidence
    """
    if not HAS_SHAPELY:
        log.error("shapely required for parcel analysis — pip install shapely")
        return {"lc_vacancy_status": "error", "lc_note": "shapely not installed"}

    try:
        parcel_shape = shape(geometry)
        bbox = list(parcel_shape.bounds)  # [west, south, east, north]
    except Exception as e:
        log.warning(f"Invalid geometry for {parcel_id}: {e}")
        return {"lc_vacancy_status": "error", "lc_note": f"Invalid geometry: {e}"}

    # Calculate pixel dimensions for this parcel
    # Approximate meters per degree at Utah's latitude (~41°N)
    lat_center = (bbox[1] + bbox[3]) / 2
    m_per_deg_lon = 111320 * math.cos(math.radians(lat_center))
    m_per_deg_lat = 110540

    width_m  = (bbox[2] - bbox[0]) * m_per_deg_lon
    height_m = (bbox[3] - bbox[1]) * m_per_deg_lat
    width_px  = max(int(width_m / ANALYSIS_PIXEL_SIZE), 10)
    height_px = max(int(height_m / ANALYSIS_PIXEL_SIZE), 10)

    # Cap at 500px to avoid huge downloads for large parcels
    if width_px > 500:
        scale = 500 / width_px
        width_px = 500
        height_px = int(height_px * scale)
    if height_px > 500:
        scale = 500 / height_px
        height_px = 500
        width_px = int(width_px * scale)

    # ── Fetch imagery ──────────────────────────────────────────────────────
    image = None
    has_nir = True

    # Method 1: COG from Planetary Computer (preferred)
    if naip_cog_url and HAS_RASTERIO:
        image = fetch_naip_cog_window(naip_cog_url, bbox)

    # Method 2: WMS fallback
    if image is None:
        image = fetch_naip_wms_tile(bbox, width=width_px, height=height_px)
        if image is not None and image.shape[2] == 3:
            has_nir = False

    if image is None:
        return {
            "lc_vacancy_status": "no_imagery",
            "lc_note": "Could not fetch satellite imagery for this parcel",
            "lc_vacancy_confidence": 0.0,
            "combined_vacancy_confidence": 0.0,
        }

    # ── Mask to parcel boundary ────────────────────────────────────────────
    # Create a raster mask of the parcel polygon so we only classify pixels
    # that fall inside the parcel (not neighboring land)
    if HAS_RASTERIO:
        try:
            h, w = image.shape[:2]
            transform_affine = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], w, h)
            mask = rasterize(
                [(mapping(parcel_shape), 1)],
                out_shape=(h, w),
                transform=transform_affine,
                fill=0,
                dtype=np.uint8,
            )
        except Exception:
            mask = np.ones(image.shape[:2], dtype=np.uint8)
    else:
        mask = np.ones(image.shape[:2], dtype=np.uint8)

    # ── Classify ───────────────────────────────────────────────────────────
    classification = classify_pixels(image, has_nir=has_nir)

    # Apply parcel mask (set pixels outside parcel to 0 = no_data)
    classification[mask == 0] = 0

    # ── Compute stats ──────────────────────────────────────────────────────
    stats = compute_land_cover_stats(classification)
    result = compute_vacancy_confidence(stats, ugrc_vacancy)
    result["parcel_id"] = parcel_id

    return result


# ── Batch Analysis ─────────────────────────────────────────────────────────────

def analyze_land_cover(geojson_fc: dict, max_parcels: int = 200) -> dict:
    """
    Analyze land cover for all features in a GeoJSON FeatureCollection.
    Enriches each feature's properties with land_cover_* fields.

    Args:
        geojson_fc: GeoJSON FeatureCollection (output of Tier 1)
        max_parcels: Safety cap

    Returns:
        Enriched GeoJSON FeatureCollection
    """
    features = geojson_fc.get("features", [])
    if len(features) > max_parcels:
        log.info(f"Capping analysis from {len(features)} to {max_parcels} parcels")
        # Prioritize by score, then vacancy confidence
        features.sort(
            key=lambda f: (
                f.get("properties", {}).get("score", 0),
                f.get("properties", {}).get("vacancy_confidence", 0),
            ),
            reverse=True,
        )
        features = features[:max_parcels]

    log.info(f"Analyzing land cover for {len(features)} parcels...")

    # Pre-fetch NAIP COG URL for the study area (one query covers all parcels)
    # Compute overall bounding box
    all_coords = []
    for f in features:
        try:
            s = shape(f["geometry"])
            all_coords.extend(list(s.bounds[::2]))  # west, east
            all_coords.extend(list(s.bounds[1::2]))  # south, north
        except Exception:
            pass

    naip_cog_url = None
    if all_coords and HAS_SHAPELY:
        overall_bbox = [
            min(all_coords[::2]),   # west
            min(all_coords[1::2]),  # south
            max(all_coords[::2]),   # east
            max(all_coords[1::2]), # north
        ]
        # Clamp to reasonable Utah extent
        overall_bbox = [
            max(overall_bbox[0], -113.0),
            max(overall_bbox[1], 40.0),
            min(overall_bbox[2], -111.0),
            min(overall_bbox[3], 42.5),
        ]
        naip_cog_url = get_naip_stac_url(overall_bbox)

    # Analyze each parcel
    analyzed = 0
    for i, feature in enumerate(features):
        pid = feature.get("properties", {}).get("PARCEL_ID", f"unknown_{i}")
        ugrc_vac = feature.get("properties", {}).get("vacancy_status", "unknown")
        geometry = feature.get("geometry")

        if not geometry:
            continue

        log.info(f"  [{i+1}/{len(features)}] Analyzing {pid}...")

        result = analyze_single_parcel(
            geometry=geometry,
            parcel_id=pid,
            ugrc_vacancy=ugrc_vac,
            naip_cog_url=naip_cog_url,
        )

        # Merge results into feature properties
        feature["properties"].update(result)
        analyzed += 1

        time.sleep(REQUEST_DELAY)

    log.info(f"✅ Land cover analysis complete: {analyzed}/{len(features)} parcels analyzed")

    # Summary stats
    confirmed = sum(1 for f in features
                    if f.get("properties", {}).get("lc_vacancy_status") == "confirmed_vacant")
    disagreements = sum(1 for f in features
                        if f.get("properties", {}).get("lc_disagreement", False))
    log.info(f"   Confirmed vacant by satellite: {confirmed}")
    log.info(f"   UGRC/satellite disagreements (manual review): {disagreements}")

    geojson_fc["features"] = features
    return geojson_fc


# ── Income-Adjusted Scoring Hook ──────────────────────────────────────────────

"""
INCOME SCORING NOTE FOR parcel_scorer.py INTEGRATION
=====================================================
Gas station / C-store sites perform BETTER in lower-income areas because:
  - C-store purchases are high-frequency, low-dollar (fountain drinks, snacks, tobacco)
  - Lower-income households make more frequent short trips vs. weekly big-box runs
  - Fuel purchase is income-agnostic (everyone needs gas)

Scoring adjustment for parcel_scorer.py (spec for Manus):

  GAS STATION MODE:
    median_hhi < $45k   → income_score = 90  (high C-store capture)
    median_hhi $45k-65k → income_score = 80  (solid)
    median_hhi $65k-90k → income_score = 70  (good fuel, less C-store)
    median_hhi > $90k   → income_score = 60  (premium fuel, low C-store margin)

  MINIFLEX MODE (traditional retail / services):
    median_hhi < $45k   → income_score = 50  (limited discretionary spend)
    median_hhi $45k-65k → income_score = 70
    median_hhi $65k-90k → income_score = 85
    median_hhi > $90k   → income_score = 95  (high disposable income)

  Data source: Census ACS 5-Year Estimates, Table B19013
  API: https://api.census.gov/data/{year}/acs/acs5?get=B19013_001E&for=block+group:*&in=state:49+county:{fips}
  Utah FIPS: State=49, Davis County=011, Weber County=057
  Free, no API key required (but key available for higher rate limits).
"""


def fetch_block_group_income(lat: float, lon: float, year: int = 2023) -> float:
    """
    Fetch median household income for the Census block group containing
    the given lat/lon. Uses the Census Geocoder to identify the block group,
    then queries ACS for income.

    Returns median HHI as float, or None if unavailable.
    """
    # Step 1: Geocode lat/lon to Census block group
    geocode_url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
    params = {
        "x": lon, "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }

    try:
        resp = requests.get(geocode_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        geographies = data.get("result", {}).get("geographies", {})
        block_groups = geographies.get("Census Block Groups", [])
        if not block_groups:
            return None

        bg = block_groups[0]
        state  = bg.get("STATE")
        county = bg.get("COUNTY")
        tract  = bg.get("TRACT")
        blkgrp = bg.get("BLKGRP")

    except Exception as e:
        log.warning(f"Census geocode failed for ({lat}, {lon}): {e}")
        return None

    # Step 2: Query ACS for median HHI
    acs_url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {
        "get": "B19013_001E",  # Median household income
        "for": f"block group:{blkgrp}",
        "in": f"state:{state} county:{county} tract:{tract}",
    }

    try:
        resp = requests.get(acs_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Response format: [['B19013_001E', 'state', 'county', 'tract', 'block group'],
        #                    ['65000', '49', '011', '120100', '1']]
        if len(data) >= 2:
            income_str = data[1][0]
            if income_str and income_str != '-666666666':  # Census null marker
                return float(income_str)

    except Exception as e:
        log.warning(f"ACS query failed: {e}")

    return None


def score_income_for_mode(median_hhi: float, mode: str = "gas_station") -> dict:
    """
    Score parcel based on surrounding median household income.
    Returns dict with income_score and income_note.
    """
    if median_hhi is None:
        return {"income_score": 70, "income_note": "Income data unavailable (default score)"}

    hhi_k = median_hhi / 1000  # Express in thousands for readability

    if mode == "gas_station":
        # C-store performs better in lower income areas
        if median_hhi < 45000:
            score = 90
            note = f"${hhi_k:.0f}k median HHI — strong C-store capture potential"
        elif median_hhi < 65000:
            score = 80
            note = f"${hhi_k:.0f}k median HHI — solid fuel + C-store market"
        elif median_hhi < 90000:
            score = 70
            note = f"${hhi_k:.0f}k median HHI — good fuel volume, moderate C-store"
        else:
            score = 60
            note = f"${hhi_k:.0f}k median HHI — premium fuel area, lower C-store margin"
    else:
        # Miniflex / traditional retail benefits from higher income
        if median_hhi < 45000:
            score = 50
            note = f"${hhi_k:.0f}k median HHI — limited discretionary spend for retail"
        elif median_hhi < 65000:
            score = 70
            note = f"${hhi_k:.0f}k median HHI — moderate retail market"
        elif median_hhi < 90000:
            score = 85
            note = f"${hhi_k:.0f}k median HHI — strong retail spending potential"
        else:
            score = 95
            note = f"${hhi_k:.0f}k median HHI — high disposable income, premium retail"

    return {"income_score": score, "income_note": note, "median_hhi": median_hhi}


# ── CLI Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tier 2: Land Cover Analysis for Parcel Vacancy Verification"
    )
    parser.add_argument("--input", required=True,
                        help="GeoJSON FeatureCollection from Tier 1 polygon map")
    parser.add_argument("--output", default="data/land_cover_results.json",
                        help="Output path for enriched GeoJSON")
    parser.add_argument("--max-parcels", type=int, default=200,
                        help="Maximum parcels to analyze (imagery downloads)")
    parser.add_argument("--mode", default="gas_station",
                        choices=["gas_station", "miniflex"],
                        help="Scoring mode (affects income scoring)")
    parser.add_argument("--include-income", action="store_true",
                        help="Also fetch Census income data (adds ~1s per parcel)")
    args = parser.parse_args()

    # Load GeoJSON
    with open(args.input, "r") as f:
        geojson = json.load(f)

    # Run land cover analysis
    enriched = analyze_land_cover(geojson, max_parcels=args.max_parcels)

    # Optionally add income scoring
    if args.include_income:
        log.info("Fetching Census income data...")
        for feature in enriched.get("features", []):
            props = feature.get("properties", {})
            # Use centroid for income lookup
            try:
                geom = shape(feature["geometry"])
                centroid = geom.centroid
                income = fetch_block_group_income(centroid.y, centroid.x)
                income_result = score_income_for_mode(income, args.mode)
                props.update(income_result)
                feature["properties"] = props
            except Exception as e:
                log.warning(f"Income lookup failed for {props.get('PARCEL_ID')}: {e}")
            time.sleep(0.5)  # Census API rate limiting

    # Save output
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)

    log.info(f"✅ Results saved to {args.output}")

    # Print summary
    features = enriched.get("features", [])
    statuses = {}
    for feat in features:
        s = feat.get("properties", {}).get("lc_vacancy_status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"\nLand Cover Analysis Summary ({len(features)} parcels):")
    for status, count in sorted(statuses.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")
