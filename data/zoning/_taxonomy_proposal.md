# Phase 18b Zoning Taxonomy Proposal
This proposal is intentionally limited to **legend codes that could be read from downloaded zoning PDFs without running vision extraction**. The local task environment did not expose `ANTHROPIC_API_KEY`, so no Opus Batch API vision calls were submitted and no polygon geometry was inferred from PDF page coordinates. The table is therefore a draft normalization aid for CC and should be reviewed after polygon extraction is rerun in an environment with the required Anthropic secret.
| Source zone code | Source city | PDF legend description | Proposed `zone_class_normalized` | Notes |
|---|---|---|---|---|
| `A-1` | Lehi City | Agricultural | `Agriculture/Rural` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `A-5` | Lehi City | Agricultural | `Agriculture/Rural` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `C` | Lehi City | Commercial | `Commercial-General` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `C-2` | Spanish Fork City | General Commercial | `Commercial-General` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `UV-C` | Spanish Fork City | Urban Village Commercial | `Commercial-General` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `C-I` | Lehi City | Commercial / Industrial | `Commercial-Industrial` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `C-1` | Spanish Fork City | Neighborhood Commercial | `Commercial-Neighborhood` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `NC` | Lehi City | Neighborhood Commercial | `Commercial-Neighborhood` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `C-O` | Spanish Fork City | Commercial Office | `Commercial-Office` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `I` | Lehi City | Industrial | `Industrial-General` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `I-3` | Spanish Fork City | Heavy Industrial | `Industrial-Heavy` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `H/I` | Lehi City | Historical / Industrial | `Industrial-Historic` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `I-1` | Spanish Fork City | Light Industrial | `Industrial-Light` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `LI` | Lehi City | Light Industrial | `Industrial-Light` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `I-2` | Spanish Fork City | Medium Industrial | `Industrial-Medium` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `MU` | Lehi City | Mixed Use Commercial/Residential | `Mixed-Use` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `RA-1` | Lehi City | Residential / Agriculture | `Residential-Agriculture` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-3` | Lehi City | High Density Residential | `Residential-High` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-5` | Spanish Fork City | Residential District | `Residential-High` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-10` | Lehi City | Residential | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-12` | Lehi City | Residential | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-12` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-15` | Lehi City | Residential | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-15` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-20` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-22` | Lehi City | Residential / Agriculture | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-30` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-40` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-6` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-60` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-8` | Lehi City | Residential | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-8` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-80` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-1-9` | Spanish Fork City | Residential District | `Residential-Low` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-2` | Lehi City | Medium Density Residential | `Residential-Medium` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-3` | Spanish Fork City | Residential District | `Residential-Medium` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-4` | Spanish Fork City | Residential District | `Residential-Medium` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-O` | Spanish Fork City | Residential Office | `Residential-Office` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |
| `R-R` | Spanish Fork City | Rural Residential | `Residential-Rural` | Legend text extracted from downloaded zoning PDF; awaiting polygon extraction. |

## Proposed grouping rationale

The initial grouping keeps commercial, residential, mixed-use, industrial, and agriculture/rural categories separate, while preserving intensity where the source legend states or strongly implies intensity. Similar codes such as Spanish Fork `C-2` and Lehi `C` are grouped under `Commercial-General`; Spanish Fork `I-1` and Lehi `LI` are grouped under `Industrial-Light`; and low-density single-family residential codes are grouped under `Residential-Low`. The proposal does **not** resolve local overlay, planned-community, or conditional-use distinctions because Phase 18b is scoped to base zoning only.

## References

[1]: https://www.lehi-ut.gov/business-development/maps/ "Lehi City Business Development Maps"
[2]: https://www.spanishfork.gov/departments/community_development/planning/zoning.php "Spanish Fork Zoning"
[3]: https://www.americanfork.gov/276/Planning-Department "American Fork Planning Department"
[4]: https://www.grantsvilleut.gov/departments/community___economic_development/zoning_map.php "Grantsville Zoning Map"
