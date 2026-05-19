"""
gp_pdf_extract.py — Georeferenced GP layer extraction pipeline (Phase 18b-2 v2).

8-stage pipeline:
  1. PDF rasterization (PyMuPDF / pdf2image, 300 DPI)
  2. Control point identification (Claude Opus vision)
  3. Ground-truth lookup (OSM Nominatim / UGRC)
  4. Affine transform fit (numpy lstsq)
  5. Validation (RMSE ≤ 100 ft)
  6. Polygon extraction (Claude Opus vision, legend + zone-by-zone)
  7. Polygon projection
  8. GeoJSON output

Supports three layer types: flu, annexation, mpc.
Multi-PDF aggregation via --pdf-urls for sectional GPs (e.g. Grantsville).

Usage:
  python scripts/gp_pdf_extract.py --city erda --layer-type flu \\
      [--pdf <path_or_url>] [--out data/zoning/future/]
  python scripts/gp_pdf_extract.py --city grantsville --layer-type flu \\
      --pdf-urls "url1,url2,url3"

Acceptance criteria (Phase 18b-2):
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

# SD-18 Stage 3 quality-gate constants
# Rule A: Overpass node-count gate — long numbered roads (e.g. 12600 S spanning 20+ miles)
# return 100s of nodes whose median collapses multiple CPs to identical points.
NODE_COUNT_THRESHOLD = 20          # reject CP if Overpass returns > this many shared nodes
# Rule B: Geographic collinearity gate — two CPs resolving within this distance (metres)
# are effectively the same point and will produce a degenerate affine system.
COLLINEARITY_THRESHOLD_M = 50      # reject the lower-confidence CP if pair is within 50 m

OVERPASS_INTERSECTION_RADIUS_M = 8000  # ~5 mi; covers most Utah cities without bleeding into adjacent ones

NOMINATIM_DELAY_S = 1.1       # OSM rate-limit: max 1 req/s
OPUS_CALLS_PER_PAGE = 8       # default cap on polygon extraction calls per page
TILE_SIZE = 500               # px, square tile side for Stage 2b refinement
_max_zone_calls: int | None = None  # overridden by --max-zone-calls
_tile_refine: bool = False    # enabled by --tile-refine

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
        # bbox + whitelist match Herriman_Zoning.kmz coverage — FLU planning area extends
        # beyond city limits — 18b-2d-2 (May 18 2026)
        "bbox": {"lon_min": -112.0941, "lat_min": 40.4425, "lon_max": -111.9241, "lat_max": 40.5421},
        "parcel_city_whitelist": ["Herriman", "South Jordan", "Bluffdale", "Unincorporated Salt Lake County"],
        "pdf_url": None,
        "stage2_preferred_streets": [
            "Rosecrest Rd", "Main St", "Pioneer St", "Herriman Pkwy",
            "Fort Herriman Pkwy", "Aylesbury Dr", "Copeland Dr",
            "Anthem Park Blvd", "Butterfield Pkwy",
        ],
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

# Valid values for future_layer_type in GeoJSON output properties.
FUTURE_LAYER_TYPES = frozenset([
    "flu",
    "annexation_proposed",
    "annexation_existing_future",
    "annexation_existing_city",
    "mpc_overlay",
    "other",
])

# Layer types that do not require zone code fields (those may be null).
ANNEXATION_LAYER_TYPES = frozenset([
    "annexation_proposed",
    "annexation_existing_future",
    "annexation_existing_city",
])

# Keywords used for --layer-type auto inference.
_ANNEXATION_KEYWORDS = ("annexation", "annex", "proposed boundary", "city boundary")
_MPC_KEYWORDS = ("mpc", "master planned community", "overlay", "specific plan")

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

    # Anthropic API hard limit: 5 MB per image (base64). A 3.5 MB JPEG encodes to ~4.7 MB.
    # Large-format maps (e.g. 36×36 in at 300 DPI) routinely exceed this — resize them down.
    MAX_IMAGE_BYTES = 3_500_000
    resized = []
    for p in valid:
        if p.stat().st_size > MAX_IMAGE_BYTES:
            try:
                from PIL import Image  # noqa: PLC0415
                scale = (MAX_IMAGE_BYTES / p.stat().st_size) ** 0.5
                with Image.open(p) as img:
                    new_w = int(img.width * scale)
                    new_h = int(img.height * scale)
                    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                    img_resized.save(str(p), "JPEG", quality=92)
                logging.info(
                    f"  Resized {p.name}: {img.width}×{img.height} → {new_w}×{new_h} "
                    f"(scale={scale:.2f}, new size={p.stat().st_size//1024}KB)"
                )
            except Exception as exc:
                logging.warning(f"  Could not resize {p.name}: {exc} — API call may fail")
        resized.append(p)
    return resized


# ---------------------------------------------------------------------------
# Stage 2 — Control point identification
# ---------------------------------------------------------------------------

CONTROL_POINT_PROMPT = """\
This is a city general plan, future land use, or annexation map image.

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
  - If the map uses a satellite or aerial photograph as its base layer, look for
    intersections of named arterial roads visible in the imagery — these are often
    more clearly identifiable than on a flat choropleth map.
  - If fewer than 4 intersections are clearly visible, return as many as you can find with honest confidence levels.

STREET SELECTION PRIORITY — IMPORTANT:
  STRONGLY PREFER named local streets (e.g. 'Rosecrest Rd', 'Main St', 'Pioneer St',
  city parkways, named drives and boulevards) over numbered arterials ('12600 South',
  '6000 West', state highways, Bangerter Hwy, Mountain View Corridor).
  Numbered roads often span 20+ miles and create georeference ambiguity. Named local
  streets produce more reliable ground-truth lookup. Include numbered arterials ONLY
  as a last resort if fewer than 5 named-street intersections are visible on the map.

Return ONLY a valid JSON array, no other text, no markdown code blocks:
[{"px_x": 847, "px_y": 523, "street_a": "Main St", "street_b": "Center St", "conf": "high"}, ...]
"""


def _build_stage2_prompt(city_cfg: Optional[dict] = None) -> str:
    """Return CONTROL_POINT_PROMPT, optionally with city-specific preferred-streets hint."""
    preferred = (city_cfg or {}).get("stage2_preferred_streets")
    if not preferred:
        return CONTROL_POINT_PROMPT
    hint = (
        "\nKnown named streets on this map that you should prioritize: "
        + ", ".join(f"'{s}'" for s in preferred)
        + ".\n"
        "Look specifically for intersections involving these streets before falling back to numbered arterials.\n"
    )
    # Insert hint just before the final JSON-format line
    split_marker = "\nReturn ONLY a valid JSON array"
    base = CONTROL_POINT_PROMPT
    idx = base.rfind(split_marker)
    if idx == -1:
        return base + hint
    return base[:idx] + hint + base[idx:]


def stage2_identify_control_points(
    client: anthropic.Anthropic,
    page_images: list[Path],
    max_pages_to_try: int = 12,
    map_page_hints: Optional[list[int]] = None,
    city_cfg: Optional[dict] = None,
) -> tuple[list[dict], int]:
    """
    Stage 2 — Call Opus to identify labeled street intersections.
    Prioritizes pages identified by text-search (map_page_hints), then falls back
    to size-sorted candidates.
    Returns (control_points, page_index).
    """
    logging.info("Stage 2: identifying control points…")
    prompt = _build_stage2_prompt(city_cfg)

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
                client, img_path, prompt,
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
# Stage 2b — Tile refinement
# ---------------------------------------------------------------------------

TILE_REFINE_PROMPT_TEMPLATE = """\
This is a zoomed-in region of a land use map.

The center of this image should be near the intersection of:
  {intersection_desc}

Return the precise pixel coordinates of the actual intersection of these streets in this \
image's coordinate space:
  (0, 0) = top-left corner
  ({tile_size}, {tile_size}) = bottom-right corner

Sub-50-pixel precision required. If the intersection is clearly visible, place the coordinate
exactly at the road crossing centerpoint. If the streets do not clearly cross within this view,
return your best estimate of where the crossing would be based on visible road trajectories.
Do NOT simply return ({half}, {half}) unless that is genuinely your most precise estimate.

Return ONLY a valid JSON object, no other text:
{{"px_x": <integer>, "px_y": <integer>}}
"""


def stage2b_tile_refine(
    client: anthropic.Anthropic,
    resolved_cps: list[dict],
    page_images: list[Path],
    map_page_idx: int,
) -> tuple[list[dict], dict]:
    """
    Stage 2b — Tile-refinement pass for large-format maps.

    For each control point, crops a TILE_SIZE×TILE_SIZE px tile centered on the rough
    pixel estimate, sends it to Claude vision, and composes the tile-relative refined
    coords back into full-image coords. Only px_x/px_y are modified; gt_lat/gt_lon
    and all other fields are preserved unchanged.

    Returns (refined_cps, stats).
    """
    try:
        from PIL import Image as PilImage  # noqa: PLC0415
    except ImportError:
        logging.warning("Stage 2b: Pillow not available — skipping tile refinement")
        return resolved_cps, {"count_refined": 0, "avg_shift_px": 0.0, "max_shift_px": 0.0}

    logging.info(f"Stage 2b: tile refinement ({len(resolved_cps)} control points)…")
    img_path = page_images[map_page_idx]

    with PilImage.open(img_path) as full_img:
        img_w, img_h = full_img.width, full_img.height
    logging.info(f"  Source image: {img_w}×{img_h} px ({img_path.stat().st_size // 1024} KB)")

    tile_dir = img_path.parent / "_tile_refine_tmp"
    tile_dir.mkdir(exist_ok=True)

    refined_cps: list[dict] = []
    shifts: list[float] = []
    half = TILE_SIZE // 2

    for i, cp in enumerate(resolved_cps):
        rough_x = float(cp["px_x"])
        rough_y = float(cp["px_y"])
        street_a = cp.get("street_a", f"cp_{i}")
        street_b = cp.get("street_b", "")

        # Build readable intersection description for the prompt
        if street_b and street_b not in ("manual", ""):
            intersection_desc = f"{street_a} and {street_b}"
        else:
            # Manual CP label format e.g. "11800S_x_AnthemPark" → "11800S and AnthemPark"
            intersection_desc = street_a.replace("_x_", " and ").replace("_", " ")

        # Tile crop: center TILE_SIZE window on rough estimate, clamped to image bounds
        tile_x0 = max(0, int(round(rough_x)) - half)
        tile_y0 = max(0, int(round(rough_y)) - half)
        tile_x1 = min(img_w, tile_x0 + TILE_SIZE)
        tile_y1 = min(img_h, tile_y0 + TILE_SIZE)
        # Re-anchor if clamped at right/bottom edge
        tile_x0 = max(0, tile_x1 - TILE_SIZE)
        tile_y0 = max(0, tile_y1 - TILE_SIZE)

        actual_w = tile_x1 - tile_x0
        actual_h = tile_y1 - tile_y0

        tile_path = tile_dir / f"_tile_cp{i:02d}.jpg"
        with PilImage.open(img_path) as full_img:
            tile = full_img.crop((tile_x0, tile_y0, tile_x1, tile_y1))
            tile.save(str(tile_path), "JPEG", quality=95)

        prompt = TILE_REFINE_PROMPT_TEMPLATE.format(
            intersection_desc=intersection_desc,
            tile_size=TILE_SIZE,
            half=half,
        )

        logging.info(
            f"  CP{i} ({street_a!r}): "
            f"rough=({rough_x:.0f},{rough_y:.0f})  "
            f"tile=[{tile_x0},{tile_y0}→{tile_x1},{tile_y1}]"
        )

        try:
            raw = _opus_vision_call(
                client, tile_path, prompt,
                label=f"tile_refine_cp{i}",
                max_tokens=256,
            )
            if not isinstance(raw, dict):
                raise ValueError(f"Expected dict, got {type(raw)}")
            tile_rx = float(raw.get("px_x", half))
            tile_ry = float(raw.get("px_y", half))
            tile_rx = max(0.0, min(float(actual_w - 1), tile_rx))
            tile_ry = max(0.0, min(float(actual_h - 1), tile_ry))
        except Exception as exc:
            logging.warning(f"    tile refinement failed: {exc} — keeping rough coords")
            tile_rx = rough_x - tile_x0
            tile_ry = rough_y - tile_y0

        full_rx = tile_x0 + tile_rx
        full_ry = tile_y0 + tile_ry
        shift = math.hypot(full_rx - rough_x, full_ry - rough_y)
        shifts.append(shift)

        logging.info(
            f"    tile_pos=({tile_rx:.1f},{tile_ry:.1f})  "
            f"full_refined=({full_rx:.1f},{full_ry:.1f})  "
            f"shift={shift:.1f}px"
        )

        refined_cps.append({
            **cp,
            "_rough_px_x": rough_x,
            "_rough_px_y": rough_y,
            "px_x": full_rx,
            "px_y": full_ry,
            "_tile_shift_px": round(shift, 1),
        })

    avg_shift = sum(shifts) / len(shifts) if shifts else 0.0
    max_shift = max(shifts) if shifts else 0.0

    stats = {
        "count_refined": len(refined_cps),
        "avg_shift_px": round(avg_shift, 1),
        "max_shift_px": round(max_shift, 1),
        "shifts_px": [round(s, 1) for s in shifts],
    }

    logging.info(
        f"  Stage 2b complete: {len(refined_cps)} CPs refined, "
        f"avg_shift={avg_shift:.1f}px  max_shift={max_shift:.1f}px"
    )
    return refined_cps, stats


# ---------------------------------------------------------------------------
# Stage 3 — Ground-truth lookup
# ---------------------------------------------------------------------------

_STREET_ABBR_MAP = [
    (re.compile(r'\bHwy\b', re.I), 'Highway'),
    (re.compile(r'\bPkwy\b', re.I), 'Parkway'),
    (re.compile(r'\bRd\b', re.I), 'Road'),
    (re.compile(r'\bBlvd\b', re.I), 'Boulevard'),
    (re.compile(r'\bDr\b', re.I), 'Drive'),
    (re.compile(r'\bAve\b', re.I), 'Avenue'),
    (re.compile(r'\bLn\b', re.I), 'Lane'),
    (re.compile(r'\bCt\b', re.I), 'Court'),
    (re.compile(r'\bCir\b', re.I), 'Circle'),
    (re.compile(r'\bCyn\b', re.I), 'Canyon'),
    (re.compile(r'\bMtn\b', re.I), 'Mountain'),
]


def _expand_street_abbr(name: str) -> str:
    for pat, repl in _STREET_ABBR_MAP:
        name = pat.sub(repl, name)
    return name


def _nominatim_lookup(street_a: str, street_b: str, city_name: str) -> Optional[tuple[float, float]]:
    """Query OSM Nominatim for a street intersection. Returns (lat, lon) or None.

    Tries up to four query variants to handle map abbreviations and OSM naming mismatches:
    exact with city, expanded with city, exact state-only, expanded state-only.
    """
    sa_exp = _expand_street_abbr(street_a)
    sb_exp = _expand_street_abbr(street_b)
    queries = [
        f"{street_a} and {street_b}, {city_name}, Utah",
        f"{sa_exp} and {sb_exp}, {city_name}, Utah",
        f"{street_a} and {street_b}, Utah",
        f"{sa_exp} and {sb_exp}, Utah",
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_queries = [q for q in queries if not (q in seen or seen.add(q))]

    for i, query in enumerate(unique_queries):
        if i > 0:
            time.sleep(NOMINATIM_DELAY_S)
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
                logging.debug(f"    Nominatim hit on variant {i}: '{query}'")
                return lat, lon
        except Exception as exc:
            logging.debug(f"  Nominatim lookup failed for '{query}': {exc}")
    return None


def _overpass_lookup(street_a: str, street_b: str, city_bbox: dict) -> Optional[tuple[float, float, int]]:
    """Query Overpass API for shared nodes between two named streets within city bbox.

    Uses fuzzy (regex) name matching so abbreviated map labels (e.g. "Herriman Hwy")
    still find OSM ways named "Herriman Highway" or "Herriman Parkway".
    Returns (lat, lon, node_count) of the median shared node, or None.
    node_count is used by the SD-18 Rule A quality gate to detect over-broad matches
    on long numbered roads (e.g. 12600 South spanning 20+ miles in the Salt Lake valley).
    """
    sa_exp = _expand_street_abbr(street_a)
    sb_exp = _expand_street_abbr(street_b)

    # Build a regex that matches the expanded name as a substring (case-insensitive)
    def _to_regex(name: str) -> str:
        # Use first two tokens of the name for more permissive matching
        tokens = name.split()
        return re.escape(tokens[0]) if len(tokens) <= 1 else re.escape(tokens[0]) + ".*" + re.escape(tokens[1])

    sa_re = _to_regex(sa_exp)
    sb_re = _to_regex(sb_exp)

    bbox_str = (
        f"{city_bbox['lat_min'] - CITY_BBOX_BUFFER_DEG},"
        f"{city_bbox['lon_min'] - CITY_BBOX_BUFFER_DEG},"
        f"{city_bbox['lat_max'] + CITY_BBOX_BUFFER_DEG},"
        f"{city_bbox['lon_max'] + CITY_BBOX_BUFFER_DEG}"
    )

    query = (
        f'[out:json][timeout:25];\n'
        f'way["name"~"{sa_re}",i]["highway"]({bbox_str});\n'
        f'way["name"~"{sb_re}",i]["highway"]({bbox_str});\n'
        f'node(w._)(w._);\nout geom;\n'
    )
    try:
        resp = HTTP_SESSION.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        nodes = [e for e in resp.json().get("elements", []) if "lat" in e]
        if not nodes:
            return None
        node_count = len(nodes)
        # Use the median node (robust to outliers across long road segments)
        nodes.sort(key=lambda n: n["lat"])
        mid = nodes[node_count // 2]
        logging.debug(
            f"    Overpass: {node_count} shared nodes for "
            f"{sa_re!r} × {sb_re!r} → median ({mid['lat']:.6f}, {mid['lon']:.6f})"
        )
        return mid["lat"], mid["lon"], node_count
    except Exception as exc:
        logging.debug(f"  Overpass lookup failed for {sa_re!r} × {sb_re!r}: {exc}")
        return None


def _overpass_intersection_lookup(
    street_a: str,
    street_b: str,
    city_lat: float,
    city_lon: float,
    radius_m: int = OVERPASS_INTERSECTION_RADIUS_M,
) -> Optional[tuple[float, float, int]]:
    """Query Overpass for the actual node(s) where street_a and street_b physically cross.

    Uses named result sets to find nodes that belong to BOTH named ways within
    a radius of the city centroid.  This returns only the intersection point(s)
    rather than the median of all nodes on one way — fixing the SD-18 root cause
    for numbered-grid cities (Herriman, Sandy, Riverton, West Jordan, etc.).

    If multiple intersection nodes are returned (overpass structures, frontage
    roads at the same crossing), returns the one closest to the city centroid.
    node_count = number of shared nodes found; compatible with SD-18 Rule A gate
    but will typically be 1–3 for a real intersection.
    Returns None if no shared nodes found.
    """
    sa_exp = _expand_street_abbr(street_a)
    sb_exp = _expand_street_abbr(street_b)

    _CARDINAL_RE = {
        "north": "N(orth)?", "south": "S(outh)?", "east": "E(ast)?", "west": "W(est)?",
        "n": "N(orth)?", "s": "S(outh)?", "e": "E(ast)?", "w": "W(est)?",
    }

    def _to_regex(name: str) -> str:
        tokens = name.split()
        if len(tokens) <= 1:
            return re.escape(tokens[0])
        # Strip parenthetical aliases (e.g. "13100 South (Main St)" → "13100 South")
        clean = [t for t in tokens if not t.startswith("(")]
        last = clean[-1].lower() if clean else tokens[-1].lower()
        if last in _CARDINAL_RE and len(clean) >= 2:
            # "12600 South" or "12600 S" → "12600.*S(outh)?" so both OSM forms match
            return re.escape(clean[0]) + ".*" + _CARDINAL_RE[last]
        return re.escape(tokens[0]) + ".*" + re.escape(tokens[1])

    sa_re = _to_regex(sa_exp)
    sb_re = _to_regex(sb_exp)

    # Named-set intersection query: nodes in set .na AND set .nb = true crossing nodes.
    query = (
        f'[out:json][timeout:30];\n'
        f'way["name"~"{sa_re}",i]["highway"](around:{radius_m},{city_lat:.6f},{city_lon:.6f})->.a;\n'
        f'node(w.a)->.na;\n'
        f'way["name"~"{sb_re}",i]["highway"](around:{radius_m},{city_lat:.6f},{city_lon:.6f})->.b;\n'
        f'node(w.b)->.nb;\n'
        f'node.na.nb;\n'
        f'out;\n'
    )
    try:
        resp = HTTP_SESSION.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        nodes = [e for e in resp.json().get("elements", []) if "lat" in e]
        if not nodes:
            return None
        node_count = len(nodes)
        # Return the intersection node closest to city centroid
        best = min(nodes, key=lambda n: haversine_ft(city_lat, city_lon, n["lat"], n["lon"]))
        logging.debug(
            f"    Overpass intersection: {node_count} node(s) for "
            f"{sa_re!r} × {sb_re!r} → closest ({best['lat']:.6f}, {best['lon']:.6f})"
        )
        return best["lat"], best["lon"], node_count
    except Exception as exc:
        logging.debug(f"  Overpass intersection lookup failed for {sa_re!r} × {sb_re!r}: {exc}")
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


_CONF_UNCERTAINTY_RANK = {"high": 0, "medium": 1, "low": 2, "manual": -1}


def stage3_ground_truth_lookup(
    candidates: list[dict],
    city_cfg: dict,
) -> list[dict]:
    """
    Stage 3 — Resolve each (street_a, street_b) to ground-truth (lat, lon).
    Drops intersections that can't be resolved or fall outside city bbox ± 0.05°.

    SD-18 quality gates applied after resolution:
      Rule A — node_count gate: reject Overpass-resolved CPs with > NODE_COUNT_THRESHOLD
        shared nodes (symptom: numbered road spanning multiple cities, median collapses CPs).
      Rule B — collinearity gate: if any two surviving CPs are within COLLINEARITY_THRESHOLD_M
        metres of each other, reject the one with higher pixel uncertainty (lower conf).

    Requires ≥3 surviving CPs after quality gates; raises ControlPointError otherwise.
    """
    logging.info("Stage 3: ground-truth lookup…")
    city_name = city_cfg["city_name"]
    city_bbox = city_cfg["bbox"]
    resolved = []

    for i, pt in enumerate(candidates):
        sa, sb = pt["street_a"], pt["street_b"]
        logging.info(f"  [{i}] Looking up: {sa!r} ∩ {sb!r}")
        time.sleep(NOMINATIM_DELAY_S)

        # Try UGRC first (most accurate for Utah), then Nominatim, then Overpass.
        # Overpass path: try intersection-node query first (SD-18 fix), fall back to
        # legacy median-of-way only if intersection query returns nothing.
        overpass_node_count = 0
        intersection_lookup_failed = False
        latlon: Optional[tuple[float, float]] = (
            _ugrc_lookup(sa, sb, city_name)
            or _nominatim_lookup(sa, sb, city_name)
        )
        if latlon is None:
            city_lat_c = (city_bbox["lat_min"] + city_bbox["lat_max"]) / 2
            city_lon_c = (city_bbox["lon_min"] + city_bbox["lon_max"]) / 2
            intersection_result = _overpass_intersection_lookup(sa, sb, city_lat_c, city_lon_c)
            if intersection_result is not None:
                lat, lon, overpass_node_count = intersection_result
                latlon = (lat, lon)
            else:
                # Fallback: legacy median-of-way (marks CP so diagnostics can flag it)
                overpass_result = _overpass_lookup(sa, sb, city_bbox)
                if overpass_result is not None:
                    lat, lon, overpass_node_count = overpass_result
                    latlon = (lat, lon)
                    intersection_lookup_failed = True

        if latlon is None:
            logging.info(f"    → not found (skipped)")
            continue

        lat, lon = latlon
        if not _within_bbox(lat, lon, city_bbox, CITY_BBOX_BUFFER_DEG):
            logging.info(
                f"    → outside city bbox ({lat:.5f}, {lon:.5f}) — likely wrong city (skipped)"
            )
            continue

        lookup_mode = "legacy-median" if intersection_lookup_failed else "intersection" if overpass_node_count else "ugrc/nominatim"
        logging.info(
            f"    → ({lat:.6f}, {lon:.6f})  conf={pt['conf']}  "
            f"overpass_nodes={overpass_node_count}  lookup={lookup_mode}"
        )
        resolved.append({
            "px_x": float(pt["px_x"]),
            "px_y": float(pt["px_y"]),
            "street_a": sa,
            "street_b": sb,
            "conf": pt["conf"],
            "gt_lat": lat,
            "gt_lon": lon,
            "overpass_node_count": overpass_node_count,
            "intersection_lookup_failed": intersection_lookup_failed,
        })

    # ------------------------------------------------------------------
    # SD-18 Stage 3 quality gates
    # ------------------------------------------------------------------
    gate_stats = {"identified": len(resolved), "rejected_rule_a": 0, "rejected_rule_b": 0}
    surviving: list[dict] = []
    rejected_log: list[str] = []

    # Rule A — node-count gate
    for cp in resolved:
        nc = cp["overpass_node_count"]
        if nc > NODE_COUNT_THRESHOLD:
            gate_stats["rejected_rule_a"] += 1
            msg = (
                f"node_count_exceeded: {cp['street_a']!r} ∩ {cp['street_b']!r} "
                f"— Overpass returned {nc} nodes (threshold {NODE_COUNT_THRESHOLD})"
            )
            logging.info(f"  [SD-18 Rule A] REJECT {msg}")
            rejected_log.append(msg)
        else:
            surviving.append(cp)

    # Rule B — geographic collinearity gate (pairwise 50 m check)
    COLLINEARITY_THRESHOLD_FT = COLLINEARITY_THRESHOLD_M * 3.28084
    ruled_out: set[int] = set()
    for idx_a in range(len(surviving)):
        if idx_a in ruled_out:
            continue
        for idx_b in range(idx_a + 1, len(surviving)):
            if idx_b in ruled_out:
                continue
            cp_a, cp_b = surviving[idx_a], surviving[idx_b]
            dist_ft = haversine_ft(cp_a["gt_lat"], cp_a["gt_lon"], cp_b["gt_lat"], cp_b["gt_lon"])
            if dist_ft < COLLINEARITY_THRESHOLD_FT:
                # Reject the one with higher uncertainty (lower confidence rank number = better)
                rank_a = _CONF_UNCERTAINTY_RANK.get(cp_a["conf"], 1)
                rank_b = _CONF_UNCERTAINTY_RANK.get(cp_b["conf"], 1)
                if rank_a <= rank_b:
                    reject_idx, keep_idx = idx_b, idx_a
                else:
                    reject_idx, keep_idx = idx_a, idx_b
                ruled_out.add(reject_idx)
                cp_rej = surviving[reject_idx]
                cp_kep = surviving[keep_idx]
                gate_stats["rejected_rule_b"] += 1
                dist_m = dist_ft / 3.28084
                msg = (
                    f"collinear_with_other_cp: {cp_rej['street_a']!r} ∩ {cp_rej['street_b']!r} "
                    f"is {dist_m:.1f} m from {cp_kep['street_a']!r} ∩ {cp_kep['street_b']!r} "
                    f"(conf {cp_rej['conf']!r} vs {cp_kep['conf']!r})"
                )
                logging.info(f"  [SD-18 Rule B] REJECT {msg}")
                rejected_log.append(msg)

    surviving = [cp for i, cp in enumerate(surviving) if i not in ruled_out]

    # ------------------------------------------------------------------
    # Stage 3 diagnostic block
    # ------------------------------------------------------------------
    n_legacy = sum(1 for cp in surviving if cp.get("intersection_lookup_failed"))
    logging.info("Stage 3 SD-18 quality gate diagnostic:")
    logging.info(f"  CPs identified (pre-gate): {gate_stats['identified']}")
    logging.info(f"  Rejected Rule A (node_count_exceeded): {gate_stats['rejected_rule_a']}")
    logging.info(f"  Rejected Rule B (collinear_with_other_cp): {gate_stats['rejected_rule_b']}")
    logging.info(f"  CPs surviving: {len(surviving)}  (intersection_lookup: {len(surviving) - n_legacy}  legacy_median_fallback: {n_legacy})")
    for cp in surviving:
        mode = " [legacy-median]" if cp.get("intersection_lookup_failed") else (" [intersection]" if cp.get("overpass_node_count") else " [ugrc/nominatim]")
        logging.info(
            f"    KEEP: {cp['street_a']!r} ∩ {cp['street_b']!r} "
            f"({cp['gt_lat']:.6f}, {cp['gt_lon']:.6f}) conf={cp['conf']}{mode}"
        )
    for msg in rejected_log:
        logging.info(f"    REJECTED: {msg}")

    logging.info(f"  {len(surviving)}/{len(candidates)} CPs survived all gates")

    if len(surviving) < 3:
        raise ControlPointError(
            "SD-18 quality gates rejected too many CPs; need >=3 unique points for valid "
            "2D georeference. Consider --manual-cps. "
            f"(Gate stats: identified={gate_stats['identified']}, "
            f"rejected_rule_a={gate_stats['rejected_rule_a']}, "
            f"rejected_rule_b={gate_stats['rejected_rule_b']}, "
            f"surviving={len(surviving)})"
        )
    return surviving


# ---------------------------------------------------------------------------
# Stage 4 — Affine transform fit
# ---------------------------------------------------------------------------

def _detect_map_rotation(control_points: list[dict]) -> float:
    """
    Detect map rotation angle in degrees by comparing bearing in pixel space
    vs bearing in geographic space for the two control points with the largest
    pixel-space separation.
    Returns rotation_deg: positive = map rotated clockwise relative to north-up.
    """
    if len(control_points) < 2:
        return 0.0

    # Find the pair with the largest pixel-space distance
    best_i, best_j, best_dist = 0, 1, 0.0
    for i in range(len(control_points)):
        for j in range(i + 1, len(control_points)):
            pi, pj = control_points[i], control_points[j]
            d = math.hypot(pj["px_x"] - pi["px_x"], pj["px_y"] - pi["px_y"])
            if d > best_dist:
                best_dist, best_i, best_j = d, i, j

    p1, p2 = control_points[best_i], control_points[best_j]

    # Bearing in pixel space (x right, y down): angle from "up" (-y) clockwise
    dpx = p2["px_x"] - p1["px_x"]
    dpy = p2["px_y"] - p1["px_y"]
    bearing_px_deg = math.degrees(math.atan2(dpx, -dpy))

    # Bearing in geographic space: angle from north clockwise
    # Correct lon difference for equirectangular aspect ratio
    lat_avg = math.radians((p1["gt_lat"] + p2["gt_lat"]) / 2.0)
    dlon = (p2["gt_lon"] - p1["gt_lon"]) * math.cos(lat_avg)
    dlat = p2["gt_lat"] - p1["gt_lat"]
    bearing_geo_deg = math.degrees(math.atan2(dlon, dlat))

    rotation_deg = bearing_px_deg - bearing_geo_deg
    # Normalize to [-180, 180]
    while rotation_deg > 180:
        rotation_deg -= 360
    while rotation_deg < -180:
        rotation_deg += 360

    logging.info(
        f"  Rotation detection: bearing_px={bearing_px_deg:.1f}°, "
        f"bearing_geo={bearing_geo_deg:.1f}°, rotation={rotation_deg:.1f}°"
    )
    return rotation_deg


def stage4_fit_affine(control_points: list[dict]) -> tuple[np.ndarray, float]:
    """
    Stage 4 — Fit rotation-aware affine transform: [lon, lat] = A @ [px_x, px_y, 1]^T

    Detects map rotation from the control point pair with the largest pixel separation,
    derotates all pixel coords around their centroid, fits affine on derotated coords,
    then folds the derotation into the final 2×3 matrix so stage7 is unchanged.

    Returns (A_combined, rotation_angle_deg) where A_combined is 2×3.
    """
    logging.info(f"Stage 4: fitting affine transform ({len(control_points)} control points)…")

    rotation_deg = _detect_map_rotation(control_points)
    theta = math.radians(-rotation_deg)  # derotate by negative of detected rotation

    # Centroid of pixel coords (rotation center)
    cx = sum(p["px_x"] for p in control_points) / len(control_points)
    cy = sum(p["px_y"] for p in control_points) / len(control_points)

    # 3×3 homogeneous derotation matrix (rotates pixel coords by -rotation_deg around centroid)
    c, s = math.cos(theta), math.sin(theta)
    D = np.array([
        [c, -s, cx - c * cx + s * cy],
        [s,  c, cy - s * cx - c * cy],
        [0,  0, 1.0],
    ])

    # Derotate all control point pixel coordinates
    derotated_px = []
    for p in control_points:
        pv = np.array([p["px_x"], p["px_y"], 1.0])
        dr = D @ pv
        derotated_px.append([dr[0], dr[1], 1.0])

    X = np.array(derotated_px)                                              # N×3
    Y = np.array([[p["gt_lon"], p["gt_lat"]] for p in control_points])     # N×2
    A_T, _, _, _ = np.linalg.lstsq(X, Y, rcond=None)
    A_derot = A_T.T  # 2×3: maps derotated pixel → geo

    # Fold derotation into final matrix: A_combined maps original pixel → geo
    A_combined = A_derot @ D  # (2×3) @ (3×3) = 2×3

    logging.info(f"  Map rotation: {rotation_deg:.1f}°")
    logging.info(f"  Affine matrix A (combined):\n{A_combined}")
    return A_combined, rotation_deg


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

    # 6b — extract polygons per zone
    polygon_records: list[dict] = []
    calls_used = 0  # legend already counted separately
    zone_call_cap = _max_zone_calls if _max_zone_calls is not None else OPUS_CALLS_PER_PAGE

    for zone in legend:
        if calls_used >= zone_call_cap:
            logging.warning(
                f"  Reached {zone_call_cap}-call cap — skipping remaining zones"
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
    rotation_angle_deg: float | None = None,
    extra_properties: Optional[dict] = None,
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

        # Retrieve layer metadata stamped during run_pipeline (multi-PDF loop).
        future_layer_type = rec.get("_future_layer_type", "flu")
        layer_subtype = rec.get("_layer_subtype")
        acreage = rec.get("acreage")  # optional, populated if PDF labels carry area
        rec_source_pdf = rec.get("_source_pdf_url", source_pdf)
        rec_source_page = rec.get("_source_page_id", f"page_{map_page_idx}")

        # Zone code fields are null for annexation polygons.
        is_annexation = future_layer_type in ANNEXATION_LAYER_TYPES
        if is_annexation:
            gp_zone_code = None
            gp_zone_description = None
            gp_zone_normalized = None
        else:
            gp_zone_code = zone_code
            gp_zone_description = zone_desc
            gp_zone_normalized = _normalize_zone(zone_code, zone_desc, city_cfg)

        props: dict = {
            "city_slug": city_slug,
            "city_name": city_name,
            "future_layer_type": future_layer_type,
            "layer_subtype": layer_subtype,
            "gp_zone_code": gp_zone_code,
            "gp_zone_description": gp_zone_description,
            "gp_zone_normalized": gp_zone_normalized,
            "acreage": acreage,
            "jurisdiction": city_cfg["jurisdiction"],
            "source_pdf_url": rec_source_pdf,
            "source_page_id": rec_source_page,
            "extraction_method": "anthropic_vision_claude_opus_4_7_georeferenced",
            "confidence": confidence,
            "transform_residual_ft": round(rmse_ft, 1),
            "n_control_points": n_control_points,
            "rotation_angle_deg": round(rotation_angle_deg, 2) if rotation_angle_deg is not None else None,
            "extraction_date": today,
        }
        if extra_properties:
            props.update(extra_properties)
        feature = {
            "type": "Feature",
            "geometry": geom,
            "properties": props,
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
    rotation_angle_deg: float | None = None,
) -> Path:
    """Write erda_transform_validation.md (or <city>_transform_validation.md)."""
    city_slug = city_cfg["city_slug"]
    today = datetime.date.today().isoformat()
    out_path = out_dir / f"{city_slug}_transform_validation.md"

    rotation_line = (
        f"**Map rotation detected**: {rotation_angle_deg:.1f}°"
        if rotation_angle_deg is not None else ""
    )

    lines = [
        f"# {city_cfg['city_name']} GP FLU — Transform Validation Report",
        f"",
        f"**Date**: {today}",
        f"**Model**: {OPUS_MODEL}",
        f"**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced",
    ]
    if rotation_line:
        lines.append(rotation_line)
    lines += [
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

    # Tile-refinement section (present when Stage 2b was run)
    tile_refined_cps = [pt for pt in control_points_annotated if "_rough_px_x" in pt]
    if tile_refined_cps:
        lines += [
            f"",
            f"## Stage 2b Tile Refinement",
            f"",
            f"| # | Intersection | Rough px_x | Rough px_y | Refined px_x | Refined px_y | Shift (px) |",
            f"|---|---|---|---|---|---|---|",
        ]
        for i, pt in enumerate(control_points_annotated):
            if "_rough_px_x" not in pt:
                continue
            lines.append(
                f"| {i+1} | {pt['street_a']} "
                f"| {pt['_rough_px_x']:.0f} | {pt['_rough_px_y']:.0f} "
                f"| {pt['px_x']:.0f} | {pt['px_y']:.0f} "
                f"| {pt.get('_tile_shift_px', '—')} |"
            )

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

def _resolve_layer_type(layer_type_arg: str, pdf_path: Path) -> str:
    """
    Resolve --layer-type auto by scanning PDF text for annexation/MPC keywords.
    Returns a concrete future_layer_type value.
    """
    if layer_type_arg != "auto":
        return layer_type_arg  # caller already specified
    try:
        import fitz  # noqa: PLC0415
        doc = fitz.open(str(pdf_path))
        text = " ".join(page.get_text().lower() for page in doc)
        if any(kw in text for kw in _ANNEXATION_KEYWORDS):
            logging.info("  auto layer-type: detected annexation keywords → annexation_proposed")
            return "annexation_proposed"
        if any(kw in text for kw in _MPC_KEYWORDS):
            logging.info("  auto layer-type: detected MPC keywords → mpc_overlay")
            return "mpc_overlay"
    except Exception:
        pass
    logging.info("  auto layer-type: no keywords matched → flu")
    return "flu"


def _layer_type_to_future_layer_type(layer_type: str) -> str:
    """Map CLI --layer-type value to a future_layer_type string for GeoJSON properties."""
    mapping = {
        "flu": "flu",
        "annexation": "annexation_proposed",
        "annexation_proposed": "annexation_proposed",
        "annexation_existing_future": "annexation_existing_future",
        "annexation_existing_city": "annexation_existing_city",
        "mpc": "mpc_overlay",
        "mpc_overlay": "mpc_overlay",
    }
    return mapping.get(layer_type, "other")


def run_pipeline(
    city_slug: str,
    pdf_source: Optional[str],
    out_dir: Path,
    api_key: str,
    dpi: int = 300,
    manual_cps: Optional[list[dict]] = None,
    map_page_override: Optional[int] = None,
    layer_type: str = "flu",
    layer_subtype: Optional[str] = None,
    pdf_sources: Optional[list[str]] = None,
    extra_properties: Optional[dict] = None,
) -> dict:
    """
    Run the full 8-stage pipeline for a given city.

    layer_type: "flu" | "annexation" | "mpc" | "auto" — controls future_layer_type tagging.
    layer_subtype: optional free-text (e.g. "South Valley MPC", "2030 Annexation Boundary").
    pdf_sources: list of PDF URLs/paths for sectional GPs. When provided, overrides pdf_source
      and runs the pipeline once per PDF, accumulating features into a single output GeoJSON.
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

    # Build the ordered list of PDF sources to process.
    if pdf_sources:
        sources_to_run = pdf_sources
    else:
        single = pdf_source or city_cfg.get("pdf_url")
        if single is None:
            raise ValueError(
                f"No PDF source for {city_slug!r}. "
                "Provide --pdf, --pdf-urls, or update CITY_CONFIGS."
            )
        sources_to_run = [single]

    if len(sources_to_run) > 1:
        logging.info(f"Multi-PDF mode: {len(sources_to_run)} PDFs to aggregate")

    client = anthropic.Anthropic(api_key=api_key)

    all_projected: list[dict] = []
    all_legends: list[dict] = []
    last_rmse_ft = 0.0
    last_resolved_cps: list[dict] = []
    last_annotated_cps: list[dict] = []
    last_A: Optional[np.ndarray] = None
    last_rotation_deg: float = 0.0
    last_map_page_idx = 0
    last_tile_refine_stats: dict | None = None

    for source_idx, pdf_src in enumerate(sources_to_run):
        if len(sources_to_run) > 1:
            logging.info(f"\n--- PDF {source_idx + 1}/{len(sources_to_run)}: {pdf_src} ---")

        # Stage 0: Get PDF as local file
        if pdf_src.startswith("http"):
            cache_dir = out_dir.parent.parent / "_pdf_cache"
            pdf_path = download_pdf(pdf_src, cache_dir)
        else:
            pdf_path = Path(pdf_src)
            if not pdf_path.exists():
                raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Resolve layer type (only run auto-detection once, on first PDF)
        resolved_layer_type = (
            _resolve_layer_type(layer_type, pdf_path) if source_idx == 0
            else _layer_type_to_future_layer_type(
                _resolve_layer_type(layer_type, pdf_path)
                if layer_type == "auto" else layer_type
            )
        )
        if source_idx == 0:
            resolved_layer_type = _layer_type_to_future_layer_type(
                _resolve_layer_type(layer_type, pdf_path)
                if layer_type == "auto" else layer_type
            )
        future_layer_type = resolved_layer_type

        # Run stages
        page_images = stage1_rasterize(pdf_path, dpi=dpi)

        if manual_cps and source_idx == 0:
            # Bypass stages 2–3 only for the first PDF (manual CPs apply to one map).
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
                client, page_images, map_page_hints=map_page_hints, city_cfg=city_cfg
            )

            resolved_cps = stage3_ground_truth_lookup(control_candidates, city_cfg)

        # Stage 2b — tile refinement (gated by --tile-refine flag)
        if _tile_refine:
            resolved_cps, last_tile_refine_stats = stage2b_tile_refine(
                client, resolved_cps, page_images, map_page_idx
            )

        A, rotation_deg = stage4_fit_affine(resolved_cps)

        rmse_ft, annotated_cps = stage5_validate(A, resolved_cps, rmse_threshold_ft=_rmse_threshold)

        legend, polygon_records = stage6_extract_polygons(client, page_images, map_page_idx)

        projected = stage7_project_polygons(A, polygon_records, city_cfg, rmse_ft=rmse_ft)

        # Tag each projected record with its source PDF and layer metadata.
        for rec in projected:
            rec["_source_pdf_url"] = pdf_src
            rec["_source_page_id"] = f"page_{map_page_idx}"
            rec["_future_layer_type"] = future_layer_type
            rec["_layer_subtype"] = layer_subtype

        all_projected.extend(projected)
        all_legends.extend(legend)
        last_rmse_ft = rmse_ft
        last_resolved_cps = resolved_cps
        last_annotated_cps = annotated_cps
        last_A = A
        last_rotation_deg = rotation_deg
        last_map_page_idx = map_page_idx

    geojson_path = stage8_write_geojson(
        all_projected, all_legends, city_cfg, last_rmse_ft,
        n_control_points=len(last_resolved_cps),
        source_pdf=sources_to_run[0] if len(sources_to_run) == 1 else f"{len(sources_to_run)} PDFs aggregated",
        map_page_idx=last_map_page_idx,
        out_dir=out_dir,
        rotation_angle_deg=last_rotation_deg,
        extra_properties=extra_properties,
    )

    val_path = write_transform_validation(
        last_annotated_cps, last_rmse_ft, all_projected, last_A, city_cfg, out_dir,
        rotation_angle_deg=last_rotation_deg,
    )

    dump_api_log(out_dir, city_slug)

    zone_classes = sorted({
        _normalize_zone(r["zone_code"], r["zone_description"], city_cfg)
        for r in all_projected
        if r.get("_future_layer_type", "flu") not in ANNEXATION_LAYER_TYPES
    })
    total_cost = sum(e["cost_usd"] for e in _api_log)

    result = {
        "city_slug": city_slug,
        "layer_type": layer_type,
        "pdf_count": len(sources_to_run),
        "rmse_ft": round(last_rmse_ft, 1),
        "n_control_points": len(last_resolved_cps),
        "n_features": len(all_projected),
        "zone_classes_found": zone_classes,
        "total_cost_usd": round(total_cost, 4),
        "geojson_path": str(geojson_path),
        "validation_path": str(val_path),
        "confidence": "anchored_approximation" if last_rmse_ft <= 50 else "anchored_approximation_yellow",
        "tile_refine_stats": last_tile_refine_stats,
    }
    logging.info(f"\n{'='*60}")
    logging.info(f"PIPELINE COMPLETE — {city_slug}")
    logging.info(f"  Layer type: {layer_type}")
    logging.info(f"  PDFs processed: {len(sources_to_run)}")
    logging.info(f"  RMSE (last PDF): {last_rmse_ft:.1f} ft  ({len(last_resolved_cps)} control points)")
    logging.info(f"  Total features: {len(all_projected)}")
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
        description="Georeferenced GP layer extraction pipeline (Phase 18b-2 v2)"
    )
    parser.add_argument("--city", required=True, help="City slug (e.g. erda)")
    parser.add_argument("--pdf", default=None, help="PDF URL or local path (overrides config)")
    parser.add_argument(
        "--pdf-urls",
        default=None,
        help=(
            "Comma-separated list of PDF URLs/paths for sectional GPs "
            "(e.g. Grantsville). Overrides --pdf and config pdf_url. "
            "Features from all PDFs are aggregated into one output GeoJSON."
        ),
    )
    parser.add_argument(
        "--layer-type",
        default="flu",
        choices=["flu", "annexation", "mpc", "auto"],
        help=(
            "Layer type to tag extracted polygons with. "
            "'flu' = Future Land Use (default). "
            "'annexation' = proposed annexation boundary (zone code fields will be null). "
            "'mpc' = Master Planned Community / overlay. "
            "'auto' = infer from PDF text content."
        ),
    )
    parser.add_argument(
        "--layer-subtype",
        default=None,
        help="Optional free-text subtype label (e.g. 'South Valley MPC', '2030 Annexation Boundary').",
    )
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
    parser.add_argument(
        "--max-zone-calls",
        type=int,
        default=None,
        help=(
            f"Max Opus calls for polygon zone extraction per page (default: {OPUS_CALLS_PER_PAGE}). "
            "Increase for maps with large legends (e.g. 16+ zones). "
            "Each additional call costs ~$0.08-0.10."
        ),
    )
    parser.add_argument(
        "--tile-refine",
        action="store_true",
        help=(
            "Enable Stage 2b tile-refinement pass. For each control point, crops a "
            f"{TILE_SIZE}x{TILE_SIZE} px tile centered on the rough pixel estimate and "
            "asks Claude to locate the intersection with sub-50-pixel precision. "
            "Recommended for large-format maps (>=24x24 in) where single-image CP "
            "identification has high pixel uncertainty."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--extra-props",
        default=None,
        help=(
            "JSON object of extra properties to merge into every output feature. "
            'Example: \'{"flu_plan_vintage": "2013", "source_pdf_page": 34}\''
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    global _model_override, _rmse_threshold, _max_zone_calls, _tile_refine
    if args.model:
        _model_override = args.model
    _rmse_threshold = args.rmse_threshold
    if args.max_zone_calls is not None:
        _max_zone_calls = args.max_zone_calls
    _tile_refine = args.tile_refine

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

    # Parse multi-PDF list
    pdf_sources = None
    if args.pdf_urls:
        pdf_sources = [u.strip() for u in args.pdf_urls.split(",") if u.strip()]
        logging.info(f"Multi-PDF mode: {len(pdf_sources)} sources")

    # Parse extra properties (JSON string or path to JSON file)
    extra_properties = None
    if args.extra_props:
        raw_ep = args.extra_props.strip()
        if raw_ep.startswith("{") or raw_ep.startswith("["):
            extra_properties = json.loads(raw_ep)
        else:
            extra_properties = json.loads(Path(raw_ep).read_text(encoding="utf-8"))
        logging.info(f"Extra feature properties: {list(extra_properties)}")

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
            layer_type=args.layer_type,
            layer_subtype=args.layer_subtype,
            pdf_sources=pdf_sources,
            extra_properties=extra_properties,
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
