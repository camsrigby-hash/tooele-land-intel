"""
gp_raster_sample_extract.py — Phase 18b-2d raster-overlay zoning extraction.

Supersedes 18b-2c's vector polygon tracing (`gp_pdf_extract.py`) for PDF cities
where Claude vision boundary tracing is too fragile (satellite-basemap maps,
large-format prints, low contrast between adjacent zones).

5-stage pipeline:
  1. PDF page rasterization (PyMuPDF / pdf2image, 300 DPI)
  2. Georeference — hybrid manual CPs OR auto Stage 2+3 + SD-18 gate
     (reuses helpers from gp_pdf_extract.py)
  3. Legend extraction — Claude vision crops legend region, reads color↔label pairs
  4. Per-parcel color sampling — project parcel centroid to pixel, sample 5x5 LAB
     window, nearest-match against legend palette (with edge-case cascade to
     `representative_point()` if centroid lands on white/road)
  5. Output — per-parcel CSV (parcel_id → zone) + GeoJSON (per-parcel features)

Architecture (per SD-20, 2026-05-16):
  This pipeline produces per-parcel zone labels directly — the deliverable
  Wasatch Intel actually needs for scoring. Vector polygon tracing produced
  fragmentary polygons (8-call cap missing zones) and brittle boundaries.
  Raster sampling captures all zones automatically.

Spanish Fork stays on 18b-2c (gp_pdf_extract.py). Herriman + Erda + future PDF
cities use this pipeline.

Usage:
  python scripts/gp_raster_sample_extract.py --city herriman \
      --pdf data/_pdf_cache/herriman/Herriman_GP_Amendment.pdf \
      --map-page 33 \
      --manual-cps data/zoning/future/herriman_manual_cps.json \
      --extra-props data/_pdf_cache/herriman/_extra_props.json \
      --out data/zoning/future/
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import anthropic
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, MultiPolygon, mapping, shape as shp_shape
from shapely.validation import make_valid

try:
    from dotenv import load_dotenv
    # override=True so an empty inherited ANTHROPIC_API_KEY (e.g. shell with
    # `ANTHROPIC_API_KEY=`) doesn't shadow the real value in .env.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
except ImportError:
    pass

# Reuse helpers from the 18b-2c pipeline (do NOT modify that file).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gp_pdf_extract as gpe

csv.field_size_limit(10_000_000)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPUS_MODEL = "claude-opus-4-7"

# LAB-space color distance threshold for "unknown" classification.
# DeltaE values: < 2 imperceptible, 2-10 perceptible, 10-50 large.
# White/road pixels typically land far from any zone swatch -> trigger fallback.
LAB_DISTANCE_THRESHOLD = 35.0

# 5x5 window around the sampled pixel for averaging (per spec).
SAMPLE_WINDOW = 5
SAMPLE_HALF = SAMPLE_WINDOW // 2

# Counties that contain each city. Drives parcel CSV selection.
CITY_TO_COUNTY = {
    "herriman": "salt_lake",
    "erda": "tooele",
    "spanish_fork": "utah",
}

# Repository root used to resolve data/raw/parcels_<county>.csv(.gz).
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Stage 1 — Rasterize selected PDF page
# ---------------------------------------------------------------------------

def stage1_rasterize_page(pdf_path: Path, page_idx: int, dpi: int = 300) -> Path:
    """Rasterize the PDF, return the path to the requested page image.

    Reuses gp_pdf_extract.stage1_rasterize which handles fitz/pdf2image fallback,
    size validation, and 5MB Anthropic-API resize cap.
    """
    pages = gpe.stage1_rasterize(pdf_path, dpi=dpi)
    if page_idx >= len(pages):
        raise IndexError(
            f"Requested map-page index {page_idx} but only {len(pages)} valid pages produced."
        )
    chosen = pages[page_idx]
    logging.info(f"Stage 1: chose page index {page_idx} → {chosen.name}")
    return chosen


# ---------------------------------------------------------------------------
# Stage 2 — Georeference (hybrid manual / auto CPs)
# ---------------------------------------------------------------------------

def stage2_georeference(
    client: anthropic.Anthropic,
    city_cfg: dict,
    map_image_path: Path,
    manual_cps_path: Optional[Path],
    rmse_threshold_ft: float,
) -> tuple[np.ndarray, float, list[dict], dict]:
    """Return (A, rmse_ft, control_points, source_info).

    A is 2×3: maps [px_x, px_y, 1] -> [lon, lat].

    If `manual_cps_path` is provided, load CPs directly (skip auto Stage 2+3).
    Otherwise run auto: Stage 2 vision + Stage 3 OSM lookup with SD-18 gate.

    `source_info` dict reports `cp_source` ("manual_file" | "auto"), `cp_count`,
    `cp_source_path` (if manual).
    """
    if manual_cps_path is not None:
        if not manual_cps_path.exists():
            raise FileNotFoundError(f"Manual CPs file not found: {manual_cps_path}")
        with manual_cps_path.open() as f:
            raw_cps = json.load(f)
        logging.info(
            f"Stage 2: loaded {len(raw_cps)} manual CPs from {manual_cps_path}"
        )
        # Normalize to gp_pdf_extract.stage4_fit_affine schema
        control_points = []
        for c in raw_cps:
            control_points.append({
                "px_x": float(c["px_x"]),
                "px_y": float(c["px_y"]),
                "street_a": c.get("label", "manual").split("_x_")[0] if "label" in c else "manual_a",
                "street_b": c.get("label", "manual").split("_x_")[1] if "label" in c and "_x_" in c.get("label", "") else "manual_b",
                "conf": "high",
                "gt_lat": float(c["gt_lat"]),
                "gt_lon": float(c["gt_lon"]),
                "overpass_node_count": 0,
                "intersection_lookup_failed": False,
            })
        source_info = {
            "cp_source": "manual_file",
            "cp_count": len(control_points),
            "cp_source_path": str(manual_cps_path),
        }
    else:
        # Auto path: reuse 18b-2c Stage 2 vision + Stage 3 SD-18-gated lookup.
        page_idx = int(map_image_path.stem.split("_page_")[-1])
        candidates, _ = gpe.stage2_identify_control_points(
            client,
            [map_image_path],
            max_pages_to_try=1,
            map_page_hints=[page_idx],
            city_cfg=city_cfg,
        )
        control_points = gpe.stage3_ground_truth_lookup(candidates, city_cfg)
        if len(control_points) < 3:
            raise gpe.ControlPointError(
                f"Auto path produced only {len(control_points)} CPs after SD-18 gate. "
                "Supply --manual-cps."
            )
        source_info = {
            "cp_source": "auto",
            "cp_count": len(control_points),
            "cp_source_path": None,
        }

    A, rotation_deg = gpe.stage4_fit_affine(control_points)
    rmse_ft, annotated = gpe.stage5_validate(A, control_points, rmse_threshold_ft=rmse_threshold_ft)
    source_info["rotation_deg"] = rotation_deg
    source_info["rmse_ft"] = rmse_ft
    return A, rmse_ft, annotated, source_info


def write_geotiff(
    map_image_path: Path,
    A: np.ndarray,
    out_path: Path,
) -> Path:
    """Write a GeoTIFF with the affine transform baked in. EPSG:4326.

    The Affine in rasterio maps (col, row) -> (x, y) = (lon, lat).
    Our A maps [px_x, px_y, 1] -> [lon, lat], so the params align directly:
      Affine(a, b, c, d, e, f)
        x = a*col + b*row + c
        y = d*col + e*row + f
    """
    import rasterio
    from rasterio.transform import Affine

    img = Image.open(map_image_path).convert("RGB")
    arr = np.array(img)  # shape: (H, W, 3)
    h, w, _ = arr.shape

    transform = Affine(
        A[0, 0], A[0, 1], A[0, 2],
        A[1, 0], A[1, 1], A[1, 2],
    )
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        # rasterio expects (bands, H, W) ordering
        dst.write(arr.transpose(2, 0, 1))
    logging.info(f"Stage 2: wrote GeoTIFF {out_path} ({h}×{w})")
    return out_path


# ---------------------------------------------------------------------------
# Stage 3 — Legend extraction (2 vision calls; cached)
# ---------------------------------------------------------------------------

LEGEND_BBOX_PROMPT_TEMPLATE = """\
This is a city general plan / future land use map.

The image is exactly {img_w} pixels wide and {img_h} pixels tall.

Find the bounding box of the map legend — the box containing color swatches and
zone-name labels. Most legends are in a corner of the map (often bottom-right or
bottom-left), enclosed in a rectangle. If the legend is split into multiple boxes,
return the bbox that tightly encloses ALL of them.

Coordinates: (0, 0) = top-left corner, ({img_w}, {img_h}) = bottom-right.
ALL coordinates MUST satisfy: 0 <= x_min < x_max <= {img_w} and 0 <= y_min < y_max <= {img_h}.
DO NOT exceed these bounds.

Return ONLY valid JSON, no markdown:
{{"x_min": <int>, "y_min": <int>, "x_max": <int>, "y_max": <int>}}
"""

LEGEND_READ_PROMPT = """\
This is the legend region cropped from a city general plan map.

For each color swatch in the legend, identify the swatch's representative RGB color
(the color that fills the body of the swatch, NOT a border or label text color) and
the exact label text written next to it.

Rules:
  - Skip any non-zoning items (north arrows, scale bars, road symbols, scale legends).
  - If two swatches have identical-looking colors, list both anyway with their labels.
  - RGB values are 0-255 integers.
  - Read labels EXACTLY as written (preserve casing and punctuation).

Return ONLY valid JSON array, no markdown:
[{"rgb": [r, g, b], "label": "<exact zone name>"}, ...]
"""


def stage3_extract_legend(
    client: anthropic.Anthropic,
    map_image_path: Path,
    cache_path: Path,
) -> list[dict]:
    """Two vision calls: (1) find legend bbox, (2) read color↔label pairs.

    Caches result at `cache_path`. If cached file exists, skips both calls.
    """
    if cache_path.exists():
        with cache_path.open() as f:
            cached = json.load(f)
        logging.info(f"Stage 3: legend cache hit at {cache_path} ({len(cached)} entries)")
        return cached

    img = Image.open(map_image_path).convert("RGB")
    w, h = img.size

    logging.info(f"Stage 3: finding legend bbox (image {w}×{h})…")
    bbox_prompt = LEGEND_BBOX_PROMPT_TEMPLATE.format(img_w=w, img_h=h)
    bbox_raw = gpe._opus_vision_call(
        client, map_image_path, bbox_prompt, label="legend_bbox",
        max_tokens=256,
    )
    if not isinstance(bbox_raw, dict) or not all(
        k in bbox_raw for k in ("x_min", "y_min", "x_max", "y_max")
    ):
        raise ValueError(f"Legend bbox response malformed: {bbox_raw!r}")
    raw_x0, raw_y0 = int(bbox_raw["x_min"]), int(bbox_raw["y_min"])
    raw_x1, raw_y1 = int(bbox_raw["x_max"]), int(bbox_raw["y_max"])

    # If the model returned coords in a different scale (common when it reasons
    # about the embedded raster's native resolution), rescale to image bounds.
    max_returned = max(raw_x0, raw_y0, raw_x1, raw_y1)
    if max_returned > max(w, h) * 1.05:
        scale_w = w / max(raw_x1, 1)
        scale_h = h / max(raw_y1, 1)
        scale = min(scale_w, scale_h)
        logging.warning(
            f"  legend bbox out of bounds ({raw_x1}, {raw_y1} > {w}, {h}); "
            f"rescaling by {scale:.4f}"
        )
        raw_x0 = int(raw_x0 * scale)
        raw_y0 = int(raw_y0 * scale)
        raw_x1 = int(raw_x1 * scale)
        raw_y1 = int(raw_y1 * scale)

    x0, y0 = max(0, raw_x0), max(0, raw_y0)
    x1, y1 = min(w, raw_x1), min(h, raw_y1)
    logging.info(f"  legend bbox: ({x0}, {y0}) → ({x1}, {y1})")

    if x1 - x0 < 50 or y1 - y0 < 50:
        raise ValueError(f"Legend bbox too small: ({x0},{y0}) → ({x1},{y1})")
    crop = img.crop((x0, y0, x1, y1))
    crop_path = map_image_path.parent / f"_legend_crop_{map_image_path.stem}.jpg"
    crop.save(crop_path, "JPEG", quality=92)
    logging.info(f"  legend crop saved to {crop_path} ({crop.size[0]}×{crop.size[1]})")

    logging.info("Stage 3: reading legend swatches…")
    swatches_raw = gpe._opus_vision_call(
        client, crop_path, LEGEND_READ_PROMPT, label="legend_read",
        max_tokens=2048,
    )
    if not isinstance(swatches_raw, list):
        raise ValueError(f"Legend read response malformed: {type(swatches_raw)}")

    entries = []
    for s in swatches_raw:
        if not isinstance(s, dict):
            continue
        rgb = s.get("rgb")
        label = s.get("label")
        if not (isinstance(rgb, list) and len(rgb) == 3 and isinstance(label, str)):
            continue
        if not all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in rgb):
            continue
        entries.append({"rgb": [int(c) for c in rgb], "label": label.strip()})

    if len(entries) < 2:
        raise ValueError(
            f"Legend has only {len(entries)} valid swatches — refusing to proceed. "
            "Check legend bbox crop visually."
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w") as f:
        json.dump(entries, f, indent=2)
    logging.info(f"Stage 3: cached {len(entries)} legend entries → {cache_path}")
    return entries


# ---------------------------------------------------------------------------
# Color space helpers
# ---------------------------------------------------------------------------

def _rgb_to_lab_array(rgb_arr: np.ndarray) -> np.ndarray:
    """Convert (..., 3) uint8 RGB array to LAB. Returns float array same shape."""
    from skimage.color import rgb2lab
    # rgb2lab expects float in [0, 1]
    norm = rgb_arr.astype(np.float64) / 255.0
    return rgb2lab(norm)


def _lab_distance(lab_a: np.ndarray, lab_b: np.ndarray) -> float:
    """Euclidean distance in LAB space (a.k.a. CIE76 DeltaE)."""
    return float(np.sqrt(np.sum((lab_a - lab_b) ** 2)))


# ---------------------------------------------------------------------------
# Stage 4 — Per-parcel color sampling
# ---------------------------------------------------------------------------

def _build_affine_inverse(A: np.ndarray) -> np.ndarray:
    """Given A (2×3) mapping pixel→geo, return A_inv (2×3) mapping geo→pixel."""
    A3 = np.vstack([A, [0.0, 0.0, 1.0]])
    A3_inv = np.linalg.inv(A3)
    return A3_inv[:2, :]


def _geo_to_pixel(A_inv: np.ndarray, lon: float, lat: float) -> tuple[float, float]:
    """Apply inverse affine: (lon, lat) -> (px_x, px_y)."""
    v = np.array([lon, lat, 1.0])
    px = A_inv @ v
    return float(px[0]), float(px[1])


def _sample_window_lab(
    raster: np.ndarray,
    px_x: float,
    px_y: float,
    half: int = SAMPLE_HALF,
) -> Optional[np.ndarray]:
    """Sample a (2*half+1)×(2*half+1) window centered at (px_x, px_y).

    Returns the mean LAB color, or None if the window is entirely outside the raster.
    """
    h, w, _ = raster.shape
    cx, cy = int(round(px_x)), int(round(px_y))
    x0, x1 = max(0, cx - half), min(w, cx + half + 1)
    y0, y1 = max(0, cy - half), min(h, cy + half + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    window = raster[y0:y1, x0:x1, :]  # (h, w, 3)
    lab_window = _rgb_to_lab_array(window)  # (h, w, 3) LAB
    return lab_window.reshape(-1, 3).mean(axis=0)


def stage4_sample_parcels(
    raster_path: Path,
    A: np.ndarray,
    legend_entries: list[dict],
    parcels: list[dict],
    city_bbox: dict,
) -> list[dict]:
    """For each parcel: project centroid → sample 5×5 LAB → nearest legend match.

    Edge-case cascade:
      1. centroid → if LAB distance > threshold, try shapely representative_point()
      2. representative_point → if still > threshold, mark "unknown"

    Returns list of dicts:
      {parcel_id, lat, lon, sampled_zone, extraction_method, color_match_confidence,
       lab_distance, polygon_geojson}
    """
    import rasterio

    # Pre-compute LAB color for each legend entry
    legend_rgb = np.array([e["rgb"] for e in legend_entries], dtype=np.uint8)
    legend_lab = _rgb_to_lab_array(legend_rgb.reshape(-1, 1, 3)).reshape(-1, 3)
    legend_labels = [e["label"] for e in legend_entries]

    A_inv = _build_affine_inverse(A)

    # Load the entire raster into memory (Herriman: ~25MB; comfortable)
    with rasterio.open(raster_path) as src:
        raster = src.read().transpose(1, 2, 0)  # (H, W, 3)
        h, w, _ = raster.shape
        logging.info(f"Stage 4: raster loaded ({h}×{w}×{raster.shape[2]})")

    results = []
    for i, parcel in enumerate(parcels):
        pid = parcel["parcel_id"]
        cx_lon = parcel["centroid_lng"]
        cx_lat = parcel["centroid_lat"]

        def _try(point_lon, point_lat, method):
            px_x, px_y = _geo_to_pixel(A_inv, point_lon, point_lat)
            if not (0 <= px_x < w and 0 <= px_y < h):
                return None
            mean_lab = _sample_window_lab(raster, px_x, px_y)
            if mean_lab is None:
                return None
            # Nearest legend in LAB
            dists = np.sqrt(np.sum((legend_lab - mean_lab) ** 2, axis=1))
            j = int(np.argmin(dists))
            d = float(dists[j])
            return {
                "px_x": px_x, "px_y": px_y, "method": method,
                "zone": legend_labels[j], "lab_distance": d,
                "mean_lab": mean_lab.tolist(),
            }

        attempt = _try(cx_lon, cx_lat, "centroid")
        if attempt is None or attempt["lab_distance"] > LAB_DISTANCE_THRESHOLD:
            # Fall back to representative_point()
            try:
                poly = shp_shape(json.loads(parcel["polygon_geojson"]))
                rep = poly.representative_point()
                attempt2 = _try(rep.x, rep.y, "interior_point")
                if attempt2 is not None and attempt2["lab_distance"] <= LAB_DISTANCE_THRESHOLD:
                    attempt = attempt2
                elif attempt is None and attempt2 is not None:
                    attempt = attempt2  # keep the only data we have
            except Exception:
                pass

        if attempt is None:
            # Centroid + interior both off-raster — skip
            continue

        if attempt["lab_distance"] > LAB_DISTANCE_THRESHOLD:
            # Mark unknown but keep the closest match for diagnostics
            zone_out = None
            method_out = "unknown"
        else:
            zone_out = attempt["zone"]
            method_out = attempt["method"]

        results.append({
            "parcel_id": pid,
            "centroid_lng": cx_lon,
            "centroid_lat": cx_lat,
            "sampled_zone": zone_out,
            "extraction_method": method_out,
            "color_match_confidence": round(1.0 / (1.0 + attempt["lab_distance"]), 4),
            "lab_distance": round(attempt["lab_distance"], 2),
            "polygon_geojson": parcel.get("polygon_geojson"),
            "parcel_city": parcel.get("parcel_city"),
            "acreage": parcel.get("acreage"),
        })

        if (i + 1) % 1000 == 0:
            logging.info(f"  sampled {i+1}/{len(parcels)} parcels")

    return results


# ---------------------------------------------------------------------------
# Parcel + boundary loading
# ---------------------------------------------------------------------------

def _open_parcel_csv(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return open(path, "rt", newline="", encoding="utf-8")


def load_parcels_in_bbox(
    county: str,
    bbox: dict,
    buffer_deg: float = 0.005,
) -> list[dict]:
    """Stream the county parcel CSV, return parcels whose centroid falls in bbox+buffer."""
    # Some county files are gzipped, some are plain
    candidates = [
        REPO_ROOT / "data" / "raw" / f"parcels_{county}.csv",
        REPO_ROOT / "data" / "raw" / f"parcels_{county}.csv.gz",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"Parcel file not found for county {county!r}. Looked at: {candidates}"
        )
    logging.info(f"Loading parcels from {path}…")

    lon_min = bbox["lon_min"] - buffer_deg
    lon_max = bbox["lon_max"] + buffer_deg
    lat_min = bbox["lat_min"] - buffer_deg
    lat_max = bbox["lat_max"] + buffer_deg

    parcels = []
    total = 0
    with _open_parcel_csv(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            try:
                lon = float(row["centroid_lng"])
                lat = float(row["centroid_lat"])
            except (KeyError, ValueError):
                continue
            if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                continue
            parcels.append({
                "parcel_id": row["parcel_id"],
                "parcel_address": row.get("parcel_address", ""),
                "parcel_city": row.get("parcel_city", ""),
                "acreage": row.get("acreage", ""),
                "centroid_lng": lon,
                "centroid_lat": lat,
                "polygon_geojson": row.get("polygon_geojson", ""),
            })
    logging.info(f"  loaded {len(parcels):,} parcels in bbox (from {total:,} county rows)")
    return parcels


def filter_parcels_by_polygon(
    parcels: list[dict],
    boundary_geom,
) -> list[dict]:
    """Keep parcels whose centroid lies within the given shapely polygon."""
    keep = []
    for p in parcels:
        from shapely.geometry import Point
        pt = Point(p["centroid_lng"], p["centroid_lat"])
        if boundary_geom.contains(pt) or boundary_geom.intersects(pt):
            keep.append(p)
    logging.info(f"  {len(keep):,}/{len(parcels):,} parcels survive city boundary filter")
    return keep


def fetch_city_boundary(city_name: str, cache_path: Path):
    """Fetch UGRC municipal boundary for `city_name`. Cache at `cache_path`.

    Returns shapely geometry (Polygon or MultiPolygon) or None on failure.
    """
    if cache_path.exists():
        with cache_path.open() as f:
            data = json.load(f)
        feat = data["features"][0] if "features" in data else data
        return shp_shape(feat["geometry"] if "geometry" in feat else feat)

    import requests
    url = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/MunicipalBoundaries/FeatureServer/0/query"
    params = {
        "where": f"NAME='{city_name.upper()}'",
        "outFields": "NAME,SHORTDESC",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": "4326",
    }
    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data.get("features"):
            logging.warning(f"UGRC: no boundary for {city_name!r} via NAME query")
            return None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w") as f:
            json.dump(data, f)
        feat = data["features"][0]
        return shp_shape(feat["geometry"])
    except Exception as exc:
        logging.warning(f"UGRC boundary fetch failed for {city_name!r}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Stage 5 — Outputs
# ---------------------------------------------------------------------------

def stage5_write_outputs(
    results: list[dict],
    legend_entries: list[dict],
    city_cfg: dict,
    out_dir: Path,
    extra_props: Optional[dict],
    legend_cache_path: Path,
    pipeline_meta: dict,
) -> tuple[Path, Path]:
    """Write the per-parcel table CSV and a per-parcel GeoJSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    city_slug = city_cfg["city_slug"]

    csv_path = out_dir / f"{city_slug}_gp_parcel_table.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "parcel_id", "centroid_lng", "centroid_lat",
            "sampled_zone", "extraction_method", "color_match_confidence",
            "lab_distance", "parcel_city", "acreage",
        ])
        for r in results:
            w.writerow([
                r["parcel_id"], r["centroid_lng"], r["centroid_lat"],
                r["sampled_zone"] or "", r["extraction_method"],
                r["color_match_confidence"], r["lab_distance"],
                r["parcel_city"], r["acreage"],
            ])
    logging.info(f"Wrote per-parcel table → {csv_path}")

    # GeoJSON output: one feature per parcel
    features = []
    base_props = extra_props or {}
    for r in results:
        if not r.get("polygon_geojson"):
            continue
        try:
            geom = json.loads(r["polygon_geojson"])
        except (json.JSONDecodeError, TypeError):
            continue
        props = {
            **base_props,
            "parcel_id": r["parcel_id"],
            "sampled_zone": r["sampled_zone"],
            "extraction_method": r["extraction_method"],
            "color_match_confidence": r["color_match_confidence"],
            "lab_distance": r["lab_distance"],
            "source_legend": str(legend_cache_path.relative_to(REPO_ROOT)) if str(legend_cache_path).startswith(str(REPO_ROOT)) else str(legend_cache_path),
            "city_slug": city_slug,
            "city_name": city_cfg["city_name"],
            "jurisdiction": city_cfg["jurisdiction"],
            "extraction_pipeline": "phase_18b_2d_raster_sample",
            "extraction_date": time.strftime("%Y-%m-%d"),
            "georeference_rmse_ft": pipeline_meta.get("rmse_ft"),
            "georeference_cp_source": pipeline_meta.get("cp_source"),
            "georeference_cp_count": pipeline_meta.get("cp_count"),
            "georeference_rotation_deg": pipeline_meta.get("rotation_deg"),
        }
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": geom,
        })

    geojson_path = out_dir / f"{city_slug}_gp.geojson"
    fc = {
        "type": "FeatureCollection",
        "name": f"{city_slug}_gp",
        "metadata": {
            "city_slug": city_slug,
            "city_name": city_cfg["city_name"],
            "extraction_pipeline": "phase_18b_2d_raster_sample",
            "extraction_date": time.strftime("%Y-%m-%d"),
            "georeference_rmse_ft": pipeline_meta.get("rmse_ft"),
            "georeference_cp_source": pipeline_meta.get("cp_source"),
            "legend_entry_count": len(legend_entries),
            "legend_path": str(legend_cache_path),
            "parcel_count": len(features),
            **{k: v for k, v in base_props.items() if k.startswith(("flu_", "source_pdf"))},
        },
        "features": features,
    }
    with geojson_path.open("w") as f:
        json.dump(fc, f)
    logging.info(f"Wrote per-parcel GeoJSON → {geojson_path} ({len(features)} features)")
    return csv_path, geojson_path


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def emit_report(
    city_cfg: dict,
    pipeline_meta: dict,
    legend_entries: list[dict],
    results: list[dict],
    parcels_total: int,
    parcels_in_bbox: int,
    parcels_in_city: int,
    csv_path: Path,
    geojson_path: Path,
    legend_cache_path: Path,
    api_log_path: Path,
    raster_path: Path,
    elapsed_s: float,
) -> None:
    sampled = sum(1 for r in results if r["sampled_zone"] is not None)
    unknown = sum(1 for r in results if r["sampled_zone"] is None)

    zone_hist = Counter(r["sampled_zone"] for r in results if r["sampled_zone"])

    print()
    print("=" * 78)
    print(f"PHASE 18b-2d — {city_cfg['city_name']} raster-sample extraction")
    print("=" * 78)
    print(f"CP source                : {pipeline_meta['cp_source']}  ({pipeline_meta['cp_count']} CPs)")
    if pipeline_meta.get("cp_source_path"):
        print(f"CP source path           : {pipeline_meta['cp_source_path']}")
    print(f"Georeference RMSE        : {pipeline_meta['rmse_ft']:.1f} ft")
    print(f"Detected rotation        : {pipeline_meta['rotation_deg']:.2f}°")
    print(f"Legend categories        : {len(legend_entries)}")
    print(f"Legend JSON              : {legend_cache_path}")
    print(f"GeoTIFF                  : {raster_path}")
    print()
    print(f"Parcels in county        : {parcels_total:,}")
    print(f"Parcels in city bbox     : {parcels_in_bbox:,}")
    print(f"Parcels post-filter      : {parcels_in_city:,}")
    print(f"Parcels sampled (with zone) : {sampled:,}")
    print(f"Parcels marked unknown      : {unknown:,}")
    print()
    print("Zone distribution histogram:")
    for label, n in zone_hist.most_common():
        print(f"  {label:<60s} {n:6d}  ({100.0*n/max(sampled,1):5.1f}%)")
    print()

    total_cost = sum(e["cost_usd"] for e in gpe._api_log)
    by_label = {}
    for e in gpe._api_log:
        by_label.setdefault(e["call"], 0.0)
        by_label[e["call"]] += e["cost_usd"]
    print("Cost breakdown:")
    for label, cost in sorted(by_label.items(), key=lambda kv: -kv[1]):
        print(f"  {label:<40s} ${cost:.4f}")
    print(f"  TOTAL                                    ${total_cost:.4f}")
    print()
    print(f"Pipeline runtime         : {elapsed_s:.1f} s ({elapsed_s/60:.1f} min)")
    print()
    print("Output files:")
    print(f"  CSV    : {csv_path}")
    print(f"  GeoJSON: {geojson_path}")
    print(f"  API log: {api_log_path}")
    print(f"  Raster : {raster_path}")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--city", required=True, help="city slug from CITY_CONFIGS in gp_pdf_extract.py")
    p.add_argument("--pdf", required=True, type=Path, help="source PDF path")
    p.add_argument("--map-page", type=int, default=0, help="0-indexed PDF page containing the FLU map")
    p.add_argument("--manual-cps", type=Path, default=None, help="path to manual CPs JSON (skips auto Stage 2)")
    p.add_argument("--extra-props", type=Path, default=None, help="path to JSON file with extra props (flu vintage, etc.)")
    p.add_argument("--out", type=Path, default=Path("data/zoning/future"), help="output directory")
    p.add_argument("--dpi", type=int, default=300, help="rasterization DPI (default: 300)")
    p.add_argument("--rmse-threshold", type=float, default=300.0, help="reject if georeference RMSE > this (ft)")
    p.add_argument("--cost-cap", type=float, default=5.0, help="abort and report if total API cost exceeds this")
    p.add_argument("--api-key", default=None, help="Anthropic API key (env ANTHROPIC_API_KEY also OK)")
    p.add_argument("--model", default=None, help="override model (default claude-opus-4-7)")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--max-parcels", type=int, default=None, help="for dev/testing: cap parcels processed")
    p.add_argument("--use-bbox-only", action="store_true",
                   help="skip city-boundary polygon filter; use config bbox only")
    p.add_argument("--restrict-parcel-city", default=None,
                   help="filter parcels to rows where parcel_city matches this value "
                        "(case-insensitive). Cleaner than bbox when UGRC boundary unavailable.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("error: --api-key or ANTHROPIC_API_KEY required")

    if args.model:
        gpe._model_override = args.model

    city_slug = args.city
    if city_slug not in gpe.CITY_CONFIGS:
        sys.exit(f"error: unknown city slug {city_slug!r}; available: {list(gpe.CITY_CONFIGS)}")
    city_cfg = gpe.CITY_CONFIGS[city_slug]

    extra_props: Optional[dict] = None
    if args.extra_props:
        with args.extra_props.open() as f:
            extra_props = json.load(f)

    client = anthropic.Anthropic(api_key=api_key)
    t_start = time.time()

    # ---- Stage 1: rasterize ----
    map_img = stage1_rasterize_page(args.pdf, args.map_page, dpi=args.dpi)

    # ---- Stage 2: georeference ----
    A, rmse_ft, control_points, source_info = stage2_georeference(
        client, city_cfg, map_img, args.manual_cps, args.rmse_threshold,
    )

    # GeoTIFF for downstream visualization / debugging
    geotif_path = args.pdf.parent / f"{city_slug}_georef.tif"
    write_geotiff(map_img, A, geotif_path)

    # Cost cap check
    if sum(e["cost_usd"] for e in gpe._api_log) > args.cost_cap:
        sys.exit(f"COST CAP TRIPPED after Stage 2 (${sum(e['cost_usd'] for e in gpe._api_log):.4f} > ${args.cost_cap})")

    # ---- Stage 3: legend extraction ----
    legend_cache_path = REPO_ROOT / "data" / "zoning" / "future" / "legends" / f"{city_slug}_legend.json"
    legend_entries = stage3_extract_legend(client, map_img, legend_cache_path)

    if sum(e["cost_usd"] for e in gpe._api_log) > args.cost_cap:
        sys.exit(f"COST CAP TRIPPED after Stage 3 (${sum(e['cost_usd'] for e in gpe._api_log):.4f} > ${args.cost_cap})")

    # ---- Stage 4: per-parcel sampling ----
    county = CITY_TO_COUNTY.get(city_slug)
    if county is None:
        sys.exit(f"error: city {city_slug!r} not in CITY_TO_COUNTY mapping; add to gp_raster_sample_extract.py")

    parcels_bbox = load_parcels_in_bbox(county, city_cfg["bbox"])
    parcels_total = len(parcels_bbox)

    if args.use_bbox_only:
        parcels = parcels_bbox
        parcels_in_city = len(parcels)
    else:
        boundary_cache = REPO_ROOT / "data" / "cache" / "city_boundaries" / f"{city_slug}.geojson"
        boundary = fetch_city_boundary(city_cfg["city_name"], boundary_cache)
        if boundary is not None:
            parcels = filter_parcels_by_polygon(parcels_bbox, boundary)
        elif args.restrict_parcel_city:
            target = args.restrict_parcel_city.strip().lower()
            parcels = [p for p in parcels_bbox if p.get("parcel_city", "").strip().lower() == target]
            logging.info(
                f"  UGRC boundary unavailable — restricted to parcel_city={args.restrict_parcel_city!r}: "
                f"{len(parcels):,}/{len(parcels_bbox):,} parcels kept"
            )
        else:
            logging.warning("City boundary unavailable — falling back to bbox-only filter")
            parcels = parcels_bbox
        parcels_in_city = len(parcels)

    if args.max_parcels:
        parcels = parcels[: args.max_parcels]
        logging.info(f"  --max-parcels capped to {len(parcels)} parcels")

    results = stage4_sample_parcels(geotif_path, A, legend_entries, parcels, city_cfg["bbox"])

    # ---- Stage 5: outputs ----
    pipeline_meta = {
        **source_info,
    }
    if extra_props:
        pipeline_meta["extra_props_loaded"] = True

    csv_path, geojson_path = stage5_write_outputs(
        results, legend_entries, city_cfg, args.out, extra_props, legend_cache_path, pipeline_meta,
    )

    api_log_path = gpe.dump_api_log(args.out, city_slug)
    elapsed = time.time() - t_start

    emit_report(
        city_cfg, pipeline_meta, legend_entries, results,
        parcels_total=parcels_total,
        parcels_in_bbox=len(parcels_bbox),
        parcels_in_city=parcels_in_city,
        csv_path=csv_path, geojson_path=geojson_path,
        legend_cache_path=legend_cache_path,
        api_log_path=api_log_path,
        raster_path=geotif_path,
        elapsed_s=elapsed,
    )


if __name__ == "__main__":
    main()
