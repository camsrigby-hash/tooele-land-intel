# Herriman GP FLU — Transform Validation Report

**Date**: 2026-05-15
**Model**: claude-opus-4-7
**Extraction method**: anthropic_vision_claude_opus_4_7_georeferenced
**Map rotation detected**: -32.7°

## Affine Transform Matrix

```
[lon]   [0.00001108  -0.00000767  -112.04022261]   [px_x]
[lat] = [0.00000669  -0.00002256  40.53243451] * [px_y]
                                                        [ 1  ]
```

## Control Points

| # | Street A | Street B | px_x | px_y | gt_lon | gt_lat | pred_lon | pred_lat | residual_ft |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 11800S_x_AnthemPark | manual | 2470 | 640 | -112.017477 | 40.534361 | -112.017751 | 40.534511 | 93.6 |
| 2 | MainSt_x_PioneerSt | manual | 1960 | 1370 | -112.033043 | 40.516835 | -112.029002 | 40.514629 | 1379.8 |
| 3 | HerrimanPkwy_x_RosecrestRd | manual | 2150 | 1620 | -112.024476 | 40.507890 | -112.028813 | 40.510258 | 1481.0 |
| 4 | MtnViewCorridor_x_RosecrestRd | manual | 3650 | 2920 | -112.022725 | 40.491267 | -112.022155 | 40.490956 | 194.8 |

**Overall RMSE: 1017.9 ft** ❌ FAIL

## Visual Spot-Checks

Manual judgment: does each projected polygon land in the correct zone per the source PDF?

| # | Zone | Representative vertex (lon, lat) | Expected location | Judgment |
|---|---|---|---|---|
| 1 | Educational Village/Campus — Educational Village/Campus | -112.022143, 40.479841 | Should be Educational Village/Campus zone area | [manual review needed] |
| 2 | Forest Residential/Recreational Resort — Forest Residential/Recreational Resort | -112.028606, 40.478453 | Should be Forest Residential/Recreational Resort zone area | [manual review needed] |
| 3 | Open Space — Open Space | -112.019097, 40.477911 | Should be Open Space zone area | [manual review needed] |
| 4 | Mixed Use Neighborhood One — Mixed Use Neighborhood One | -112.031694, 40.506188 | Should be Mixed Use Neighborhood One zone area | [manual review needed] |
| 5 | Mixed Use Neighborhood One — Mixed Use Neighborhood One | -112.034659, 40.522875 | Should be Mixed Use Neighborhood One zone area | [manual review needed] |

## API Cost Summary

| Call | Input tokens | Output tokens | Cost USD |
|---|---|---|---|
| legend | 4881 | 496 | $0.1104 |
| zone_Educational Village/Campus | 5079 | 96 | $0.0834 |
| zone_Forest Residential/Recreational Resort | 5089 | 188 | $0.0904 |
| zone_Open Space | 5069 | 260 | $0.0955 |
| zone_Mixed Use Neighborhood One | 5087 | 104 | $0.0841 |
| zone_Employment Campus/Business Park | 5083 | 174 | $0.0893 |
| zone_Parks and Plazas | 5077 | 96 | $0.0833 |
| zone_Civic and Community | 5073 | 126 | $0.0855 |
| zone_Utilities and Support Services | 5079 | 142 | $0.0868 |
| zone_Office Mixed Use | 5077 | 96 | $0.0833 |
| zone_General Retail | 5069 | 184 | $0.0898 |
| zone_Neighborhood Commercial/Node | 5087 | 142 | $0.0870 |
| zone_Mixed Neighborhood Two | 5085 | 108 | $0.0844 |
| zone_Neighborhood Residential Two | 5083 | 107 | $0.0843 |
| zone_Neighborhood Residential One | 5081 | 185 | $0.0901 |
| zone_Hillside and Agricultural Residential | 5087 | 162 | $0.0885 |
| zone_Mountain and Canyon Residential | 5085 | 157 | $0.0881 |
| **TOTAL** | | | **$1.5043** |
