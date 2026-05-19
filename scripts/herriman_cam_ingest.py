"""
herriman_cam_ingest.py — Phase 18b-2d-2 Herriman Cam-KMZ pipeline.

Replaces the failed algorithmic-georef run with Cam's manually-placed KMZ
as the georeference source. Stages 1–4:

  1. KMZ ingestion → georeferenced GeoTIFF (herriman_cam_georef.tif)
  2. Legend extraction from legend_source.png → herriman_legend.json (16 zones)
  3. Per-parcel LAB sampling → herriman_gp.geojson + herriman_gp_parcel_table.csv
  4. GeoJSON → herriman_gp.kmz (simplekml, folders by zone, 50% opacity)

Design:
  - Gitignored files (GeoTIFF, KMZ, PDFs) live in the main repo data dirs.
  - Tracked outputs (GeoJSON, CSV, legend JSON) go to the worktree data dirs.
  - parcel CSV lookup is redirected to the main repo via REPO_ROOT patch.

Usage:
  py -3 scripts/herriman_cam_ingest.py [--verbose] [--skip-legend]
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import anthropic
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import Affine
import simplekml

# ---------------------------------------------------------------------------
# Path layout
# ---------------------------------------------------------------------------

# The script lives in <WORKTREE>/scripts/ so parents[1] = worktree root.
WORKTREE = Path(__file__).resolve().parents[1]

# Main repo has gitignored data files (parcels, _pdf_cache, KMZ outputs).
MAIN_REPO = Path("C:/Users/camsr/code/tooele-land-intel")

# Inputs (gitignored, in main repo)
KMZ_PATH    = MAIN_REPO / "data/_pdf_cache/herriman/Herriman_Zoning.kmz"
LEGEND_IMG  = MAIN_REPO / "data/_pdf_cache/herriman/legend_source.png"
EXTRA_PROPS = MAIN_REPO / "data/_pdf_cache/herriman/_extra_props.json"

# Gitignored outputs (main repo)
GEOTIFF_PATH = MAIN_REPO / "data/_pdf_cache/herriman/herriman_cam_georef.tif"
KMZ_OUT      = MAIN_REPO / "data/zoning/future/herriman_gp.kmz"

# Tracked outputs (worktree — will be committed)
LEGEND_JSON = WORKTREE / "data/zoning/future/legends/herriman_legend.json"
OUT_DIR     = WORKTREE / "data/zoning/future"

# Redirect parcel CSV lookup to main repo (patched into imported module below)
sys.path.insert(0, str(WORKTREE / "scripts"))
import gp_raster_sample_extract as grse
import gp_pdf_extract as gpe
grse.REPO_ROOT = MAIN_REPO  # load_parcels_in_bbox uses this at call-time

CITY_CFG = gpe.CITY_CONFIGS["herriman"]
COUNTY   = "salt_lake"

# ---------------------------------------------------------------------------
# Legend ground-truth (16 categories, spec-confirmed)
# ---------------------------------------------------------------------------

LEGEND_CATEGORIES = [
    "Hillside/Rural Residential (0.5-1.7 du/acre)",
    "Agricultural Residential (1.8-3.0 du/acre)",
    "Low Density Residential (1.8-2.5 du/acre)",
    "Single Family Residential (2.6-4.5 du/acre)",
    "Medium Density Residential (4.6-8 du/acre)",
    "High Density Residential (8-20 du/acre)",
    "Mixed Use",
    "Mixed Use - Towne Center",
    "Commercial",
    "Light Industrial/Business Park",
    "Public/Institutional/Cultural/Schools",
    "Quasi-Public/Utilities",
    "Military Operation",
    "Resort/Recreational",
    "Open Space",
    "Parks and Recreation",
]

# ---------------------------------------------------------------------------
# Stage 1 — KMZ → GeoTIFF
# ---------------------------------------------------------------------------

def stage1_kmz_to_geotiff(kmz_path: Path, out_tif: Path) -> tuple[np.ndarray, dict]:
    """Extract the map PNG from the KMZ, georeference it, write GeoTIFF.

    Returns (A, bounds) where A is the 2×3 affine matrix mapping
    [px_x, px_y, 1] → [lon, lat] and bounds = {north,south,east,west}.
    """
    logging.info(f"Stage 1: opening {kmz_path.name}")
    with zipfile.ZipFile(kmz_path) as z:
        names = z.namelist()
        logging.info(f"  KMZ contents: {names}")

        kml_text = z.read("doc.kml").decode("utf-8")

        png_names = [n for n in names if n.lower().endswith(".png")]
        if not png_names:
            raise FileNotFoundError(f"No PNG found in KMZ. Contents: {names}")
        png_name = png_names[0]
        logging.info(f"  Map image: {png_name}")
        png_bytes = z.read(png_name)

    # Parse LatLonBox
    def _coord(tag: str) -> float:
        m = re.search(rf"<{tag}>\s*([-0-9.]+)\s*</{tag}>", kml_text)
        if not m:
            raise ValueError(f"<{tag}> not found in doc.kml")
        return float(m.group(1))

    north = _coord("north")
    south = _coord("south")
    east  = _coord("east")
    west  = _coord("west")
    logging.info(f"  LatLonBox: N={north} S={south} E={east} W={west}")

    # Validate against spec
    assert abs(north - 40.5421) < 0.001, f"north mismatch: {north}"
    assert abs(south - 40.4425) < 0.001, f"south mismatch: {south}"
    assert abs(east  - -111.9241) < 0.001, f"east mismatch: {east}"
    assert abs(west  - -112.0941) < 0.001, f"west mismatch: {west}"
    logging.info("  Bounds validated against spec ✓")

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape
    logging.info(f"  Image size: {w}×{h} px")

    # Simple axis-aligned affine: col→lon, row→lat
    # Affine(a, b, c, d, e, f): x = a*col + b*row + c, y = d*col + e*row + f
    a = (east - west) / w   # lon per pixel (positive)
    e = (south - north) / h  # lat per pixel (negative)
    transform = Affine(a, 0.0, west, 0.0, e, north)

    profile = {
        "driver": "GTiff",
        "height": h, "width": w,
        "count": 3, "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
    }
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(arr.transpose(2, 0, 1))  # (bands, H, W)
    logging.info(f"  Wrote GeoTIFF: {out_tif}")

    # Build A matrix matching gp_raster_sample_extract convention
    A = np.array([
        [a,   0.0, west],
        [0.0, e,   north],
    ])

    bounds = {"north": north, "south": south, "east": east, "west": west}
    return A, bounds


# ---------------------------------------------------------------------------
# Stage 2 — Legend extraction via Claude vision
# ---------------------------------------------------------------------------

LEGEND_PROMPT = """\
This is the legend from Herriman City's General Plan Future Land Use Map (Map 7, 2013 Amendment).

The legend contains exactly 16 zone categories. For each category listed below, identify the \
representative RGB color of the center of its color swatch (NOT borders, NOT background white, \
NOT label text — only the filled interior of the swatch rectangle).

Return the RGB for each category in the exact order listed. Be precise: read the swatch center \
pixel color, not an average of the whole swatch area.

Categories (return in this exact order, using these exact labels):
{categories}

Return ONLY a valid JSON array, no markdown fences, no explanation:
[{{"rgb": [r, g, b], "label": "<label as listed above>"}}, ...]

Exactly 16 entries. RGB values are integers 0–255.
"""


def stage2_extract_legend(
    client: anthropic.Anthropic,
    legend_img_path: Path,
    out_json: Path,
    force: bool = False,
) -> list[dict]:
    """Run one Claude vision call to extract RGB for each of the 16 legend categories.

    If out_json already exists and has exactly 16 entries and force=False, reuse it.
    """
    if out_json.exists() and not force:
        with out_json.open() as f:
            existing = json.load(f)
        if len(existing) == 16:
            logging.info(f"Stage 2: reusing cached legend ({len(existing)} entries): {out_json}")
            return existing
        logging.info(f"Stage 2: cached legend has {len(existing)} entries — re-extracting")

    logging.info(f"Stage 2: running Claude vision on {legend_img_path.name}")
    img_bytes = legend_img_path.read_bytes()
    img_b64   = base64.standard_b64encode(img_bytes).decode()

    # Determine media type from extension
    suffix = legend_img_path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"

    category_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(LEGEND_CATEGORIES))
    prompt = LEGEND_PROMPT.format(categories=category_list)

    t0 = time.time()
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": img_b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    elapsed = time.time() - t0

    raw_text = resp.content[0].text.strip()
    logging.info(f"  Vision call: {elapsed:.1f}s, tokens in={resp.usage.input_tokens} out={resp.usage.output_tokens}")

    # Strip accidental markdown fences
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

    entries = json.loads(raw_text)
    if not isinstance(entries, list):
        raise ValueError(f"Expected JSON array, got {type(entries)}: {raw_text[:200]}")
    if len(entries) != 16:
        raise ValueError(f"Expected 16 legend entries, got {len(entries)}: {entries}")

    # Validate structure
    for e in entries:
        assert "rgb" in e and "label" in e, f"Bad entry: {e}"
        assert len(e["rgb"]) == 3, f"rgb must be 3-element: {e}"
        assert all(0 <= c <= 255 for c in e["rgb"]), f"rgb out of range: {e}"

    out_json.parent.mkdir(parents=True, exist_ok=True)
    with out_json.open("w") as f:
        json.dump(entries, f, indent=2)
    logging.info(f"  Wrote legend → {out_json} ({len(entries)} entries)")
    return entries


# ---------------------------------------------------------------------------
# Stage 3 — Per-parcel color sampling
# ---------------------------------------------------------------------------

def stage3_sample_parcels(
    geotiff_path: Path,
    A: np.ndarray,
    legend_entries: list[dict],
    extra_props: dict,
    out_dir: Path,
) -> tuple[Path, Path, list[dict], dict]:
    """Load parcels within KMZ bounds, sample raster, write GeoJSON + CSV.

    Uses parcel_city_whitelist from CITY_CFG plus blank-city parcels (federal land
    such as Camp Williams that lack a parcel_city tag in the UGRC LIR CSV).

    Returns (csv_path, geojson_path, results, city_breakdown).
    """
    logging.info("Stage 3: loading parcels…")
    bbox = CITY_CFG["bbox"]

    # Load all parcels in (widened) bbox from the main-repo parcel CSV
    parcels_bbox = grse.load_parcels_in_bbox(COUNTY, bbox)
    logging.info(f"  Parcels in bbox: {len(parcels_bbox):,}")

    # Include whitelist cities + blank/null city tags (Camp Williams federal land etc.)
    whitelist = {c.lower() for c in CITY_CFG.get("parcel_city_whitelist", ["herriman"])}
    parcels = [
        p for p in parcels_bbox
        if p.get("parcel_city", "").strip().lower() in whitelist
        or not p.get("parcel_city", "").strip()
    ]
    logging.info(f"  Parcels after whitelist+blank filter: {len(parcels):,}")

    if not parcels:
        raise RuntimeError("No parcels found — check parcel CSV and city filter")

    logging.info("Stage 3: sampling parcels…")
    results = grse.stage4_sample_parcels(
        geotiff_path, A, legend_entries, parcels, bbox,
    )
    logging.info(f"  Sampled {len(results):,} parcels")

    # Source-city breakdown for reporting
    from collections import Counter as _Counter
    city_breakdown = _Counter(
        r.get("parcel_city", "").strip() or "(blank)" for r in results
    )

    pipeline_meta = {
        "cp_source": "cam_kmz",
        "cp_count": 0,
        "cp_source_path": str(KMZ_PATH),
        "rmse_ft": 0.0,
        "rotation_deg": 0.0,
    }

    csv_path, geojson_path = grse.stage5_write_outputs(
        results, legend_entries, CITY_CFG, out_dir,
        extra_props, LEGEND_JSON, pipeline_meta,
    )
    return csv_path, geojson_path, results, city_breakdown


# ---------------------------------------------------------------------------
# Stage 4 — GeoJSON → KMZ
# ---------------------------------------------------------------------------

def _rgb_to_kml(r: int, g: int, b: int, alpha_hex: str = "80") -> str:
    return f"{alpha_hex}{b:02x}{g:02x}{r:02x}"


def stage4_geojson_to_kmz(
    geojson_path: Path,
    legend_entries: list[dict],
    kmz_out: Path,
) -> None:
    """Convert per-parcel GeoJSON → KMZ grouped by zone, 50% opacity."""
    logging.info(f"Stage 4: converting {geojson_path.name} → KMZ…")

    with geojson_path.open() as f:
        gj = json.load(f)
    features = gj["features"]
    logging.info(f"  {len(features):,} features")

    legend_map = {e["label"]: _rgb_to_kml(*e["rgb"]) for e in legend_entries}
    UNCLASSIFIED = "80808080"

    kml = simplekml.Kml(name="Herriman GP FLU (Phase 18b-2d-2, Cam-KMZ, 2026-05-18)")
    kml.document.description = (
        "Source: Map 7 — Future Land Use 2025, 2013 GP Amendment, page 34\n"
        "Georeferenced manually by Cam in Google Earth Pro\n"
        "Pipeline: Phase 18b-2d-2 raster-sample\n"
        f"Extraction date: 2026-05-18\n"
        f"Features: {len(features)}"
    )

    zone_features: dict[str, list] = defaultdict(list)
    for feat in features:
        zone = feat["properties"].get("sampled_zone") or "Unclassified"
        zone_features[zone].append(feat)

    style_cache: dict = {}

    def _get_style(zone: str):
        if zone in style_cache:
            return style_cache[zone]
        fill = legend_map.get(zone, UNCLASSIFIED)
        s = simplekml.Style()
        s.polystyle.color   = fill
        s.polystyle.fill    = 1
        s.polystyle.outline = 1
        s.linestyle.color   = "60000000"
        s.linestyle.width   = 1
        style_cache[zone] = s
        return s

    total_pm = 0
    skipped  = 0

    for zone in sorted(zone_features):
        folder = kml.newfolder(name=zone)
        style  = _get_style(zone)
        for feat in zone_features[zone]:
            geom = feat.get("geometry")
            if not geom:
                skipped += 1
                continue
            props = feat["properties"]
            desc  = (
                f"<table>"
                + "".join(
                    f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
                    for k, v in [
                        ("parcel_id",              props.get("parcel_id", "")),
                        ("sampled_zone",           props.get("sampled_zone", "")),
                        ("extraction_method",      props.get("extraction_method", "")),
                        ("color_match_confidence", props.get("color_match_confidence", "")),
                        ("lab_distance",           props.get("lab_distance", "")),
                        ("flu_plan_vintage",       props.get("flu_plan_vintage", "")),
                        ("source_pdf_filename",    props.get("source_pdf_filename", "")),
                        ("source_pdf_page",        props.get("source_pdf_page", "")),
                    ]
                    if v not in ("", None)
                )
                + "</table>"
            )
            try:
                gtype = geom["type"]
                rings_list = []
                if gtype == "Polygon":
                    rings_list = [(geom["coordinates"][0], geom["coordinates"][1:])]
                elif gtype == "MultiPolygon":
                    for poly in geom["coordinates"]:
                        rings_list.append((poly[0], poly[1:]))
                for outer, holes in rings_list:
                    pol = folder.newpolygon(name=zone, description=desc)
                    pol.outerboundaryis = [(c[0], c[1]) for c in outer]
                    if holes:
                        pol.innerboundaryis = [[(c[0], c[1]) for c in h] for h in holes]
                    pol.style = style
                    total_pm += 1
            except Exception as ex:
                logging.warning(f"  geometry error on {props.get('parcel_id','?')}: {ex}")
                skipped += 1

    kmz_out.parent.mkdir(parents=True, exist_ok=True)
    kml.savekmz(str(kmz_out))
    size_mb = kmz_out.stat().st_size / 1_048_576
    logging.info(f"  Saved KMZ: {kmz_out} ({size_mb:.1f} MB, {total_pm:,} placemarks, {skipped} skipped)")
    if size_mb > 30:
        logging.warning("  KMZ > 30 MB — consider geometry simplification")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skip-legend", action="store_true",
                   help="reuse existing herriman_legend.json even if it needs refresh")
    p.add_argument("--skip-georef", action="store_true",
                   help="skip Stage 1 (KMZ→GeoTIFF); read affine from existing herriman_cam_georef.tif")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Verify inputs
    for path, label in [
        (KMZ_PATH, "Herriman_Zoning.kmz"),
        (LEGEND_IMG, "legend_source.png"),
        (EXTRA_PROPS, "_extra_props.json"),
    ]:
        if not path.exists():
            sys.exit(f"ERROR: {label} not found at {path}")
    logging.info("All inputs verified ✓")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try loading from main repo .env
        env_path = MAIN_REPO / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set and not found in .env")

    client = anthropic.Anthropic(api_key=api_key)
    t_start = time.time()

    with EXTRA_PROPS.open() as f:
        extra_props = json.load(f)
    extra_props["flu_source_jurisdiction"] = "Herriman"

    # Stage 1
    if args.skip_georef:
        logging.info("=" * 60)
        logging.info("STAGE 1 — skipped (--skip-georef); reading affine from existing GeoTIFF")
        if not GEOTIFF_PATH.exists():
            sys.exit(f"ERROR: --skip-georef set but GeoTIFF not found: {GEOTIFF_PATH}")
        with rasterio.open(GEOTIFF_PATH) as src:
            t = src.transform
            A = np.array([[t.a, t.b, t.c], [t.d, t.e, t.f]])
        logging.info(f"  A matrix read from GeoTIFF:\n{A}")
    else:
        logging.info("=" * 60)
        logging.info("STAGE 1 — KMZ → GeoTIFF")
        A, bounds = stage1_kmz_to_geotiff(KMZ_PATH, GEOTIFF_PATH)
        logging.info(f"  A matrix:\n{A}")

    # Stage 2
    logging.info("=" * 60)
    logging.info("STAGE 2 — Legend extraction")
    legend_entries = stage2_extract_legend(
        client, LEGEND_IMG, LEGEND_JSON, force=not args.skip_legend
    )

    # Stage 3
    logging.info("=" * 60)
    logging.info("STAGE 3 — Per-parcel sampling")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path, geojson_path, results, city_breakdown = stage3_sample_parcels(
        GEOTIFF_PATH, A, legend_entries, extra_props, OUT_DIR,
    )

    # Stage 4
    logging.info("=" * 60)
    logging.info("STAGE 4 — GeoJSON → KMZ")
    stage4_geojson_to_kmz(geojson_path, legend_entries, KMZ_OUT)

    # Summary
    elapsed = time.time() - t_start
    sampled = sum(1 for r in results if r["sampled_zone"] is not None)
    unknown = sum(1 for r in results if r["sampled_zone"] is None)
    zone_hist = Counter(r["sampled_zone"] for r in results if r["sampled_zone"])
    pct_mut = 100.0 * zone_hist.get("Mixed Use - Towne Center", 0) / max(sampled, 1)

    print()
    print("=" * 70)
    print("Phase 18b-2d-2 — Herriman Cam-KMZ extraction — COMPLETE")
    print("=" * 70)
    print(f"GeoTIFF  : {GEOTIFF_PATH}")
    print(f"Legend   : {LEGEND_JSON} ({len(legend_entries)} entries)")
    print(f"GeoJSON  : {geojson_path}")
    print(f"CSV      : {csv_path}")
    print(f"KMZ      : {KMZ_OUT}")
    print()
    print(f"Parcels sampled (with zone) : {sampled:,}")
    print(f"Parcels marked unknown      : {unknown:,}")
    print(f"Coverage %                  : {100.0*sampled/max(sampled+unknown,1):.1f}%")
    print()
    print("Zone distribution:")
    for label, n in zone_hist.most_common():
        print(f"  {label:<60s} {n:6d}  ({100.0*n/max(sampled,1):5.1f}%)")
    print()
    print(f"Mixed Use Towne Center: {pct_mut:.1f}%  (prior single-city run: 8.8%)")
    print()
    print("Source parcel_city breakdown:")
    total_results = len(results)
    for city, n in city_breakdown.most_common():
        print(f"  {city:<50s} {n:6,}  ({100.0*n/max(total_results,1):5.1f}%)")
    print(f"Runtime: {elapsed:.0f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
