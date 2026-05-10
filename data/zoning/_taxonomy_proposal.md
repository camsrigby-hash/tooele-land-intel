# Phase 18b Zoning Taxonomy Proposal

This proposal is based on **zone codes that were actually extracted as GeoJSON polygon features** from the official PDF zoning-map sources processed through `claude-opus-4-7` via the Anthropic Messages Batch API. The extraction is intentionally conservative: the source maps did not expose reliable georeferencing grids, so the resulting polygons are simplified and marked low quality where the model reported limited coordinate confidence. The taxonomy should be treated as a CC normalization starting point rather than a final ordinance-grade zoning ontology.

The final Batch run produced **54 polygon features** across **4 cities**, with an estimated Batch API LLM cost of **$0.72** derived from returned token usage.

| Proposed `zone_class_normalized` | Source zone code | Source city | PDF legend description | Notes |
|---|---|---|---|---|
| `Agriculture/Rural` | `A-5` | Lehi City | Agricultural | Extracted polygon feature present in GeoJSON. |
| `Agriculture/Rural` | `A-E` | Spanish Fork City | Exclusive Agriculture | Extracted polygon feature present in GeoJSON. |
| `Commercial-Community` | `CC-1` | American Fork City | Community Commercial 1 | Extracted polygon feature present in GeoJSON. |
| `Commercial-Community` | `CC-2` | American Fork City | Community Commercial 2 | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `GC-2` | American Fork City | General Commercial 2 | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `SC-1` | American Fork City | Shopping Center | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `CD` | Grantsville City | Downtown Commercial | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `CG` | Grantsville City | General Commercial | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `C` | Lehi City | Commercial | Extracted polygon feature present in GeoJSON. |
| `Commercial-General` | `C-D` | Spanish Fork City | Downtown Commercial | Extracted polygon feature present in GeoJSON. |
| `Industrial-Heavy` | `MG-EX` | Grantsville City | Manufacturing General Extraction | Extracted polygon feature present in GeoJSON. |
| `Industrial-Heavy` | `I-3` | Spanish Fork City | Heavy Industrial | Extracted polygon feature present in GeoJSON. |
| `Industrial-Light` | `I-1` | American Fork City | Industrial | Extracted polygon feature present in GeoJSON. |
| `Industrial-Light` | `M-1` | American Fork City | Manufacturing | Extracted polygon feature present in GeoJSON. |
| `Industrial-Light` | `I-1` | Spanish Fork City | Light Industrial | Extracted polygon feature present in GeoJSON. |
| `Industrial-Medium` | `MD` | Grantsville City | Manufacturing Distribution | Extracted polygon feature present in GeoJSON. |
| `Industrial-Medium` | `MG` | Grantsville City | Manufacturing General | Extracted polygon feature present in GeoJSON. |
| `Mixed-Use` | `MU` | Grantsville City | Mixed Use | Extracted polygon feature present in GeoJSON. |
| `Mixed-Use` | `MU` | Lehi City | Mixed Use Commercial/Residential | Extracted polygon feature present in GeoJSON. |
| `Planned/Master-Planned` | `PC` | American Fork City | Planned Commercial | Extracted polygon feature present in GeoJSON. |
| `Planned/Master-Planned` | `PI-1` | American Fork City | Planned Industrial | Extracted polygon feature present in GeoJSON. |
| `Planned/Master-Planned` | `PUD` | Grantsville City | Planned Unit Development | Extracted polygon feature present in GeoJSON. |
| `Planned/Master-Planned` | `PC` | Lehi City | Planned Community | Extracted polygon feature present in GeoJSON. |
| `Public/Open-Space` | `PF` | American Fork City | Public Facilities | Extracted polygon feature present in GeoJSON. |
| `Public/Open-Space` | `S-1` | American Fork City | Special District | Extracted polygon feature present in GeoJSON. |
| `Public/Open-Space` | `P-F` | Spanish Fork City | Public Facilities | Extracted polygon feature present in GeoJSON. |
| `Residential-Agriculture` | `RA-1` | American Fork City | Residential Agricultural 1 | Extracted polygon feature present in GeoJSON. |
| `Residential-Agriculture` | `RA-5` | American Fork City | Residential Agricultural 5 | Extracted polygon feature present in GeoJSON. |
| `Residential-Agriculture` | `RA-1` | Lehi City | Residential / Agriculture | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R1-12000` | American Fork City | Single-Family Residential 12,000 sq ft | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R1-7500` | American Fork City | Single-Family Residential 7,500 sq ft | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R1-9000` | American Fork City | Single-Family Residential 9,000 sq ft | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-12` | Grantsville City | Single-Family Residential 12,000 sf | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-21` | Grantsville City | Single-Family Residential 21,000 sf | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-8` | Grantsville City | Single-Family Residential 8,000 sf | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-8` | Lehi City | Residential | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-12` | Spanish Fork City | Residential District | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-6` | Spanish Fork City | Residential District | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-8` | Spanish Fork City | Residential District | Extracted polygon feature present in GeoJSON. |
| `Residential-Low` | `R-1-9` | Spanish Fork City | Residential District | Extracted polygon feature present in GeoJSON. |
| `Residential-Medium/High` | `R4-7500` | American Fork City | Multi-Family Residential 4 | Extracted polygon feature present in GeoJSON. |
| `Residential-Rural` | `RR-1` | Grantsville City | Rural Residential 1 acre | Extracted polygon feature present in GeoJSON. |
| `Residential-Rural` | `R-R` | Spanish Fork City | Rural Residential | Extracted polygon feature present in GeoJSON. |
| `Special/Employment` | `RC` | Lehi City | Resort Community | Extracted polygon feature present in GeoJSON. |
| `Special/Employment` | `T-M` | Lehi City | Technical Manufacturing | Extracted polygon feature present in GeoJSON. |

## Grouping rationale

The grouping preserves the primary land-use families that recur across the four extracted jurisdictions: **residential**, **commercial**, **industrial**, **mixed-use**, **agriculture/rural**, **public/open-space**, and **planned or special districts**. Residential codes are split where the source legend conveys rural, agricultural, low-density, or medium/high-density intensity. Commercial and industrial codes are split by neighborhood/community/general and light/medium/heavy intensity where the legend text supports that distinction. Local planned, resort, business-park, and technical-manufacturing codes are preserved in broader planned or special/employment categories so CC can decide whether to preserve them as separate normalized classes.

## References

[1]: https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php "Grantsville Zoning Map"
[2]: https://www.lehi-ut.gov/business-development/maps/ "Lehi City Maps"
[3]: https://www.americanfork.gov/276/Planning-Department "American Fork Planning Department"
[4]: https://www.spanishfork.gov/departments/community_development/planning/zoning.php "Spanish Fork Zoning"
[5]: https://erda.gov/city-codes-and-maps/ "Erda City Code and Maps"
[6]: https://www.sjc.utah.gov/FAQ.aspx?QID=193 "South Jordan Planning FAQ"
