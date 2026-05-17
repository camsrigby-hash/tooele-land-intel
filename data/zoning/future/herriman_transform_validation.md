# Herriman GP FLU — Transform Validation Report

**Date**: 2026-05-16
**Model**: claude-opus-4-7
**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced
**Map rotation detected**: 22.5°

## Affine Transform Matrix

```
[lon]   [0.00008196  0.00001703  -112.18262444]   [px_x]
[lat] = [0.00001121  -0.00003131  40.52365070] * [px_y]
                                                        [ 1  ]
```

## Control Points

| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Main_St_x_Pioneer_St | manual | 1640 | 890 | -112.033050 | 40.514170 | -112.033050 | 40.514170 | 0.0 |
| 2 | Fort_Herriman_Pkwy_x_13400_South | manual | 1860 | 1170 | -112.010250 | 40.507870 | -112.010250 | 40.507870 | 0.0 |
| 3 | Herriman_Pkwy_x_Rosecrest_Rd | manual | 1640 | 1395 | -112.024450 | 40.498360 | -112.024450 | 40.498360 | 0.0 |

**Overall RMSE: 0.0 ft** ✅ PASS

## Visual Spot-Checks

Manual judgment: does each projected polygon land in the correct zone per the source PDF?

| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |
|---|---|---|---|---|
| 1 | Agricultural Residential — 1.8 - 3.0 du/acre | -111.984619, 40.499603 | Should be Agricultural Residential zone area | [manual review needed] |
| 2 | Agricultural Residential — 1.8 - 3.0 du/acre | -111.994540, 40.485464 | Should be Agricultural Residential zone area | [manual review needed] |
| 3 | Agricultural Residential — 1.8 - 3.0 du/acre | -111.960904, 40.488383 | Should be Agricultural Residential zone area | [manual review needed] |
| 4 | Single Family Residential — 2.6 - 4.5 du/acre | -112.033647, 40.502316 | Should be Single Family Residential zone area | [manual review needed] |
| 5 | Single Family Residential — 2.6 - 4.5 du/acre | -112.020192, 40.503483 | Should be Single Family Residential zone area | [manual review needed] |

## API Cost Summary

| Call | Input tokens | Output tokens | Cost USD |
|---|---|---|---|
| legend | 4878 | 514 | $0.1117 |
| zone_Hillside/Rural Residential | 5082 | 78 | $0.0821 |
| zone_Agricultural Residential | 5079 | 238 | $0.0940 |
| zone_Low Density Residential | 5078 | 173 | $0.0891 |
| zone_Single Family Residential | 5078 | 276 | $0.0969 |
| zone_Medium Density Residential | 5077 | 156 | $0.0879 |
| zone_High Density Residential | 5073 | 142 | $0.0867 |
| zone_Mixed Use | 5070 | 66 | $0.0810 |
| zone_Mixed Use- Towne Center | 5082 | 80 | $0.0822 |
| **TOTAL** | | | **$0.8117** |
