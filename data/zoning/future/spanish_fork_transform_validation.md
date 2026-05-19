# Spanish Fork GP FLU — Transform Validation Report

**Date**: 2026-05-16
**Model**: claude-opus-4-7
**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced
**Map rotation detected**: 0.0°

## Affine Transform Matrix

```
[lon]   [0.00005318  0.00000000  -111.77430337]   [px_x]
[lat] = [0.00000065  -0.00001596  40.13596021] * [px_y]
                                                        [ 1  ]
```

## Control Points

| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Main St x Center St | manual | 2247 | 1719 | -111.654800 | 40.109800 | -111.654800 | 40.109973 | 63.0 |
| 2 | Main St x 400 N | manual | 2247 | 1409 | -111.654800 | 40.115000 | -111.654800 | 40.114921 | 28.8 |
| 3 | Main St x 300 S | manual | 2247 | 1980 | -111.654800 | 40.105900 | -111.654800 | 40.105806 | 34.2 |
| 4 | Center St x Mill Rd | manual | 1980 | 1719 | -111.669000 | 40.109800 | -111.669000 | 40.109800 | 0.0 |

**Overall RMSE: 38.6 ft** ✅ PASS

## Visual Spot-Checks

Manual judgment: does each projected polygon land in the correct zone per the source PDF?

| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |
|---|---|---|---|---|
| 1 | Agricultural — Agricultural | -111.678041, 40.128830 | Should be Agricultural zone area | [manual review needed] |
| 2 | Agricultural — Agricultural | -111.659959, 40.092335 | Should be Agricultural zone area | [manual review needed] |
| 3 | Agricultural — Agricultural | -111.611562, 40.089092 | Should be Agricultural zone area | [manual review needed] |
| 4 | Business Park — Business Park | -111.612094, 40.103612 | Should be Business Park zone area | [manual review needed] |
| 5 | Business Park — Business Park | -111.617412, 40.122703 | Should be Business Park zone area | [manual review needed] |

## API Cost Summary

| Call | Input tokens | Output tokens | Cost USD |
|---|---|---|---|
| legend | 4795 | 298 | $0.0943 |
| zone_Agricultural | 4985 | 144 | $0.0856 |
| zone_Business Park | 4983 | 266 | $0.0947 |
| zone_Commercial | 4983 | 534 | $0.1148 |
| zone_Floodplain (FLOODWAY) | 5003 | 112 | $0.0834 |
| zone_Industrial | 4983 | 66 | $0.0797 |
| zone_Estate Density Residential | 4991 | 176 | $0.0881 |
| zone_Low Density Residential | 4989 | 148 | $0.0859 |
| zone_Medium Density Residential | 4991 | 94 | $0.0819 |
| **TOTAL** | | | **$0.8084** |
