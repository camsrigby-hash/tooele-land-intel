"""
gp_pdf_extract.py — Georeferenced GP FLU extraction pipeline (Phase 18b-2b).

8-stage pipeline:
  1. PDF rasterization (PyMuPDF / pdf2image, 300 DPI)
  2. Control point identification (Claude Opus vision)
  3. Ground-truth lookup (OSM Nominatim / UGRC)
  4. Affine transform fit (numpy lstsq)
  5. Validation (RMSE ≤ 100 ft)
  6. Polygon extraction (Claude Opus vision, legend + zone-by-zone)
  7. Polygon projection
  8. GeoJSON output

Usage:
  python scripts/gp_pdf_extract.py --city erda [--pdf <path_or_url>] [--out data/zoning/future/]

Acceptance criteria (Phase 18b-2b):
  - ≥4 control points survive ground-truth lookup
  - RMSE ≤ 100 ft
  - Valid GeoJSON FeatureCollection written to --out/<city_slug>_gp.geojson
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import anthropic
import numpy as np
import requests
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely.validation import make_valid


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ControlPointError(Exception):
    """Raised when <4 control points survive ground-truth lookup."""


class TransformError(Exception):
    """Raised when affine transform RMSE exceeds 100 ft."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPUS_MODEL = "claude-opus-4-7"
# Cost per million tokens (approximate for Opus 4.7)
OPUS_INPUT_COST_PER_MTOK = 15.0
OPUS_OUTPUT_COST_PER_MTOK = 75.0

# Global overrides set via CLI flags
_model_override: str | None = None
_rmse_threshold: float = 100.0  # default: reject if RMSE > 100 ft


def _get_model() -> str:
    return _model_override or OPUS_MODEL

UTAH_BBOX = {"xmin": -114.0, "ymin": 37.0, "xmax": -109.0, "ymax": 42.0}
CITY_BBOX_BUFFER_DEG = 0.05   # ±0.05° for control-point acceptance
VERTEX_BBOX_BUFFER_MI = 1.0   # ±1 mile for polygon vertex validation

NOMINATIM_DELAY_S = 1.1       # OSM rate-limit: max 1 req/s
OPUS_CALLS_PER_PAGE = 8       # hard cap on polygon extraction calls per page

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "WasatchIntel-18b-2b/1.0 (cam.s.rigby@gmail.com)"})
HTTP_TIMEOUT = 60

# ---------------------------------------------------------------------------
# City configurations
# ---------------------------------------------------------------------------

CITY_CONFIGS: dict[str, dict] = {
    "erda": {
        "city_name": "Erda",
        "city_slug": "erda",
        "jurisdiction": "tooele_county_ut",
        "bbox": {"lon_min": -112.36, "lat_min": 40.58, "lon_max": -112.22, "lat_max": 40.65},
        "pdf_url": "https://erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf",
        "gp_zone_normalized_hints": {
            "residential": "future_low_density_residential",
            "commercial": "future_commercial_general",
            "industrial": "future_industrial_light",
            "agriculture": "future_agriculture",
            "open space": "future_open_space",
            "public": "future_public_institutional",
        },
    },
    "grantsville": {
        "city_name": "Grantsville",
        "city_slug": "grantsville",
        "jurisdiction": "tooele_county_ut",
        "bbox": {"lon_min": -112.50, "lat_min": 40.57, "lon_max": -112.40, "lat_max": 40.63},
        "pdf_url": None,
    },
    "bluffdale": {
        "city_name": "Bluffdale",
        "city_slug": "bluffdale",
        "jurisdiction": "salt_lake_county_ut",
        "bbox": {"lon_min": -112.00, "lat_min": 40.43, "lon_max": -111.86, "lat_max": 40.51},
        "pdf_url": None,
    },
    "vineyard": {
        "city_name": "Vineyard",
        "city_slug": "vineyard",
        "jurisdiction": "utah_county_ut",
        "bbox": {"lon_min": -111.76, "lat_min": 40.29, "lon_max": -111.71, "lat_max": 40.33},
        "pdf_url": None,
    },
    "draper": {
        "city_name": "Draper",
        "city_slug": "draper",
        "jurisdiction": "salt_lake_county_ut",
        "bbox": {"lon_min": -111.91, "lat_min": 40.43, "lon_max": -111.82, "lat_max": 40.54},
        "pdf_url": None,
    },
    "herriman": {
        "city_name": "Herriman",
        "city_slug": "herriman",
        "jurisdiction": "salt_lake_county_ut",
        "bbox": {"lon_min": -112.08, "lat_min": 40.49, "lon_max": -111.97, "lat_max": 40.56},
        "pdf_url": None,
    },
    "spanish_fork": {
        "city_name": "Spanish Fork",
        "city_slug": "spanish_fork",
        "jurisdiction": "utah_county_ut",
        "bbox": {"lon_min": -111.68, "lat_min": 40.09, "lon_max": -111.60, "lat_max": 40.16},
        "pdf_url": None,
    },
}

GP_ZONE_NORMALIZED_CLASSES = [
    "future_low_density_residential",
    "future_medium_density_residential",
    "future_high_density_residential",
    "future_commercial_general",
    "future_commercial_neighborhood",
    "future_mixed_use",
    "future_industrial_light",
    "future_industrial_heavy",
    "future_public_institutional",
    "future_open_space",
    "future_agriculture",
    "future_planned_community",
    "future_employment_center",
    "future_unknown",
]

# ---------------------------------------------------------------------------
# API call logger
# ---------------------------------------------------------------------------

_api_log: list[dict] = []


def _log_api_call(label: str, model: str, in_tok: int, out_tok: int, elapsed_s: float) -> None:
    cost = (in_tok * OPUS_INPUT_COST_PER_MTOK + out_tok * OPUS_OUTPUT_COST_PER_MTOK) / 1_000_000
    entry = {
        "call": label,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 5),
        "elapsed_s": round(elapsed_s, 1),
    }
    _api_log.append(entry)
    logging.info(
        f"  [API] {label}: {in_tok}in / {out_tok}out  ~${cost:.4f}  ({elapsed_s:.1f}s)"
    )


def dump_api_log(out_dir: Path, city_slug: str) -> Path:
    log_path = out_dir / f"{city_slug}_api_calls.jsonl"
    with log_path.open("w") as f:
        for entry in _api_log:
            f.write(json.dumps(entry) + "\n")
    total_cost = sum(e["cost_usd"] for e in _api_log)
    total_calls = len(_api_log)
    logging.info(f"API log: {total_calls} calls, total ~${total_cost:.4f} → {log_path}")
    return log_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def haversine_ft(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in feet."""
    R_FT = 20_902_231.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R_FT * math.asin(math.sqrt(a))


def miles_to_deg_lat(miles: float) -> float:
    return miles / 69.0


def miles_to_deg_lon(miles: float, lat: float) -> float:
    return miles / (69.0 * math.cos(math.radians(lat)))


def image_to_b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def _parse_json_from_text(text: str) -> object:
    """
    Extract JSON from an Opus response that may be wrapped in markdown code blocks.
    """
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    md_match = re.search(r"```(?:json)?\s*\n?([\s\S]+?)```", text)
    if md_match:
        text = md_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Try to find the first [ or { and parse from there
        for start_char in ("[", "{"):
            idx = text.find(start_char)
            if idx != -1:
                try:
                    return json.loads(text[idx:])
                except json.JSONDecodeError:
                    pass
        raise ValueError(f"Cannot parse JSON from Opus response: {text[:200]}") from exc


def _opus_vision_call(
    client: anthropic.Anthropic,
    image_path: Path,
    prompt: str,
    label: str,
    max_tokens: int = 4096,
) -> object:
    """Single Opus vision call. Returns parsed JSON. Logs usage. Retries on rate limit."""
    b64 = image_to_b64(image_path)
    retry_delays = [10, 30, 60]  # seconds to wait on successive rate-limit hits
    t0 = time.time()
    model = _get_model()
    for attempt, delay in enumerate([0] + retry_delays):
        if delay:
            logging.info(f"  Rate-limited — waiting {delay}s before retry {attempt}…")
            time.sleep(delay)
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            break  # success
        except anthropic.RateLimitError:
            if attempt >= len(retry_delays):
                raise
            continue
    else:
        raise RuntimeError("Exhausted rate-limit retries")

    elapsed = time.time() - t0
    _log_api_call(label, model, msg.usage.input_tokens, msg.usage.output_tokens, elapsed)
    return _parse_json_from_text(msg.content[0].text)


# ---------------------------------------------------------------------------
# Stage 1 — PDF rasterization
# ---------------------------------------------------------------------------

def _rasterize_with_fitz(pdf_path: Path, dpi: int, out_dir: Path) -> list[Path]:
    """Rasterize PDF with PyMuPDF (no poppler needed)."""
    import fitz  # noqa: PLC0415
    doc = fitz.open(str(pdf_path))
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        dest = out_dir / f"_page_{i:03d}.jpg"
        pix.save(str(dest))
        paths.append(dest)
    return paths


def _rasterize_with_pdf2image(pdf_path: Path, dpi: int, out_dir: Path) -> list[Path]:
    """Rasterize PDF with pdf2image + poppler."""
    from pdf2image import convert_from_path  # noqa: PLC0415
    images = convert_from_path(str(pdf_path), dpi=dpi, fmt="jpeg")
    paths = []
    for i, img in enumerate(images):
        dest = out_dir / f"_page_{i:03d}.jpg"
        img.save(str(dest), "JPEG", quality=95)
        paths.append(dest)
    return paths


MAP_PAGE_KEYWORDS = [
    "future land use", "general plan map", "land use map",
    "flu map", "future land", "land use element",
]


def find_map_page_hints(pdf_path: Path) -> list[int]:
    """
    Scan PDF page text for FLU/land-use map keywords.
    Returns list of 0-indexed page numbers that likely contain a map, ordered by relevance.
    Falls back to empty list if fitz not available or PDF has no text.
    """
    try:
        import fitz  # noqa: PLC0415
        doc = fitz.open(str(pdf_path))
        hits: list[tuple[int, int]] = []  # (page_num, score)
        for i, page in enumerate(doc):
            text = page.get_text().lower()
            score = sum(1 for kw in MAP_PAGE_KEYWORDS if kw in text)
            if score > 0:
                hits.append((i, score))
        hits.sort(key=lambda t: t[1], reverse=True)
        return [h[0] for h in hits]
    except Exception:
        return []


def stage1_rasterize(pdf_path: Path, dpi: int = 300) -> list[Path]:
    """
    Stage 1 — Convert PDF to JPEG images at `dpi` resolution.
    Tries PyMuPDF first (no external dependency), falls back to pdf2image.
    Rejects pages < 300 KB (likely low-res scan or blank).
    """
    tmp_dir = pdf_path.parent / "_raster_tmp"
    tmp_dir.mkdir(exist_ok=True)
    logging.info("Stage 1: rasterizing PDF…")

    try:
        pages = _rasterize_with_fitz(pdf_path, dpi, tmp_dir)
        logging.info(f"  Used PyMuPDF (fitz) — {len(pages)} pages")
    except ImportError:
        try:
            pages = _rasterize_with_pdf2image(pdf_path, dpi, tmp_dir)
            logging.info(f"  Used pdf2image — {len(pages)} pages")
        except Exception as exc:
            raise RuntimeError(
                "Neither PyMuPDF (fitz) nor pdf2image+poppler is available. "
                "Install PyMuPDF: pip install pymupdf"
            ) from exc

    valid = [p for p in pages if p.stat().st_size >= 300_000]
    logging.info(f"  {len(valid)}/{len(pages)} pages ≥ 300 KB (map pages)")
    if not valid:
        raise RuntimeError("No map pages found (all pages < 300 KB). Check PDF source.")
    return valid


# ---------------------------------------------------------------------------
# Stage 2 — Control point identification
# ---------------------------------------------------------------------------

CONTROL_POINT_PROMPT = """\
This is a city general plan or land use map image.

Identify 6–10 labeled street intersections visible on the map.
For each intersection you identify, return:
  - px_x: x pixel coordinate from the LEFT edge of the image
  - px_y: y pixel coordinate from the TOP edge of the image
  - street_a: name of the first street (exactly as labeled on the map)
  - street_b: name of the second street (exactly as labeled on the map)
  - conf: your confidence — "high" (both names clearly readable), "medium" (one name slightly unclear), or "low" (guessing)

Rules:
  - Only return intersections where you can clearly read BOTH street names.
  - Do not guess street names from context or geography.
  - Pixel coordinates must be integers measured from the top-left corner.
  - If fewer than 4 intersections are clearly visible, return as many as you can find with honest confidence levels.

Return ONLY a valid JSON array, no other text, no markdown code blocks:
[{"px_x": 847, "px_y": 523, "street_a": "Main St", "street_b": "Center St", "conf": "high"}, ...]
"""


def stage2_identify_control_points(
    client: anthropic.Anthropic,
    page_images: list[Path],
    max_pages_to_try: int = 12,
    map_page_hints: Optional[list[int]] = None,
) -> tuple[list[dict], int]:
    """
    Stage 2 — Call Opus to identify labeled street intersections.
    Prioritizes pages identified by text-search (map_page_hints), then falls back
    to size-sorted candidates.
    Returns (control_points, page_index).
    """
    logging.info("Stage 2: identifying control points…")

    # Build candidate list: text-hint pages first, then size-sorted remainder
    seen: set[int] = set()
    candidates_to_try: list[tuple[int, Path]] = []

    # First: pages flagged by text keyword scan (most reliable signal for FLU map)
    if map_page_hints:
        for raw_page_num in map_page_hints:
            # raw_page_num is the original PDF page index;
            # find the corresponding filtered page
            for i, p in enumerate(page_images):
                page_num = int(p.stem.split("_page_")[-1]) if "_page_" in p.stem else -1
                if page_num == raw_page_num and i not in seen:
                    seen.add(i)
                    candidates_to_try.append((i, p))
                    break

    # Then: size-sorted (largest first = most image content)
    size_sorted = sorted(
        enumerate(page_images),
        key=lambda t: t[1].stat().st_size,
        reverse=True,
    )
    for i, p in size_sorted:
        if i not in seen and len(candidates_to_try) < max_pages_to_try:
            seen.add(i)
            candidates_to_try.append((i, p))

    candidates_to_try = candidates_to_try[:max_pages_to_try]
    logging.info(
        f"  Will try {len(candidates_to_try)} pages: "
        + ", ".join(f"p{i}({p.stat().st_size//1024}KB)" for i, p in candidates_to_try)
    )

    best_points: list[dict] = []
    best_page = 0

    for page_idx, img_path in candidates_to_try:
        logging.info(f"  Trying page {page_idx}: {img_path.name}")
        try:
            raw = _opus_vision_call(
                client, img_path, CONTROL_POINT_PROMPT,
                label=f"control_points_p{page_idx}",
                max_tokens=2048,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"  Page {page_idx} control-point parse failed: {exc}")
            continue

        if not isinstance(raw, list):
            logging.warning(f"  Page {page_idx}: unexpected response type {type(raw)}")
            continue

        # Filter to high/medium confidence only
        candidates = [
            pt for pt in raw
            if isinstance(pt, dict)
            and pt.get("conf", "low") in ("high", "medium")
            and isinstance(pt.get("px_x"), (int, float))
            and isinstance(pt.get("px_y"), (int, float))
            and pt.get("street_a") and pt.get("street_b")
        ]
        logging.info(f"  Page {page_idx}: {len(candidates)} high/medium confidence intersections")

        if len(candidates) >= len(best_points):
            best_points = candidates
            best_page = page_idx

        if len(best_points) >= 4:
            break  # enough to proceed

    if len(best_points) < 4:
        raise ControlPointError(
            f"Only {len(best_points)} high/medium confidence intersections found across all pages. "
            "Need ≥4. Check that the PDF contains a readable street-labeled map."
        )

    logging.info(
        f"  Selected page {best_page} with {len(best_points)} candidate control points"
    )
    return best_points, best_page


# ---------------------------------------------------------------------------
# Stage 3 — Ground-truth lookup
# ---------------------------------------------------------------------------

def _nominatim_lookup(street_a: str, street_b: str, city_name: str) -> Optional[tuple[float, float]]:
    """Query OSM Nominatim for a street intersection. Returns (lat, lon) or None."""
    query = f"{street_a} and {street_b}, {city_name}, Utah"
    url = (
        "https://nominatim.openstreetmap.org/search"
        f"?q={quote(query)}&format=json&limit=1&addressdetails=0"
    )
    try:
        resp = HTTP_SESSION.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
    except Exception as exc:
        logging.debug(f"  Nominatim lookup failed for '{query}': {exc}")
    return None


def _ugrc_lookup(street_a: str, street_b: str, city_name: str) -> Optional[tuple[float, float]]:
    """Query UGRC geocoder API for a street intersection. Returns (lat, lon) or None."""
    api_key = os.environ.get("UGRC_API_KEY", "")
    if not api_key:
        return None
    # UGRC intersection endpoint
    url = (
        "https://api.mapserv.utah.gov/api/v1/geocode/intersection"
        f"/{quote(street_a)}/{quote(street_b)}"
        f"?apiKey={api_key}&spatialReference=4326"
    )
    try:
        resp = HTTP_SESSION.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or {}
        loc = result.get("location") or {}
        if loc.get("x") and loc.get("y"):
            return float(loc["y"]), float(loc["x"])  # lat, lon
    except Exception as exc:
        logging.debug(f"  UGRC lookup failed for '{street_a}/{street_b}': {exc}")
    return None


def _within_bbox(lat: float, lon: float, bbox: dict, buffer_deg: float = 0.0) -> bool:
    return (
        bbox["lon_min"] - buffer_deg <= lon <= bbox["lon_max"] + buffer_deg
        and bbox["lat_min"] - buffer_deg <= lat <= bbox["lat_max"] + buffer_deg
    )


def stage3_ground_truth_lookup(
    candidates: list[dict],
    city_cfg: dict,
) -> list[dict]:
    """
    Stage 3 — Resolve each (street_a, street_b) to ground-truth (lat, lon).
    Drops intersections that can't be resolved or fall outside city bbox ± 0.05°.
    Requires ≥4 surviving intersections.
    """
    logging.info("Stage 3: ground-truth lookup…")
    city_name = city_cfg["city_name"]
    city_bbox = city_cfg["bbox"]
    resolved = []

    for i, pt in enumerate(candidates):
        sa, sb = pt["street_a"], pt["street_b"]
        logging.info(f"  [{i}] Looking up: {sa!r} ∩ {sb!r}")
        time.sleep(NOMINATIM_DELAY_S)

        # Try UGRC first (more accurate for Utah), fall back to Nominatim
        latlon = _ugrc_lookup(sa, sb, city_name) or _nominatim_lookup(sa, sb, city_name)
        if latlon is None:
            logging.info(f"    → not found (skipped)")
            continue

        lat, lon = latlon
        if not _within_bbox(lat, lon, city_bbox, CITY_BBOX_BUFFER_DEG):
            logging.info(
                f"    → outside city bbox ({lat:.5f}, {lon:.5f}) — likely wrong city (skipped)"
            )
            continue

        logging.info(f"    → ({lat:.6f}, {lon:.6f})  conf={pt['conf']}")
        resolved.append({
            "px_x": float(pt["px_x"]),
            "px_y": float(pt["px_y"]),
            "street_a": sa,
            "street_b": sb,
            "conf": pt["conf"],
            "gt_lat": lat,
            "gt_lon": lon,
        })

    logging.info(f"  {len(resolved)}/{len(candidates)} intersections resolved within bbox")
    if len(resolved) < 4:
        raise ControlPointError(
            f"Only {len(resolved)} intersections resolved inside city bbox. "
            "Need ≥4. Check that street names on the map match OSM/UGRC naming."
        )
    return resolved


# ---------------------------------------------------------------------------
# Stage 4 — Affine transform fit
# ---------------------------------------------------------------------------

def stage4_fit_affine(control_points: list[dict]) -> np.ndarray:
    """
    Stage 4 — Fit 6-parameter affine transform: [lon, lat] = A @ [px_x, px_y, 1]^T
    Returns A as 2×3 numpy array.
    """
    logging.info(f"Stage 4: fitting affine transform ({len(control_points)} control points)…")
    X = np.array([[p["px_x"], p["px_y"], 1.0] for p in control_points])   # N×3
    Y = np.array([[p["gt_lon"], p["gt_lat"]] for p in control_points])     # N×2
    # lstsq solves X @ A_T = Y  →  A_T is 3×2, A is 2×3
    A_T, residuals, rank, sv = np.linalg.lstsq(X, Y, rcond=None)
    A = A_T.T  # 2×3
    logging.info(f"  Affine matrix A:\n{A}")
    return A


# ---------------------------------------------------------------------------
# Stage 5 — Validation (RMSE in feet)
# ---------------------------------------------------------------------------

def stage5_validate(A: np.ndarray, control_points: list[dict], rmse_threshold_ft: float = 100.0) -> tuple[float, list[dict]]:
    """
    Stage 5 — Compute per-point residuals and overall RMSE in feet.
    Returns (rmse_ft, annotated_control_points).
    Raises TransformError if RMSE > 100 ft.
    """
    logging.info("Stage 5: validating transform (RMSE check)…")
    annotated = []
    residuals_sq = []
    for pt in control_points:
        px = np.array([pt["px_x"], pt["px_y"], 1.0])
        pred = A @ px  # [pred_lon, pred_lat]
        pred_lon, pred_lat = pred[0], pred[1]
        res_ft = haversine_ft(pt["gt_lat"], pt["gt_lon"], pred_lat, pred_lon)
        residuals_sq.append(res_ft ** 2)
        annotated.append({
            **pt,
            "pred_lon": round(pred_lon, 7),
            "pred_lat": round(pred_lat, 7),
            "residual_ft": round(res_ft, 1),
        })
        logging.info(
            f"  {pt['street_a']} ∩ {pt['street_b']}: residual {res_ft:.1f} ft"
        )

    rmse = math.sqrt(sum(residuals_sq) / len(residuals_sq))
    logging.info(f"  RMSE: {rmse:.1f} ft")

    if rmse > rmse_threshold_ft:
        raise TransformError(
            f"Transform RMSE {rmse:.1f} ft exceeds {rmse_threshold_ft:.0f} ft threshold. "
            "Check control point quality or image resolution. "
            "Use --rmse-threshold to override for testing."
        )
    if rmse > 50.0:
        logging.warning(f"  RMSE {rmse:.1f} ft is in yellow-flag zone (>50 ft)")

    return rmse, annotated


# ---------------------------------------------------------------------------
# Stage 6 — Polygon extraction
# ---------------------------------------------------------------------------

LEGEND_PROMPT = """\
This is a city general plan or future land use map.

Read the map legend and identify all zone codes and their descriptions.
Return ONLY a valid JSON array, no markdown, no other text:
[{"code": "R-1", "description": "Low Density Residential"}, ...]

If no clear legend is visible, return an empty array: []
"""


def _build_zone_extraction_prompt(zone_code: str, zone_description: str) -> str:
    return f"""\
This is a city general plan or future land use map.

Trace all polygons on the map that are labeled or colored to represent the zone:
  Code: {zone_code!r}
  Description: {zone_description!r}

For each polygon you find, return an ordered list of [px_x, px_y] pixel vertices
measured from the TOP-LEFT corner of the image.
Trace only polygon boundaries you can clearly see — do not guess.
Include enough vertices to capture the shape accurately (at least 4 per polygon).

Return ONLY a valid JSON array of polygons, no markdown, no other text:
[[px_x1, py_y1], [px_x2, px_y2], ...]]  <- single polygon
OR
[[[px_x1, py_y1], ...], [[px_x1, py_y1], ...]]  <- multiple polygons

If this zone code is not present on this map, return an empty array: []
"""


def stage6_extract_polygons(
    client: anthropic.Anthropic,
    page_images: list[Path],
    map_page_idx: int,
) -> tuple[list[dict], list[dict]]:
    """
    Stage 6 — Read legend, then extract pixel polygons for each zone class.
    Returns (legend_entries, polygon_records).
    Each polygon_record: {"zone_code", "zone_description", "px_polygon": [[x,y],...]}
    """
    img = page_images[map_page_idx]
    logging.info(f"Stage 6: polygon extraction from page {map_page_idx}…")

    # 6a — read legend
    logging.info("  Reading legend…")
    try:
        legend = _opus_vision_call(
            client, img, LEGEND_PROMPT,
            label="legend",
            max_tokens=1024,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        logging.warning(f"  Legend read failed: {exc} — continuing with empty legend")
        legend = []

    if not isinstance(legend, list):
        logging.warning(f"  Legend response was {type(legend)}, expected list — treating as empty")
        legend = []

    legend = [e for e in legend if isinstance(e, dict) and e.get("code")]
    logging.info(f"  Legend: {len(legend)} zone entries")
    for e in legend:
        logging.info(f"    {e.get('code')!r}: {e.get('description')!r}")

    if not legend:
        logging.warning(
            "  No legend entries found. Will attempt extraction with generic zone descriptions."
        )
        legend = [
            {"code": "Residential", "description": "Residential"},
            {"code": "Commercial", "description": "Commercial"},
            {"code": "Industrial", "description": "Industrial"},
            {"code": "Agriculture", "description": "Agriculture / Open Space"},
            {"code": "Public", "description": "Public / Institutional"},
        ]

    # 6b — extract polygons per zone (hard cap: OPUS_CALLS_PER_PAGE)
    polygon_records: list[dict] = []
    calls_used = 0  # legend already counted separately

    for zone in legend:
        if calls_used >= OPUS_CALLS_PER_PAGE:
            logging.warning(
                f"  Reached {OPUS_CALLS_PER_PAGE}-call cap — skipping remaining zones"
            )
            break

        code = zone.get("code", "")
        desc = zone.get("description", "")
        prompt = _build_zone_extraction_prompt(code, desc)

        try:
            raw = _opus_vision_call(
                client, img, prompt,
                label=f"zone_{code}",
                max_tokens=4096,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"  Zone {code!r} extraction parse failed: {exc}")
            calls_used += 1
            continue

        calls_used += 1

        if not isinstance(raw, list) or not raw:
            logging.info(f"  Zone {code!r}: no polygons found")
            continue

        # Normalize: could be [[x,y],...] or [[[x,y],...],...]
        if isinstance(raw[0][0], (int, float)):
            # Single polygon returned as flat list
            polygons = [raw]
        else:
            polygons = raw

        valid_polys = [
            poly for poly in polygons
            if isinstance(poly, list) and len(poly) >= 3
        ]
        logging.info(f"  Zone {code!r}: {len(valid_polys)} polygons extracted")
        for poly in valid_polys:
            polygon_records.append({
                "zone_code": code,
                "zone_description": desc,
                "px_polygon": poly,
            })

    logging.info(f"  Total: {len(polygon_records)} polygon records from {len(legend)} zones")
    return legend, polygon_records


# ---------------------------------------------------------------------------
# Stage 7 — Polygon projection
# ---------------------------------------------------------------------------

def _project_pixel_polygon(A: np.ndarray, px_poly: list) -> list[tuple[float, float]]:
    """Apply affine transform A to pixel polygon. Returns [(lon, lat), ...]."""
    projected = []
    for vertex in px_poly:
        if not (isinstance(vertex, (list, tuple)) and len(vertex) >= 2):
            continue
        px_x, px_y = float(vertex[0]), float(vertex[1])
        pred = A @ np.array([px_x, px_y, 1.0])
        projected.append((round(pred[0], 7), round(pred[1], 7)))
    return projected


def _vertex_in_bounds(lon: float, lat: float, bbox: dict, buffer_mi: float = 1.0) -> bool:
    lat_buf = miles_to_deg_lat(buffer_mi)
    lon_buf = miles_to_deg_lon(buffer_mi, lat)
    return (
        bbox["lon_min"] - lon_buf <= lon <= bbox["lon_max"] + lon_buf
        and bbox["lat_min"] - lat_buf <= lat <= bbox["lat_max"] + lat_buf
    )


def stage7_project_polygons(
    A: np.ndarray,
    polygon_records: list[dict],
    city_cfg: dict,
    rmse_ft: float = 0.0,
) -> list[dict]:
    """
    Stage 7 — Apply affine transform to all pixel polygons.
    Drops polygons with any vertex outside city bbox + buffer.
    Buffer scales with RMSE: accurate transforms use ±1 mile; coarse regional
    maps (RMSE > 500 ft) use ±(rmse_ft / 5280 * 15) miles so vertices aren't
    all rejected due to transform imprecision.
    """
    logging.info("Stage 7: projecting polygons to EPSG:4326…")
    city_bbox = city_cfg["bbox"]
    # Scale buffer: for very coarse transforms, allow a much larger region
    buffer_mi = max(VERTEX_BBOX_BUFFER_MI, (rmse_ft / 5280) * 15)
    if buffer_mi > VERTEX_BBOX_BUFFER_MI:
        logging.info(f"  Expanded vertex bbox buffer to {buffer_mi:.1f} mi (RMSE={rmse_ft:.0f} ft)")
    projected_records = []
    dropped = 0

    for rec in polygon_records:
        coords = _project_pixel_polygon(A, rec["px_polygon"])
        if len(coords) < 3:
            dropped += 1
            continue

        # Validate all vertices are within bounds
        out_of_bounds = [
            v for v in coords
            if not _vertex_in_bounds(v[0], v[1], city_bbox, buffer_mi)
        ]
        if out_of_bounds:
            logging.debug(
                f"  Dropping zone {rec['zone_code']!r} polygon — "
                f"{len(out_of_bounds)} vertices outside bounds"
            )
            dropped += 1
            continue

        # Build Shapely polygon and validate
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.is_empty or poly.area == 0:
                dropped += 1
                continue
        except Exception:
            dropped += 1
            continue

        projected_records.append({
            **rec,
            "projected_coords": coords,
            "shapely_polygon": poly,
        })

    logging.info(
        f"  {len(projected_records)} polygons valid, {dropped} dropped (out-of-bounds or degenerate)"
    )
    return projected_records


# ---------------------------------------------------------------------------
# Stage 8 — GeoJSON output
# ---------------------------------------------------------------------------

def _normalize_zone(zone_code: str, zone_description: str, city_cfg: dict) -> str:
    """Map a raw zone code/description to a normalized GP zone class."""
    hints = city_cfg.get("gp_zone_normalized_hints", {})
    combined = (zone_code + " " + zone_description).lower()
    for keyword, normalized in hints.items():
        if keyword.lower() in combined:
            return normalized
    # Fallback heuristics
    if any(w in combined for w in ("residential", "single family", "multi family", "duplex", "r-1", "r-2", "r-3", "lr", "mr", "hr")):
        if any(w in combined for w in ("high", "multi", "apartment", "condo")):
            return "future_high_density_residential"
        if any(w in combined for w in ("medium", "duplex", "townhome")):
            return "future_medium_density_residential"
        return "future_low_density_residential"
    if any(w in combined for w in ("commercial", "retail", "business", "c-1", "c-2", "bc", "nc", "gc", "cc")):
        if any(w in combined for w in ("neighborhood", "local", "nc")):
            return "future_commercial_neighborhood"
        return "future_commercial_general"
    if any(w in combined for w in ("mixed use", "mixed-use", "mu", "town center", "village center")):
        return "future_mixed_use"
    if any(w in combined for w in ("industrial", "manufacturing", "warehouse", "m-1", "m-2", "li", "hi")):
        if any(w in combined for w in ("heavy", "m-2", "hi")):
            return "future_industrial_heavy"
        return "future_industrial_light"
    if any(w in combined for w in ("public", "institutional", "government", "civic", "school", "pi")):
        return "future_public_institutional"
    if any(w in combined for w in ("open space", "park", "recreation", "os", "p&r", "greenway")):
        return "future_open_space"
    if any(w in combined for w in ("agriculture", "agricultural", "ag", "farm", "rural")):
        return "future_agriculture"
    if any(w in combined for w in ("planned", "pud", "pc")):
        return "future_planned_community"
    if any(w in combined for w in ("employment", "office", "research", "tech")):
        return "future_employment_center"
    return "future_unknown"


def stage8_write_geojson(
    projected_records: list[dict],
    legend: list[dict],
    city_cfg: dict,
    rmse_ft: float,
    n_control_points: int,
    source_pdf: str,
    map_page_idx: int,
    out_dir: Path,
) -> Path:
    """
    Stage 8 — Build GeoJSON FeatureCollection and write to disk.
    Returns path to written file.
    """
    logging.info("Stage 8: writing GeoJSON…")
    city_slug = city_cfg["city_slug"]
    city_name = city_cfg["city_name"]
    today = datetime.date.today().isoformat()

    confidence = (
        "anchored_approximation" if rmse_ft <= 50.0
        else "anchored_approximation_yellow"
    )

    features = []
    for rec in projected_records:
        poly: Polygon = rec["shapely_polygon"]
        geom = mapping(poly)

        zone_code = rec["zone_code"]
        zone_desc = rec["zone_description"]
        normalized = _normalize_zone(zone_code, zone_desc, city_cfg)

        feature = {
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "city_slug": city_slug,
                "city_name": city_name,
                "gp_zone_code": zone_code,
                "gp_zone_description": zone_desc,
                "gp_zone_normalized": normalized,
                "jurisdiction": city_cfg["jurisdiction"],
                "source_pdf": source_pdf,
                "source_page_id": f"page_{map_page_idx}",
                "extraction_method": "anthropic_vision_claude_opus_4_7_georeferenced",
                "confidence": confidence,
                "transform_residual_ft": round(rmse_ft, 1),
                "n_control_points": n_control_points,
                "extraction_date": today,
            },
        }
        features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    out_path = out_dir / f"{city_slug}_gp.geojson"
    out_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    logging.info(f"  Wrote {len(features)} features → {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------

def write_transform_validation(
    control_points_annotated: list[dict],
    rmse_ft: float,
    projected_records: list[dict],
    A: np.ndarray,
    city_cfg: dict,
    out_dir: Path,
) -> Path:
    """Write erda_transform_validation.md (or <city>_transform_validation.md)."""
    city_slug = city_cfg["city_slug"]
    today = datetime.date.today().isoformat()
    out_path = out_dir / f"{city_slug}_transform_validation.md"

    lines = [
        f"# {city_cfg['city_name']} GP FLU — Transform Validation Report",
        f"",
        f"**Date**: {today}",
        f"**Model**: {OPUS_MODEL}",
        f"**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced",
        f"",
        f"## Affine Transform Matrix",
        f"",
        f"```",
        f"[lon]   [{A[0,0]:.8f}  {A[0,1]:.8f}  {A[0,2]:.8f}]   [px_x]",
        f"[lat] = [{A[1,0]:.8f}  {A[1,1]:.8f}  {A[1,2]:.8f}] * [px_y]",
        f"                                                        [ 1  ]",
        f"```",
        f"",
        f"## Control Points",
        f"",
        f"| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ]

    for i, pt in enumerate(control_points_annotated):
        lines.append(
            f"| {i+1} | {pt['street_a']} | {pt['street_b']} "
            f"| {int(pt['px_x'])} | {int(pt['px_y'])} "
            f"| {pt['gt_lon']:.6f} | {pt['gt_lat']:.6f} "
            f"| {pt['pred_lon']:.6f} | {pt['pred_lat']:.6f} "
            f"| {pt['residual_ft']:.1f} |"
        )

    flag = "✅ PASS" if rmse_ft <= 100 else "❌ FAIL"
    yellow = " (yellow flag)" if 50 < rmse_ft <= 100 else ""
    lines += [
        f"",
        f"**Overall RMSE: {rmse_ft:.1f} ft** {flag}{yellow}",
        f"",
        f"## Visual Spot-Checks",
        f"",
        f"Manual judgment: does each projected polygon land in the correct zone per the source PDF?",
        f"",
        f"| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |",
        f"|---|---|---|---|---|",
    ]

    # Generate 5 spot-checks from the projected records (pick representative ones)
    spot_checks = projected_records[:5]
    for i, rec in enumerate(spot_checks):
        coords = rec["projected_coords"]
        mid = coords[len(coords) // 2] if coords else (0, 0)
        lon, lat = mid
        lines.append(
            f"| {i+1} | {rec['zone_code']} — {rec['zone_description']} "
            f"| {lon:.6f}, {lat:.6f} "
            f"| Should be {rec['zone_code']} zone area "
            f"| [manual review needed] |"
        )

    if len(spot_checks) < 5:
        for j in range(len(spot_checks), 5):
            lines.append(f"| {j+1} | — | — | — | insufficient polygons extracted |")

    lines += [
        f"",
        f"## API Cost Summary",
        f"",
        f"| Call | Input tokens | Output tokens | Cost USD |",
        f"|---|---|---|---|",
    ]
    total_cost = 0.0
    for entry in _api_log:
        lines.append(
            f"| {entry['call']} | {entry['input_tokens']} | {entry['output_tokens']} | ${entry['cost_usd']:.4f} |"
        )
        total_cost += entry["cost_usd"]
    lines.append(f"| **TOTAL** | | | **${total_cost:.4f}** |")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.info(f"  Wrote validation report -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def download_pdf(url: str, dest_dir: Path) -> Path:
    """Download a PDF from a URL to dest_dir. Returns local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1] or "gp.pdf"
    dest = dest_dir / filename
    if dest.exists():
        logging.info(f"PDF already cached at {dest}")
        return dest
    logging.info(f"Downloading PDF: {url}")
    resp = HTTP_SESSION.get(url, timeout=HTTP_TIMEOUT, stream=True)
    resp.raise_for_status()
    with dest.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    logging.info(f"  Saved {dest.stat().st_size / 1024:.0f} KB → {dest}")
    return dest


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    city_slug: str,
    pdf_source: Optional[str],
    out_dir: Path,
    api_key: str,
    dpi: int = 300,
    manual_cps: Optional[list[dict]] = None,
    map_page_override: Optional[int] = None,
) -> dict:
    """
    Run the full 8-stage pipeline for a given city.

    manual_cps: optional list of pre-seeded control points that bypass stages 2–3.
      Each entry: {"px_x": int, "px_y": int, "gt_lat": float, "gt_lon": float, "label": str}
    map_page_override: 0-indexed filtered-page index to use for stage 6 polygon extraction.
      Required when manual_cps is provided (so the pipeline knows which page to extract from).

    Returns a result summary dict.
    """
    if city_slug not in CITY_CONFIGS:
        raise ValueError(f"Unknown city slug {city_slug!r}. Known: {list(CITY_CONFIGS)}")

    city_cfg = CITY_CONFIGS[city_slug]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve PDF source
    if pdf_source is None:
        pdf_source = city_cfg.get("pdf_url")
    if pdf_source is None:
        raise ValueError(
            f"No PDF source for {city_slug!r}. "
            "Provide --pdf <url_or_path> or update CITY_CONFIGS."
        )

    client = anthropic.Anthropic(api_key=api_key)

    # Stage 0: Get PDF as local file
    if pdf_source.startswith("http"):
        cache_dir = out_dir.parent.parent / "_pdf_cache"
        pdf_path = download_pdf(pdf_source, cache_dir)
    else:
        pdf_path = Path(pdf_source)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Run stages
    page_images = stage1_rasterize(pdf_path, dpi=dpi)

    if manual_cps:
        # Bypass stages 2–3: use caller-supplied control points directly
        logging.info(f"Stage 2–3 BYPASSED: using {len(manual_cps)} manual control points")
        resolved_cps = [
            {
                "px_x": float(cp["px_x"]),
                "px_y": float(cp["px_y"]),
                "street_a": cp.get("label", f"cp_{i}"),
                "street_b": "manual",
                "conf": "manual",
                "gt_lat": float(cp["gt_lat"]),
                "gt_lon": float(cp["gt_lon"]),
            }
            for i, cp in enumerate(manual_cps)
        ]
        map_page_idx = map_page_override if map_page_override is not None else 0
        logging.info(f"  Using map page index {map_page_idx} for polygon extraction")
    else:
        map_page_hints = find_map_page_hints(pdf_path)
        logging.info(f"Map page hints (text scan): raw pages {map_page_hints[:5]}")

        control_candidates, map_page_idx = stage2_identify_control_points(
            client, page_images, map_page_hints=map_page_hints
        )

        resolved_cps = stage3_ground_truth_lookup(control_candidates, city_cfg)

    A = stage4_fit_affine(resolved_cps)

    rmse_ft, annotated_cps = stage5_validate(A, resolved_cps, rmse_threshold_ft=_rmse_threshold)

    legend, polygon_records = stage6_extract_polygons(client, page_images, map_page_idx)

    projected = stage7_project_polygons(A, polygon_records, city_cfg, rmse_ft=rmse_ft)

    geojson_path = stage8_write_geojson(
        projected, legend, city_cfg, rmse_ft,
        n_control_points=len(resolved_cps),
        source_pdf=pdf_source,
        map_page_idx=map_page_idx,
        out_dir=out_dir,
    )

    val_path = write_transform_validation(
        annotated_cps, rmse_ft, projected, A, city_cfg, out_dir
    )

    dump_api_log(out_dir, city_slug)

    zone_classes = sorted({r["gp_zone_normalized"] for r in projected})
    total_cost = sum(e["cost_usd"] for e in _api_log)

    result = {
        "city_slug": city_slug,
        "rmse_ft": round(rmse_ft, 1),
        "n_control_points": len(resolved_cps),
        "n_features": len(projected),
        "zone_classes_found": zone_classes,
        "total_cost_usd": round(total_cost, 4),
        "geojson_path": str(geojson_path),
        "validation_path": str(val_path),
        "confidence": "anchored_approximation" if rmse_ft <= 50 else "anchored_approximation_yellow",
    }
    logging.info(f"\n{'='*60}")
    logging.info(f"PIPELINE COMPLETE — {city_slug}")
    logging.info(f"  RMSE: {rmse_ft:.1f} ft  ({len(resolved_cps)} control points)")
    logging.info(f"  Features: {len(projected)}")
    logging.info(f"  Zone classes: {zone_classes}")
    logging.info(f"  Total cost: ~${total_cost:.4f}")
    logging.info(f"  GeoJSON: {geojson_path}")
    logging.info(f"  Validation: {val_path}")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Georeferenced GP FLU extraction pipeline (Phase 18b-2b)"
    )
    parser.add_argument("--city", required=True, help="City slug (e.g. erda)")
    parser.add_argument("--pdf", default=None, help="PDF URL or local path (overrides config)")
    parser.add_argument(
        "--out",
        default="data/zoning/future",
        help="Output directory (default: data/zoning/future)",
    )
    parser.add_argument("--dpi", type=int, default=300, help="Rasterization DPI (default: 300)")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Override model (default: {OPUS_MODEL}). Use claude-haiku-4-5-20251001 for testing.",
    )
    parser.add_argument(
        "--rmse-threshold",
        type=float,
        default=100.0,
        help="Max acceptable RMSE in feet (default: 100). Increase for coarse regional maps.",
    )
    parser.add_argument(
        "--manual-cps",
        default=None,
        help=(
            "JSON string or file path with pre-seeded control points (bypasses stages 2–3). "
            "Format: [{\"px_x\":int,\"px_y\":int,\"gt_lat\":float,\"gt_lon\":float,\"label\":str},...]"
        ),
    )
    parser.add_argument(
        "--map-page",
        type=int,
        default=None,
        help="0-indexed filtered-page number to use for polygon extraction (required with --manual-cps).",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    global _model_override, _rmse_threshold
    if args.model:
        _model_override = args.model
    _rmse_threshold = args.rmse_threshold

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        parser.error(
            "Anthropic API key required. Set ANTHROPIC_API_KEY env var or pass --api-key."
        )

    # Parse manual control points
    manual_cps = None
    if args.manual_cps:
        raw = args.manual_cps.strip()
        if raw.startswith("[") or raw.startswith("{"):
            manual_cps = json.loads(raw)
        else:
            manual_cps = json.loads(Path(raw).read_text())
        if isinstance(manual_cps, dict):
            manual_cps = [manual_cps]
        logging.info(f"Loaded {len(manual_cps)} manual control points from CLI")

    out_dir = Path(args.out)

    try:
        result = run_pipeline(
            city_slug=args.city,
            pdf_source=args.pdf,
            out_dir=out_dir,
            api_key=api_key,
            dpi=args.dpi,
            manual_cps=manual_cps,
            map_page_override=args.map_page,
        )
    except ControlPointError as exc:
        logging.error(f"CONTROL POINT ERROR: {exc}")
        sys.exit(2)
    except TransformError as exc:
        logging.error(f"TRANSFORM ERROR: {exc}")
        sys.exit(3)
    except Exception as exc:
        logging.exception(f"PIPELINE FAILED: {exc}")
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
