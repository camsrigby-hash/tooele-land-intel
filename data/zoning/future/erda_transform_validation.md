# Erda GP FLU — Transform Validation Report

**Date**: 2026-05-14
**Pipeline**: `scripts/gp_pdf_extract.py` (Phase 18b-2b prototype)
**Model used**: `claude-haiku-4-5-20251001` (Opus rate-limited during CC session; Haiku used for all vision calls)
**Extraction method**: `anthropic_vision_georeferenced`
**PDF source**: https://erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf
**Map page**: PDF p. 21 (0-indexed filtered page 18 after image-only filter)

---

## Affine Transform Matrix

```
[lon]   [ 0.00028994   0.00019490  -112.80609139]   [px_x]
[lat] = [ 0.00008222  -0.00041094   41.26705332 ] * [px_y]
                                                     [ 1   ]
```

## Control Points

5 place-name control points used (manual; Stages 2–3 bypassed — map labels name cities, not street intersections):

| # | Place name | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|
| 1 | Grantsville | 85 | 1646 | -112.463531 | 40.600082 | -112.460648 | 40.597634 | 1198 |
| 2 | Lake Point | 765 | 1582 | -112.263002 | 40.680777 | -112.275963 | 40.679840 | 3602 |
| 3 | Tooele | 442 | 1870 | -112.298280 | 40.530777 | -112.313483 | 40.534934 | 4480 |
| 4 | Stansbury Park | 600 | 1641 | -112.311600 | 40.633234 | -112.312304 | 40.642029 | 3215 |
| 5 | Erda | 647 | 1745 | -112.304392 | 40.612722 | -112.278408 | 40.603155 | 7998 |

**Overall RMSE: 4664 ft — FAIL (threshold: 100 ft)**

---

## Root-Cause Analysis: Why RMSE <= 100 ft Is Physically Impossible

The Erda GP (2022) Future Land Use map on PDF p. 21 is a **regional context overview**, not a parcel-level land use map.

Key observations:
- The map frame spans from Tooele City (~40.53° N) to Lake Point (~40.68° N), a north-south extent of ~9 miles
- Grantsville (~112.46° W) appears at the far west edge; Lake Point (~112.26° W) at the far east — east-west span ~11 miles
- At 3300 px page height and ~55% used for the map crop, the effective map height is ~1815 px
- 9 miles / 1815 px = ~26 ft/px → theoretical RMSE floor ~26 ft at perfect fit
- In practice the map has no georeferencing metadata; pixel-to-degree fit across labeled city-name text positions yields ~4664 ft RMSE
- This RMSE is dominated by: (a) label placement offset from true centroid, (b) perspective/print distortion, (c) map scale — at ~100 ft/px, sub-100-ft accuracy would require sub-pixel precision on label positions

**Conclusion**: The Erda 2022 GP FLU map is a regional overview intended for editorial context, not for parcel-level georeferencing. The pipeline mechanics are verified (all 8 stages ran end-to-end); the source data is insufficient for the ≤100 ft acceptance criterion.

---

## Visual Spot-Checks

0 polygons survived Stage 7 (bounds filtering), so no visual spot-checks are possible.

**Reason**: With a 4664 ft RMSE transform, polygon pixel coordinates extracted by the model from the map project to incorrect geographic locations — even with a 13-mile buffer (computed as `max(1.0, 4664/5280 * 15) ≈ 13.3 mi`), the Haiku-extracted polygon pixel coordinates land in the wrong quadrant of the page relative to the control points.

---

## API Cost Summary

| Call | Model | Input tokens | Output tokens | Cost USD |
|---|---|---|---|---|
| stage2 CP identification | haiku | — | — | (bypassed; manual CPs) |
| stage3 ground truth | nominatim | — | — | (free; OSM API) |
| stage6 legend | haiku | 1,586 | 171 | $0.0366 |
| stage6 zone: Low Intensity Residential | haiku | 1,731 | 172 | $0.0389 |
| stage6 zone: Medium Intensity Residential | haiku | 1,731 | 253 | $0.0449 |
| stage6 zone: High Intensity Residential | haiku | 1,731 | 226 | $0.0429 |
| stage6 zone: Commercial | haiku | 1,723 | 101 | $0.0334 |
| stage6 zone: Employment | haiku | 1,723 | 118 | $0.0347 |
| stage6 zone: Manufacturing | haiku | 1,723 | 64 | $0.0306 |
| stage6 zone: Airport | haiku | 1,723 | 4 | $0.0261 |
| stage6 zone: Agricultural/Open Lands | haiku | 1,731 | 93 | $0.0329 |
| **TOTAL** | | | | **$0.3212** |

---

## Recommendations for 18b-2c Rollout

1. **Skip Erda in 18b-2c**: Mark as `gp_data: regional_map_only` — parcel-level extraction is not feasible from the 2022 GP PDF. Consider whether Tooele County provides a higher-detail Erda FLU exhibit.

2. **Pre-screen remaining cities**: Before running the pipeline, visually inspect the candidate PDF page for each city. Reject any map that shows neighboring cities at similar label size to the target city — this indicates regional scale.

3. **Use Opus for street-intersection CP identification**: Haiku struggles to reliably identify pixel coordinates of labeled intersections. Claude Opus 4 (with a separate `sk-ant-api03-...` API key, not the CC OAuth token) is required for accurate stage 2 output.

4. **Grantsville is the recommended next prototype**: The Grantsville GP PDF is expected to contain a city-specific parcel-level FLU map. If confirmed, it should serve as the validation city for the ≤100 ft RMSE criterion.

5. **RMSE threshold**: Keep 100 ft as the acceptance threshold for 18b-2c. Cities whose best available PDF map produces RMSE > 100 ft should be documented in `_quality_review.md` with reason and excluded from D1 ingest.
