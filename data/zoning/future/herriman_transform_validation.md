# Herriman GP FLU — Transform Validation Report

**Date**: 2026-05-16
**Model**: claude-opus-4-7
**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced
**Map rotation detected**: -143.7°

## Affine Transform Matrix

```
[lon]   [0.00000000  -0.00029212  -111.58451691]   [px_x]
[lat] = [0.00000000  -0.00007076  40.61726010] * [px_y]
                                                        [ 1  ]
```

## Control Points

| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 6000 West (Pioneer St) | 12600 South | 1729 | 1342 | -111.976548 | 40.522301 | -111.976548 | 40.522301 | 0.0 |
| 2 | 6000 West (Pioneer St) | 13400 South | 1729 | 1546 | -112.036141 | 40.507866 | -112.036141 | 40.507866 | 0.0 |
| 3 | Bangerter Hwy | 13400 South | 2335 | 1546 | -112.036141 | 40.507866 | -112.036141 | 40.507866 | 0.0 |
| 4 | Bangerter Hwy | 12600 South | 2335 | 1342 | -111.976548 | 40.522301 | -111.976548 | 40.522301 | 0.0 |

**Overall RMSE: 0.0 ft** ✅ PASS

## Visual Spot-Checks

Manual judgment: does each projected polygon land in the correct zone per the source PDF?

| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |
|---|---|---|---|---|
| 1 | Single Family Residential — 2.6 - 4.5 du/acre | -112.051916, 40.504045 | Should be Single Family Residential zone area | [manual review needed] |
| 2 | — | — | — | insufficient polygons extracted |
| 3 | — | — | — | insufficient polygons extracted |
| 4 | — | — | — | insufficient polygons extracted |
| 5 | — | — | — | insufficient polygons extracted |

## API Cost Summary

| Call | Input tokens | Output tokens | Cost USD |
|---|---|---|---|
| control_points_p0 | 5227 | 357 | $0.1052 |
| legend | 4878 | 514 | $0.1117 |
| zone_Hillside/Rural Residential | 5082 | 96 | $0.0834 |
| zone_Agricultural Residential | 5079 | 196 | $0.0909 |
| zone_Low Density Residential | 5078 | 131 | $0.0860 |
| zone_Single Family Residential | 5078 | 214 | $0.0922 |
| zone_Medium Density Residential | 5077 | 180 | $0.0896 |
| zone_High Density Residential | 5073 | 96 | $0.0833 |
| zone_Mixed Use | 5070 | 66 | $0.0810 |
| zone_Mixed Use- Towne Center | 5082 | 72 | $0.0816 |
| **TOTAL** | | | **$0.9050** |
