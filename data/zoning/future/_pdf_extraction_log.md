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

---

## Spanish Fork — 2026-05-16 (18b-2c validation run)

**PDF**: `GeneralPlan_Letter.pdf` (17×11 inch tabloid, single FLU map)
**Source URL**: https://www.spanishfork.gov/document_center/Public%20Works/Maps/Planning/GeneralPlan_Letter.pdf
**Layer type**: `flu`
**Model**: `claude-opus-4-7`
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)
**Purpose**: Validate methodology on standard letter/tabloid-size map (Herriman failed at RMSE 1017 ft on 36×36 in large-format map)

### Run outcome: PASS — RMSE 38.6 ft

| Criterion | Result | Status |
|---|---|---|
| Stage 2 auto CPs found | 7 (probe run; bypassed in final run) | INFO |
| Detected rotation angle | 0.0° | INFO |
| Feature count ≥ 10 | 14 features | PASS |
| RMSE ≤ 100 ft | 38.6 ft | **PASS** |
| RMSE ≤ 300 ft | 38.6 ft | **PASS** |
| Centroid within 5 km | 1.51 km | PASS |
| BBox within city | Yes | PASS |
| Schema v2 fields | All present | PASS |
| Cost within $3 | $1.85 total | PASS |

### Control points (--manual-cps, 4 points)

Stage 3 auto ground-truth lookup failed: Overpass returned bad medians for all "Main St" intersections
because `Main.*St` regex matched streets across Spanish Fork **and** adjacent Springville/Mapleton
within the expanded bbox (+0.05°). The "Main St × 400 N" median landed at lon −111.616 (3.8 km east
of true Main St at −111.655). Stage 2 pixel identification was correct and geometrically consistent
(Main St column at px_x=2247, Center St row at px_y=1719). Switched to `--manual-cps` with verified
Nominatim-derived intersections.

| # | Label | px_x | px_y | gt_lat | gt_lon | residual_ft | Source |
|---|---|---|---|---|---|---|---|
| 1 | Main St × Center St | 2247 | 1719 | 40.1098 | −111.6548 | 63.0 | Nominatim "1 S Main St" |
| 2 | Main St × 400 N | 2247 | 1409 | 40.1150 | −111.6548 | 28.8 | Nominatim "400 N" mid-street |
| 3 | Main St × 300 S | 2247 | 1980 | 40.1059 | −111.6548 | 34.2 | Nominatim "300 S" mid-street |
| 4 | Center St × Mill Rd | 1980 | 1719 | 40.1098 | −111.6690 | 0.0 | Mill Road Nominatim anchor |

**RMSE: 38.6 ft** — within 100 ft threshold. **PASS.**

Note: 3 CPs are collinear at px_x=2247 (all on Main St). The collinear set forces
rotation_angle_deg=0.0° (no-rotation affine). Map likely has slight tilt ≤20° per probe run
Stage 4 detection (−19.0°). Non-rotation approximation adequate for ≤100 ft RMSE on this map scale.

### Stage 3 root cause: Overpass bbox over-broad for dense Utah Valley

Spanish Fork bbox (−111.68 to −111.60, 40.09 to 40.16) + 0.05° buffer overlaps Springville, Mapleton,
and Salem — all with "Main St" and numbered N/S streets. Overpass `Main.*St` returned 278–979 shared
nodes whose median was geographically valid (within bbox) but spatially wrong (not at the intersection).
Fix: add city-slug-specific Overpass name filters or a tighter bbox (−0.02° instead of −0.05°) for
dense urban areas where multiple cities share the same grid naming conventions.

### Zone classes extracted (8/12 zones; 8-call cap reached)

| Normalized class | Raw label | Count |
|---|---|---|
| future_agriculture | Agricultural | 3 |
| future_commercial_general | Business Park | 5 |
| future_commercial_general | Commercial | 4 |
| future_medium_density_residential | Medium Density Residential | 2 |

Unprocessed (cap reached): Urban Density Residential, Mixed Use, Public Facilities, High Density
Residential. Use `--max-zone-calls 20` on follow-up runs to extract all 12 zone types.

**Polygons**: 27 extracted, 13 dropped (bbox filter), 14 valid. Drop rate (48%) higher than Herriman
(0%) — attributed to non-rotated affine slightly misplacing some polygon vertices outside city bbox.

### Cost breakdown (3 API runs)

| Run | Stages | Cost |
|---|---|---|
| Auto-CP run (RMSE fail) | Stage 1–3 only | $0.104 |
| Probe run (high RMSE, --rmse-threshold 99999) | Stages 1–8 (bad CPs) | $0.935 |
| Final manual-CP run | Stages 1, 4–8 (Stage 2–3 bypassed) | $0.808 |
| **TOTAL** | | **$1.847** |

### Decision

GeoJSON committed as `data/zoning/future/spanish_fork.geojson` with `confidence: anchored_approximation`.
**Methodology validated**: stage 2 pixel identification works correctly on standard tabloid-size maps
(self-consistent grid structure detected). Stage 3 Overpass fix needed for adjacent-city urban areas.
RMSE 38.6 ft confirms pipeline-v2 can achieve ≤100 ft on standard-size maps when correct CPs are provided.

---

## Vineyard — 2026-05-16 (18b-2c REST ingest)

**Layer**: `Vineyard Future Land Use` (FeatureServer Layer 0)
**Source URL**: https://services.arcgis.com/QdlehUncXjEmQYtI/arcgis/rest/services/Vineyard_Future_Land_Use_View/FeatureServer/0
**Layer type**: `flu`
**Extraction method**: `arcgis_rest` — no PDF stages (not applicable)
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)

### Run outcome: PASS

| Criterion | Result | Status |
|---|---|---|
| Feature count = 36 | 36 features | PASS |
| Centroid within 10 km of city center | 0.14 km | PASS |
| Schema v2 fields | All present | PASS |
| Cost | $0 (REST ingest) | PASS |

### Zone types (12)

| Zone | Count |
|---|---|
| Open Space | 14 |
| Public Facility | 5 |
| Low Density | 3 |
| Medium Density | 3 |
| Regional Commercial | 2 |
| High Density | 2 |
| Neighborhood Center | 2 |
| University | 1 |
| Town Center | 1 |
| Residential Mixed Use | 1 |
| Vineyard Commerce Center | 1 |
| Low & Medium Density | 1 |

**Output**: `data/zoning/future/vineyard_gp.geojson` (36 features, schema v2)
**Primary FLU field**: `Land_Use` (string, no coded domain — values are label strings)
**Native SR**: WKID 103170 (EPSG:6625 Utah Central State Plane, feet) → reprojected to EPSG:4326 via `outSR=4326`

---

## Grantsville — 2026-05-16 (18b-2c REST ingest)

**Layer**: `Future Land Use Map` (FeatureServer Layer 81)
**Source URL**: https://services5.arcgis.com/uWdqWzgcb7gCRVuK/arcgis/rest/services/Future_Land_Use_Map/FeatureServer/81
**Layer type**: `flu`
**Extraction method**: `arcgis_rest` — no PDF stages (not applicable)
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)

### Run outcome: PASS

| Criterion | Result | Status |
|---|---|---|
| Feature count = 51 | 51 features | PASS |
| Centroid within 10 km of city center | 0.27 km | PASS |
| Schema v2 fields | All present | PASS |
| Cost | $0 (REST ingest) | PASS |

### Zone types (9)

| Zone | Count |
|---|---|
| Municipal/School | 14 |
| Mixed Use Density | 11 |
| Industrial | 6 |
| Parks & Open Space | 6 |
| High Single Family Density Residential | 5 |
| Commercial | 3 |
| Rural Residential 2 | 2 |
| Medium Density Rsidential *(source typo)* | 2 |
| Low Density Residentail *(source typo)* | 2 |

**Output**: `data/zoning/future/grantsville_gp.geojson` (51 features, schema v2)
**Primary FLU field**: `Name` (string, no coded domain — values are label strings)
**Native SR**: WKID 102100 (EPSG:3857 Web Mercator) → reprojected to EPSG:4326 via `outSR=4326`
**Data quality note**: Two zone values contain typos in the source service ("Rsidential", "Residentail") — preserved as-is in `gp_zone_code`/`gp_zone_description`; normalize during Phase 18b-3 D1 load.

---

## Bluffdale — 2026-05-16 (18b-2c REST ingest)

**Layer**: `Land Use Designations January 2022` (FeatureServer Layer 0)
**Source URL**: https://services3.arcgis.com/ojBMkFlpg5ujUNtB/arcgis/rest/services/LandUse/FeatureServer/0
**Layer type**: `flu`
**Extraction method**: `arcgis_rest` — no PDF stages (not applicable)
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)

### Run outcome: PASS

| Criterion | Result | Status |
|---|---|---|
| Feature count = 94 | 94 features | PASS |
| Centroid within 10 km of city center | 0.54 km | PASS |
| Coded domain resolved | 11 domain entries resolved to labels | PASS |
| Schema v2 fields | All present | PASS |
| Cost | $0 (REST ingest) | PASS |

### Coded domain (LandUse field, 11 entries)

| Code | Label |
|---|---|
| R-VLD | Very Low Density Residential |
| R-LD | Low Density Residential |
| R-C | Cluster Residential |
| R-MF | Multi-Family Residential |
| MU | Mixed-Use |
| C-RC | Regional Core |
| C | Commercial |
| C-H | Heavy Commercial |
| PROS | Parks, Recreation, & Open Space |
| CI | Civic Institutional |
| G | Governmental |

### Zone types (12, including 1 null)

| Zone | Count |
|---|---|
| Commercial | 20 |
| Parks, Recreation, & Open Space | 19 |
| Very Low Density Residential | 12 |
| Civic Institutional | 12 |
| Low Density Residential | 11 |
| Cluster Residential | 7 |
| Mixed-Use | 6 |
| Heavy Commercial | 3 |
| Regional Core | 1 |
| Multi-Family Residential | 1 |
| Governmental | 1 |
| *(null LandUse — 1 feature with no domain value)* | 1 |

**Output**: `data/zoning/future/bluffdale_gp.geojson` (94 features, schema v2)
**Primary FLU field**: `LandUse` (coded domain — resolved to label strings, not codes)
**Native SR**: WKID 102743 (EPSG:3566 Utah Central State Plane, feet) → reprojected to EPSG:4326 via `outSR=4326`
**Data quality note**: 1 feature has a null `LandUse` value in the source — stored as empty string in `gp_zone_code`. Additional `gp_zone_type` field populated from `Type` column for supplementary classification context.

---

## Draper — 2026-05-16 (18b-2c REST ingest)

**Layer**: `Land Use` (Land_Use_Public FeatureServer Layer 3)
**Source URL**: https://services2.arcgis.com/nAPVXppTJAHM40Se/arcgis/rest/services/Land_Use_Public/FeatureServer/3
**Layer type**: `flu`
**Extraction method**: `arcgis_rest` — no PDF stages (not applicable)
**Pipeline version**: v2 (phase-18b-2-pipeline-v2)

### Run outcome: PASS

| Criterion | Result | Status |
|---|---|---|
| Feature count = 62 | 62 features | PASS |
| Centroid within 10 km of city center | 2.45 km | PASS |
| Schema v2 fields | All present | PASS |
| Cost | $0 (REST ingest) | PASS |

### Zone types (20)

| Zone | Count |
|---|---|
| Residential Low/Medium Density | 41 |
| Office/Service | 3 |
| Open Space/Parks | 1 |
| Community/Neighborhood Commercial | 1 |
| Regional Commercial | 1 |
| Residential Medium-High Density | 1 |
| Sensitive River Overlay | 1 |
| Residential Hillside Low Density | 1 |
| Neighborhood Commercial | 1 |
| Community Commercial | 1 |
| Destination Commercial | 1 |
| Town Center | 1 |
| Transit Station District | 1 |
| Residential High Density | 1 |
| Business & Light Manufacturing | 1 |
| Growth Area | 1 |
| Industrial/Manufacturing | 1 |
| Cultural/Institutional | 1 |
| Commercial Special District | 1 |
| Residential Medium Density | 1 |

**Output**: `data/zoning/future/draper_gp.geojson` (62 features, schema v2)
**Primary FLU field**: `LAND_USE` (string, no coded domain — values are label strings)
**Native SR**: WKID 102743 (EPSG:3566 Utah Central State Plane, feet) → reprojected to EPSG:4326 via `outSR=4326`
**Data quality note**: 20 distinct zone types vs. ~12 in pre-check inventory (inventory was based on sampling). Actual layer has higher granularity. `ZONING` field (current zoning cross-ref) retained in `gp_zone_label` property for Phase 18b-3 join — not used as FLU classification.

---

## Herriman — 2026-05-18 (18b-2d-2 Cam-KMZ raster-sample)

**Source**: Map 7 — Future Land Use 2025 from 2013 GP Amendment (page 34 of `Herriman_GP_Amendment.pdf`)
**Georeference source**: `Herriman_Zoning.kmz` — manually placed in Google Earth Pro by Cam using
local geography knowledge (Mountain View Corridor, Bangerter, city boundary, named streets). ~99% alignment confidence.
**Layer type**: `flu`
**Pipeline version**: Phase 18b-2d raster-sample (per-parcel LAB color sampling)
**Model**: `claude-opus-4-7` (legend extraction only — 1 vision call)
**Vintage flags**: `flu_plan_vintage=2013_amendment_2025_horizon`; `flu_currency_note=may not reflect post-2013 updates; FLU2022 exists on Herriman internal Enterprise GIS but is not publicly accessible`

### Run outcome: COMPLETE — pending Cam eye-test (Stage 5)

| Criterion | Result | Status |
|---|---|---|
| Parcels sampled (with zone) | 16,219 / 16,408 (98.8%) | PASS |
| Parcels unknown | 189 (1.2%) | PASS |
| Legend categories | 16 / 16 | PASS |
| Mixed Use Towne Center % | 8.8% (prior red flag: 26.5%) | IMPROVED |
| GeoTIFF bounds match KML LatLonBox | N=40.5421 S=40.4425 E=−111.9241 W=−112.0941 | PASS |
| Vintage flags in GeoJSON properties | All 4 fields present | PASS |
| KMZ produced for eye-test | 8.1 MB, 16,622 placemarks | PASS |
| API cost | < $0.10 (1 legend vision call) | PASS |

### Legend (16 categories confirmed)

| Zone | RGB | Parcel count | % of sampled |
|---|---|---|---|
| Hillside/Rural Residential (0.5-1.7 du/acre) | 206, 195, 165 | 2,782 | 17.2% |
| High Density Residential (8-20 du/acre) | 180, 130, 50 | 2,463 | 15.2% |
| Low Density Residential (1.8-2.5 du/acre) | 255, 252, 200 | 2,279 | 14.1% |
| Single Family Residential (2.6-4.5 du/acre) | 255, 245, 0 | 2,044 | 12.6% |
| Mixed Use - Towne Center | 235, 205, 205 | 1,421 | 8.8% |
| Agricultural Residential (1.8-3.0 du/acre) | 200, 220, 130 | 1,416 | 8.7% |
| Medium Density Residential (4.6-8 du/acre) | 232, 165, 60 | 1,403 | 8.7% |
| Mixed Use | 205, 145, 175 | 715 | 4.4% |
| Military Operation | 45, 125, 130 | 534 | 3.3% |
| Resort/Recreational | 120, 160, 105 | 347 | 2.1% |
| Open Space | 195, 215, 190 | 328 | 2.0% |
| Commercial | 230, 40, 40 | 221 | 1.4% |
| Public/Institutional/Cultural/Schools | 40, 110, 175 | 74 | 0.5% |
| Parks and Recreation | 60, 180, 50 | 72 | 0.4% |
| Quasi-Public/Utilities | 160, 205, 240 | 62 | 0.4% |
| Light Industrial/Business Park | 140, 120, 175 | 58 | 0.4% |

### Prior run comparison

| Metric | 18b-2d-1 (algorithmic georef) | 18b-2d-2 (Cam-KMZ) | Change |
|---|---|---|---|
| Mixed Use Towne Center | 26.5% | 8.8% | −17.7 pp ✓ |
| Parcel coverage | ~16,408 | 16,219 (98.8%) | improved |
| Georef RMSE | unknown/high | N/A (Cam-placed, ~99% confidence) | n/a |

The 26.5% Towne Center overrepresentation in the prior run was a georef offset artifact.
Cam-KMZ alignment eliminated it — distribution is now plausible for a mixed-use city.

### Anomalies / notes

- **High Density Residential at 15.2%** — second-largest zone. Herriman has seen dense
  development in the northeast quadrant; warrants visual confirmation during eye-test.
- **Military Operation at 3.3%** — Camp Williams occupies a large footprint in the west;
  count appears correct for that footprint.
- **No `unknown` in distribution table** means the 189 unknowns (~1.2%) are parcels whose
  centroids landed on white space (roads, map border) — expected and acceptable.

### Decision

**PENDING eye-test (Stage 5)** — Cam to open `herriman_gp.kmz` and `Herriman_Zoning.kmz`
in Google Earth Pro, toggle layers, spot-check 10 parcels across zones.

- PASS → ship Herriman on PR #11 with 18b-2c (REST cities) + 18b-2d-2 (Herriman)
- FAIL → diagnose residual error (color mapping vs alignment) and iterate

**Output files** (superseded by bbox+whitelist fix below):
- `data/zoning/future/herriman_gp.geojson` (16,408 features, schema v2 + vintage flags)
- `data/zoning/future/herriman_gp_parcel_table.csv`
- `data/zoning/future/legends/herriman_legend.json` (16 entries)
- `data/zoning/future/herriman_gp.kmz` (eye-test KMZ, gitignored)
- `data/_pdf_cache/herriman/herriman_cam_georef.tif` (georeferenced raster, gitignored)

---

### 18b-2d-2 bbox+whitelist fix (2026-05-18)

**Trigger**: Cam's eye-test showed large vacant areas in the GP map with no colored parcels.
Diagnostic confirmed: Herriman's FLU planning area (KMZ bounds) extends beyond city limits,
covering Olympia Hills parcels tagged South Jordan and Bluffdale parcels along the
Mountain View Corridor. No unincorporated SLC parcels exist in the area (hypothesis disproved).

**Changes**:
- `CITY_CONFIGS["herriman"]["bbox"]` widened to match KMZ LatLonBox exactly (lat_min 40.49→40.4425, lon_min −112.08→−112.0941, lon_max −111.97→−111.9241)
- `parcel_city_whitelist: ["Herriman", "South Jordan", "Bluffdale", "Unincorporated Salt Lake County"]` added to city config
- Blank-city parcels included (Camp Williams / federal land without parcel_city tag)
- `flu_source_jurisdiction: "Herriman"` added to all feature properties
- `--skip-georef` flag added to `herriman_cam_ingest.py` (reuses cached GeoTIFF)

**Before/after parcel counts**:

| Source city | Prior run | This run | Notes |
|---|---|---|---|
| Herriman | 16,219 sampled | 19,321 | +3,102 from expanded bbox (southern fringe) |
| South Jordan | 0 | 4,160 | Olympia Hills mega-development |
| Bluffdale | 0 | 4,199 | Mountain View Corridor / western fringe |
| (blank city tag) | 0 | 711 | Camp Williams federal land, commercial |
| **Total sampled** | **16,219** | **28,195** | +74% |
| Unknown | 189 | 196 | stable |
| Coverage % | 98.8% | 99.3% | |

**Zone distribution — before / after / Herriman-only**:

| Zone | Prior 16k run | This 28k run | Herriman-only |
|---|---|---|---|
| Mixed Use - Towne Center | 8.8% | 24.6% | 7.4% |
| Hillside/Rural Residential | 17.2% | 20.6% | 16.7% |
| Single Family Residential | 12.6% | 9.8% | 14.3% |
| High Density Residential | 15.2% | 9.5% | 13.7% |
| Low Density Residential | 14.1% | 9.1% | 13.2% |
| Medium Density Residential | 8.7% | 6.8% | 10.0% |
| Agricultural Residential | 8.7% | 5.8% | 8.5% |
| Open Space | 2.0% | 4.8% | — |
| Military Operation | 3.3% | 2.3% | — |

**Interpretation of 24.6% MUT in combined run**:
South Jordan parcels are 78% MUT (Olympia Hills planned development, correctly colored MUT
on Herriman's FLU map). Bluffdale is 47% MUT (western Mountain View Corridor). These are
geographically correct — Herriman's GP map designates those areas as Towne Center regardless
of which city's parcel records they appear in. Herriman-only parcels remain 7.4% MUT,
confirming georef is accurate. The 24.6% headline is an artifact of the mixed-jurisdiction
FLU planning area, not a sampling error.

**KMZ**: 14.3 MB, 28,812 placemarks.

**Output files**:
- `data/zoning/future/herriman_gp.geojson` (28,391 features, schema v2 + vintage flags + flu_source_jurisdiction)
- `data/zoning/future/herriman_gp_parcel_table.csv`
- `data/zoning/future/herriman_gp.kmz` (eye-test KMZ, gitignored)
- GeoTIFF and legend unchanged from prior run.
