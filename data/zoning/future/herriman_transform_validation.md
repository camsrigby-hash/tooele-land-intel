# Herriman GP FLU — Transform Validation Report

**Date**: 2026-05-16
**Model**: claude-opus-4-7
**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced
**Map rotation detected**: -33.3°

## Affine Transform Matrix

```
[lon]   [0.00000930  -0.00000664  -112.03644445]   [px_x]
[lat] = [0.00000801  -0.00002371  40.53328150] * [px_y]
                                                        [ 1  ]
```

## Control Points

| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 11800S_x_AnthemPark | manual | 2490 | 785 | -112.017477 | 40.534361 | -112.018500 | 40.534616 | 298.7 |
| 2 | MainSt_x_PioneerSt | manual | 1940 | 1405 | -112.033043 | 40.516835 | -112.027733 | 40.515509 | 1550.1 |
| 3 | HerrimanPkwy_x_RosecrestRd | manual | 1986 | 1692 | -112.024476 | 40.507890 | -112.029211 | 40.509072 | 1382.5 |
| 4 | MtnViewCorridor_x_RosecrestRd | manual | 3680 | 3020 | -112.022725 | 40.491267 | -112.022276 | 40.491155 | 131.0 |

**Overall RMSE: 1051.2 ft** ❌ FAIL

## Visual Spot-Checks

Manual judgment: does each projected polygon land in the correct zone per the source PDF?

| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |
|---|---|---|---|---|
| 1 | Educational Village/Campus — Educational Village/Campus | -112.015899, 40.498807 | Should be Educational Village/Campus zone area | [manual review needed] |
| 2 | Forest Residential/Recreational Resort — Forest Residential/Recreational Resort | -112.030195, 40.490090 | Should be Forest Residential/Recreational Resort zone area | [manual review needed] |
| 3 | Open Space — Open Space | -112.015180, 40.483235 | Should be Open Space zone area | [manual review needed] |
| 4 | Mixed Use Neighborhood One — Mixed Use Neighborhood One | -112.029665, 40.505839 | Should be Mixed Use Neighborhood One zone area | [manual review needed] |
| 5 | Mixed Use Neighborhood One — Mixed Use Neighborhood One | -112.026955, 40.503676 | Should be Mixed Use Neighborhood One zone area | [manual review needed] |

## Stage 2b Tile Refinement

| # | Intersection | Rough px_x | Rough px_y | Refined px_x | Refined px_y | Shift (px) |
|---|---|---|---|---|---|---|
| 1 | 11800S_x_AnthemPark | 2470 | 640 | 2490 | 785 | 146.4 |
| 2 | MainSt_x_PioneerSt | 1960 | 1370 | 1940 | 1405 | 40.3 |
| 3 | HerrimanPkwy_x_RosecrestRd | 2150 | 1620 | 1986 | 1692 | 179.1 |
| 4 | MtnViewCorridor_x_RosecrestRd | 3650 | 2920 | 3680 | 3020 | 104.4 |

## API Cost Summary

| Call | Input tokens | Output tokens | Cost USD |
|---|---|---|---|
| tile_refine_cp0 | 606 | 21 | $0.0107 |
| tile_refine_cp1 | 607 | 21 | $0.0107 |
| tile_refine_cp2 | 613 | 21 | $0.0108 |
| tile_refine_cp3 | 613 | 19 | $0.0106 |
| legend | 4881 | 496 | $0.1104 |
| zone_Educational Village/Campus | 5079 | 56 | $0.0804 |
| zone_Forest Residential/Recreational Resort | 5089 | 121 | $0.0854 |
| zone_Open Space | 5069 | 176 | $0.0892 |
| zone_Mixed Use Neighborhood One | 5087 | 134 | $0.0864 |
| zone_Employment Campus/Business Park | 5083 | 128 | $0.0858 |
| zone_Parks and Plazas | 5077 | 108 | $0.0843 |
| zone_Civic and Community | 5073 | 122 | $0.0853 |
| zone_Utilities and Support Services | 5079 | 96 | $0.0834 |
| zone_Office Mixed Use | 5077 | 124 | $0.0855 |
| zone_General Retail | 5069 | 186 | $0.0900 |
| zone_Neighborhood Commercial/Node | 5087 | 156 | $0.0880 |
| zone_Mixed Neighborhood Two | 5085 | 129 | $0.0859 |
| zone_Neighborhood Residential Two | 5083 | 122 | $0.0854 |
| zone_Neighborhood Residential One | 5081 | 87 | $0.0827 |
| zone_Hillside and Agricultural Residential | 5087 | 151 | $0.0876 |
| zone_Mountain and Canyon Residential | 5085 | 219 | $0.0927 |
| **TOTAL** | | | **$1.5311** |
