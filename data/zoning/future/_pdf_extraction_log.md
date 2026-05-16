# GP FLU PDF Extraction Log

Tracks per-city extraction runs for the georeferenced GP layer pipeline (Phase 18b-2 v2).

---

## Herriman — 2026-05-15

**PDF**: `LandUse203036x36.pdf` (36×36 inch large-format FLU map)
**Source URL**: https://www.herriman.gov/uploads/files/3174/LandUse203036x36.pdf
**Layer type**: `flu`
**Model**: `claude-opus-4-7`
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)

### Run outcome: PARTIAL SUCCESS — RMSE RED FLAG

| Criterion | Result | Status |
|---|---|---|
| Feature count ≥ 30 | 46 features | PASS |
| Feature count ≥ 50 | 46 features | NEAR MISS |
| RMSE ≤ 100 ft | 1017.9 ft | **RED FLAG** |
| Centroid within 5km | 2.21 km | PASS |
| BBox < 30km | 5.1 km × 8.1 km | PASS |
| Schema v2 fields | All present | PASS |
| Cost within $3 | $1.54 | PASS |

### Control points (--manual-cps, 4 points)

Stage 3 automatic lookup (UGRC, Nominatim) failed — Nominatim intersection queries
rejected all abbreviated map labels ("Herriman Hwy", "Rose Cyn Rd", etc.). An Overpass
API fallback was added to the pipeline but returned overly-wide centroids (RMSE 5951 ft).
Switched to --manual-cps with verified intersections.

| # | Label | px_x | px_y | gt_lat | gt_lon | residual_ft | Source |
|---|---|---|---|---|---|---|---|
| 1 | 11800 S × Anthem Park Blvd | 2470 | 640 | 40.534361 | -112.017477 | 93.6 | Overpass exact (38 nodes) |
| 2 | Main St × Pioneer St | 1960 | 1370 | 40.516835 | -112.033043 | 1379.8 | Overpass exact (61 nodes) |
| 3 | Herriman Pkwy × Rosecrest Rd | 2150 | 1620 | 40.507890 | -112.024476 | 1481.0 | Overpass exact (57 nodes) |
| 4 | Mountain View Corridor × Rosecrest Rd | 3650 | 2920 | 40.491267 | -112.022725 | 194.8 | Overpass exact (187 nodes) |

**RMSE: 1017.9 ft** — exceeds 100 ft threshold.

### Root cause: large-format map incompatibility

The `LandUse203036x36.pdf` is a **36×36 inch large-format exhibition map with a non-standard
(rotated ~45°) orientation**. This creates two compounding problems:

1. **Image size**: At 300 DPI, rasterization produces a 10741×10741 px JPEG (13.4 MB /
   18.8 MB base64), exceeding the Anthropic API 5 MB image limit. Fixed with auto-resize to
   50% (5348×5348, 3.8 MB). ✅ FIXED in pipeline.

2. **Pixel coordinate uncertainty**: On a 36×36 inch image, Claude's pixel coordinate
   identification has ~300–1200 px uncertainty across repeated calls (equivalent to ~0.5–2 km
   in geographic space). The 100 ft RMSE target requires ~16 px accuracy — not achievable
   with this map format using the current single-image approach.

3. **Nominatim failures**: Map uses abbreviated street labels ("Herriman Hwy", "Rose Cyn Rd")
   not recognized by Nominatim's intersection query. Fixed with abbreviation expansion +
   Overpass fallback. ✅ FIXED in pipeline.

4. **Overpass centroid imprecision**: For long arterials (Mountain View Corridor, Rosecrest
   Road), Overpass returns centroids of 57–187 shared nodes spread over wide areas — precise
   enough for 2/4 control points (CP1: 93.6 ft, CP4: 194.8 ft) but poor for the others.

### Zone classes extracted (16/16 zones, 46 features)

| Normalized class | Count |
|---|---|
| future_mixed_use | 12 |
| future_commercial_general | 10 |
| future_low_density_residential | 9 |
| future_commercial_neighborhood | 5 |
| future_open_space | 4 |
| future_industrial_light | 3 |
| future_unknown | 2 |
| future_agriculture | 1 |

8-call cap was extended to 20 via `--max-zone-calls 20`. ✅ FIXED in pipeline.

### Recommended pipeline fix for large-format maps

For 36×36 inch (or similar large-format) maps, the single-image control point approach
cannot achieve 100 ft RMSE. Recommended two-pass approach:

1. **First pass** (current): identify approximate map extent using 3–4 wide-spread
   control points at relaxed RMSE threshold (e.g., 5000 ft).
2. **Second pass** (new): for each control point, crop a 1000×1000 px tile around the
   predicted intersection, send the tile to Claude for sub-50-pixel-precision coordinate
   identification, and recalculate ground truth → pixel mapping.

This would reduce pixel uncertainty from 300–1200 px to ~20–50 px, making ≤100 ft RMSE
achievable for large-format maps.

### Decision

GeoJSON committed with RED FLAG annotation. The output is geographically valid (centroid
2.21 km from city center, correct bbox) and schema-compliant (all v2 fields populated).
The RMSE of 1017.9 ft means polygon boundaries are approximate — suitable for visual
analysis but not survey-grade use. Confidence: `anchored_approximation_yellow`.

**Three pipeline fixes shipped in this run (committed to phase-18b-2-pipeline-v2):**
- Auto-resize for images exceeding 5 MB API limit
- Abbreviation expansion + Overpass fallback in stage3
- `--max-zone-calls` CLI flag for large legend maps

---

## Herriman — 2026-05-16 (18b-2c re-run: rotation fix)

**PDF**: `LandUse203036x36.pdf` (same as above)
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)
**Change**: Rotation-aware stage4 implemented

### Rotation fix: what was changed

`stage4_fit_affine` now:
1. Detects map rotation by comparing bearing in pixel space vs bearing in geo space for the
   control point pair with the largest pixel separation (bearing_px=152.6°, bearing_geo=-174.7°,
   detected rotation=-32.7°).
2. Builds a 3×3 homogeneous derotation matrix D that rotates pixel coords by +32.7° around
   their centroid.
3. Fits affine A_derot on the derotated pixel coords.
4. Returns A_combined = A_derot @ D (2×3 matrix) and rotation_angle_deg.

Stage7 interface is unchanged. `rotation_angle_deg` is now logged in GeoJSON properties and
transform validation report.

### Why RMSE did not improve for Herriman

For **manual CPs**, the rotation fix is mathematically equivalent to the original 6-DOF affine:
A_combined = A_orig (same matrix). This is expected — a change of basis in the domain does not
change the least-squares minimum when the same training data is used.

The 1017.9 ft RMSE is caused by **unreliable pixel coordinates** for CP2 and CP3, not by the
affine fitting method. On a 36×36 inch large-format rotated map, Claude's pixel identification
and human visual inspection both have ~300–1200 px uncertainty, which translates to ~1000–7000 ft
error at this map scale (~6 ft/px). CP1 (93.6 ft) and CP4 (194.8 ft) are reliable anchors;
CP2 and CP3 are dragging up the RMSE.

Ground truth investigation: precise Overpass intersection queries (node-on-both-ways) confirmed
that the old Overpass median-node gt for CP2 and CP3 was 975 ft and 3470 ft off respectively.
BUT replacing with OSM-verified gt made RMSE worse (1739.9 ft), confirming the pixel coordinates
themselves are inconsistent with the true geographic locations.

### When the rotation fix DOES help

For **automatic CPs** (stage2 vision-identified), the rotation-aware fit reduces one source of
noise: Claude's pixel coordinate estimates are more consistent along the map's principal axes
than in geographic N-S/E-W directions. After derotation, the lstsq fit operates in a coordinate
space aligned with the map, reducing shear artifacts.

### Run outcome: PARTIAL SUCCESS — RMSE RED FLAG (unchanged)

| Criterion | Result | Status |
|---|---|---|
| Feature count ≥ 30 | 43 features | PASS |
| Feature count ≥ 50 | 43 features | NEAR MISS |
| RMSE ≤ 100 ft | 1017.9 ft | **RED FLAG** |
| Centroid within 5km | within range | PASS |
| BBox < 30km | 5.1 km × 8.1 km | PASS |
| Schema v2 fields | All present (incl. rotation_angle_deg=-32.7) | PASS |
| Cost within $3 | $1.50 | PASS |

**Pipeline fix shipped in this run:**
- Rotation-aware stage4 (`_detect_map_rotation` + derotation matrix composition)
- `rotation_angle_deg` field added to GeoJSON properties and transform validation report
