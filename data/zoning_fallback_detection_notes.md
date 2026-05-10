# Phase 18b Fallback Jurisdiction Detection Notes

Detection source: repository state on branch `phase-18b-zoning-extraction`.

`data/jurisdictions.yaml` lists 13 active jurisdictions. `scripts/score_parcel_zoning.py` documents Phase 13b-5 behavior: the checked-in UGRC LIR parcel CSVs lack `zone_class` / `zoning` / `zone` / `zone_code` style source columns, so the script falls back to `prop_class` when enabled. The checked-in `data/raw/parcel_zoning_scores.csv` contains 947,863 scored rows and every populated `source_zone_string` begins with `prop_class:`. No rows have a non-prop_class zoning source.

The active jurisdictions are therefore treated as B1 fallback jurisdictions for Phase 18b. A parcel-city cross-check found prop_class-derived zoning rows for most active jurisdictions and no real zoning source for any active jurisdiction. South Jordan and Herriman have no parcel rows matching exact `parcel_city` values in the current CSVs, but because all Phase 13b-5 zoning-score rows are prop_class fallback and repo state contains no per-city real zoning source, they remain in the fallback scope. Per the task instruction, if fallback detection is not more granular from repo state, default processing covers all 13 active jurisdictions.

| Jurisdiction slug | Name | Repo determination |
|---|---|---|
| grantsville | Grantsville City | B1 fallback |
| erda | Erda City | B1 fallback |
| tooele_city | Tooele City | B1 fallback |
| lehi | Lehi City | B1 fallback |
| saratoga_springs | Saratoga Springs City | B1 fallback |
| eagle_mountain | Eagle Mountain City | B1 fallback |
| south_jordan | South Jordan City | B1 fallback by repo-wide fallback default |
| herriman | Herriman City | B1 fallback by repo-wide fallback default |
| bluffdale | Bluffdale City | B1 fallback |
| draper | Draper City | B1 fallback |
| american_fork | American Fork City | B1 fallback |
| vineyard | Vineyard City | B1 fallback |
| spanish_fork | Spanish Fork City | B1 fallback |
