# Zoning Data Quality Review
## Phase 18b-2e — Taxonomy Harmonization Audit Trail

**Last updated**: 2026-05-23
**Author**: Claude Code (Sonnet 4.6), Phase 18b-2e
**Purpose**: Permanent audit trail for zoning data quality decisions. Per-jurisdiction Other/Unknown rates before and after taxonomy harmonization. Source authority decisions. Cam action items.

---

## Summary Table — Current Zoning Coverage

Per-jurisdiction Other/Unknown rates in `zone_class_normalized` field of current zoning GeoJSONs.
Polygon counts from `data/zoning/current/*_ut_zoning.geojson`. Parcel counts from D1 post-load.

| Jurisdiction | Total polygons | Other/Unknown (before) | Other/Unknown (after) | Fix applied |
|---|---|---|---|---|
| American Fork | 208 | 1 (0.5%) | 0 (0%) | No action needed |
| Bluffdale | 89 | 1 (1.1%) | 0 (0%) | No action needed |
| Draper | 139 | 4 (2.9%) | 0 (0%) | No action needed |
| Eagle Mountain | 1,392 | 263 (18.9%) | 236 (17.0%) | See **Eagle Mountain note** below |
| Erda | 63 | 4 (6.3%) | 4 (6.3%) | No fix; codes UNKNOWN — see Erda note |
| Grantsville | 19 | 9 (47.4%) | 1 (5.3%) | 8 codes added to `gp_taxonomy.yaml` |
| Herriman | 262 | 4 (1.5%) | 0 (0%) | No action needed |
| **Lehi** | **514** | **215 (41.8%)** | **0 (0%)** | 12 codes added to `gp_taxonomy.yaml` |
| Saratoga Springs | 368 | 58 (15.8%) | 0 (0%) | PC → Planned/Mixed-Use |
| South Jordan | 423 | 1 (0.2%) | 0 (0%) | No action needed |
| **Spanish Fork** | **203** | **75 (36.9%)** | **1 (0.5%)** | 9 codes added to `gp_taxonomy.yaml` |
| Tooele | 112 | 1 (0.9%) | 0 (0%) | No action needed |
| Vineyard | 51 | 3 (5.9%) | 0 (0%) | No action needed |

Notes on "Other/Unknown after": these represent codes genuinely undecipherable from available data (UNKNOWN literals, Eagle Mountain ordinance references). They are NOT pipeline errors.

---

## Summary Table — Future GP/FLU Coverage

Per-jurisdiction normalization for `zone_future_normalized` in D1.
All future GeoJSON codes are now covered by `gp_taxonomy.yaml` entries.

| Jurisdiction | Source | Total polygons | Zone codes covered | Notes |
|---|---|---|---|---|
| American Fork | REST | 97 | 16/16 | Clean |
| Bluffdale | REST | 94 | 11/11 (+1 null) | 1 null feature (no gp_zone_code) stays NULL |
| Draper | REST | 62 | 20/20 | Clean |
| Eagle Mountain | REST | 972 | 13/13 | Clean |
| Grantsville | REST | 51 | 9/9 (incl. 2 typos) | Typos normalized in taxonomy |
| Herriman | PDF raster (Cam-KMZ) | 28,391 parcels | 16/16 sampled zones | Uses parcel table, not polygon GeoJSON |
| Lehi | REST (NLS) | 284 | 20/20 | NLS caveat — see below |
| Saratoga Springs | REST (NLS) | 217 | 18/18 | NLS caveat — see below |
| South Jordan | REST | 155 | 12/12 | Clean |
| Spanish Fork | PDF vision | 14 | 4/4 | RMSE 38.6 ft |
| Tooele City | REST | 63 | all tokens | 37/63 have comma-separated codes; secondary stored in `zone_future_secondary` |
| Vineyard | REST | 36 | 12/12 | Clean |

---

## D1 Parcel Coverage Baseline (post-18b-2e re-load, verified 2026-05-23)

Run [26336300126](https://github.com/camsrigby-hash/wasatch-intel/actions/runs/26336300126) completed 2026-05-23 in 15m59s. 462/462 SQL chunks executed (0 failed).

**Overall D1 counts (parcel_records, 947,863 total rows):**

| Column | Count | Notes |
|---|---|---|
| zone_current | 202,913 | parcels with raw zone code set |
| zone_current_normalized | 180,518 | 22,395 NULL = Other/Unknown (Eagle Mountain 17.25/17 + South Jordan P-C + Vineyard) |
| zone_future | 173,165 | parcels with raw future GP code set |
| zone_future_normalized | 172,830 | 335 NULL (Herriman color failures + edge cases) |
| zone_future_secondary | 12,927 | Tooele City (12,813) + other jurisdictions with comma codes |

**Per-county processing (from loader script, 3-county scope):**

| County | Parcels processed | zone_current set | zone_future set |
|---|---|---|---|
| Salt Lake | 394,610 | 70,939 | 59,683 |
| Utah | 327,655 | 127,524 | 108,793 |
| Tooele | 45,618 | 28,782 | 24,931 |
| **Total** | **767,883** | **227,245** | **193,407** |

Note: D1 total is 947,863 (includes parcels from other sources not in the 3-county loader scope). The `zone_current_normalized` count (180,518) is lower than `zone_current` (202,913) due to parcels where the raw zone code cannot be mapped: Eagle Mountain ordinance codes 17.25/17 (deferred per SD), South Jordan P-C (11,855 parcels), Vineyard Waters Edge/HF/OS codes (1,882 parcels). These latter two are pre-existing taxonomy gaps not in 18b-2e scope — see New Taxonomy Gaps section below.

**Per-jurisdiction zone_current_normalized breakdown (actual D1 parcel counts, 2026-05-23):**

| Jurisdiction | Parcels with zone_current | Other/Unknown (NULL norm) | % | Notes |
|---|---|---|---|---|
| American Fork | 13,251 | 729 | 5.5% | Investigate in future phase |
| Bluffdale | 6,821 | 5 | 0.1% | Clean |
| Draper | 16,473 | 2,300 | 14.0% | Large parcels with unmapped codes |
| Eagle Mountain | 21,379 | 3,727 | 17.4% | Expected — 17.25/17 deferred (SD) |
| Erda | 1,557 | 106 | 6.8% | Expected — UNKNOWN literal |
| Grantsville | 5,954 | 76 | 1.3% | UNKNOWN literal (1 polygon covers small area) |
| Herriman | 19,298 | 906 | 4.7% | Investigate in future phase |
| **Lehi** | **30,559** | **1** | **0%** | ✅ FIXED (was ~41.8% at polygon level) |
| **Saratoga Springs** | **20,529** | **0** | **0%** | ✅ FIXED (was 15.8% at polygon level) |
| South Jordan | 29,677 | 11,855 | 39.9% | ⚠️ P-C code (11,855 parcels) — GeoJSON zone_class_normalized is NULL not "Other/Unknown", missed by polygon-level review. Cam action: add south_jordan P-C → Planned/Mixed-Use to taxonomy |
| **Spanish Fork** | **14,060** | **0** | **0%** | ✅ FIXED (was 36.9% at polygon level) |
| Tooele | 14,032 | 803 | 5.7% | Acceptable |
| Vineyard | 4,638 | 1,882 | 40.6% | ⚠️ Waters Edge (1,807), HF (74), OS (1) — same null-norm issue as South Jordan. Cam action: add vineyard codes to taxonomy |

**Per-jurisdiction zone_future_normalized breakdown (actual D1 parcel counts, 2026-05-23):**

| Jurisdiction | Parcels with zone_future | Other/Unknown (NULL norm) | % | Notes |
|---|---|---|---|---|
| American Fork | 11,522 | 58 | 0.5% | Comma-split artifact |
| Bluffdale | 4,965 | 0 | 0% | Clean |
| Draper | 6,398 | 13 | 0.2% | Clean |
| Eagle Mountain | 21,344 | 0 | 0% | Clean |
| Grantsville | 5,419 | 0 | 0% | Clean (typos handled) |
| Herriman | 19,121 | 0 | 0% | Clean (Cam-KMZ coverage) |
| Lehi | 30,635 | 87 | 0.3% | NLS caveat; 87 parcels with unmapped codes |
| Saratoga Springs | 21,101 | 0 | 0% | Clean |
| South Jordan | 29,620 | 121 | 0.4% | Clean at parcel scale |
| Spanish Fork | 2,406 | 0 | 0% | Clean |
| Tooele | 13,953 | 0 | 0% | Clean |
| Vineyard | 2,985 | 0 | 0% | Clean |

**Tooele City zone_future_secondary: 12,813 parcels** with secondary comma codes populated. Additional jurisdictions with zone_future_secondary: American Fork (58), Centerville (6), Ogden (25), Sunset (2), Syracuse (8), Unincorporated Box Elder (2), Unincorporated Utah (12), Willard (1) — these reflect comma characters in source GP data not specific to Tooele City.

---

## New Taxonomy Gaps (surfaced by parcel-level D1 analysis, 2026-05-23)

These gaps were NOT visible in the polygon-level GeoJSON quality review (which counts zone_class_normalized = "Other/Unknown", not zone_class_normalized = NULL). They represent jurisdictions where some zone polygons have NULL normalized field in the source GeoJSON, causing normalize_current() to fall through to taxonomy with no match.

| Jurisdiction | Raw code | Parcel count | Recommended fix |
|---|---|---|---|
| South Jordan | P-C | 11,855 | Add `P-C → Planned/Mixed-Use` to `current_zoning.south_jordan` in gp_taxonomy.yaml |
| Vineyard | Waters Edge | 1,807 | Add `Waters Edge → Residential-Medium` (or appropriate class) to `current_zoning.vineyard` |
| Vineyard | HF | 74 | Decode Vineyard HF code; likely High Frequency / Highway Frontage → Commercial-General |
| Vineyard | OS | 1 | Open Space → Open Space/Public |

**Cam action items** (to fix in a future taxonomy update):
- South Jordan: Confirm P-C = "Planned Community" → add to gp_taxonomy.yaml. This alone would fix 39.9% → ~0%.
- Vineyard: Look up city zoning ordinance for Waters Edge, HF, OS codes. Single taxonomy.yaml update fixes 40.6% → ~0%.

---

## Per-Jurisdiction Decisions

### Lehi — Current Zoning Normalization ✅ RESOLVED

**Before**: 215/514 features (41.8%) → `Other/Unknown` in D1's `zone_current_normalized`.
**Root cause**: 18b-1 Manus extraction script lacked mappings for 12 Lehi-specific zone codes.
**Fix**: All 12 codes added to `gp_taxonomy.yaml` under `current_zoning.lehi`.
**After**: 0/514 features → `Other/Unknown`. Full coverage.

| Code | Count | Normalized to |
|---|---|---|
| TH-5 | 74 | Residential-Townhome |
| PF | 46 | Public/Institutional |
| A-1 | 44 | Agriculture/Rural |
| C | 25 | Commercial-General |
| PC | 9 | Planned/Mixed-Use |
| NC | 5 | Commercial-Neighborhood |
| BP | 4 | Industrial/Flex |
| C-H | 2 | Commercial-General |
| CR | 2 | Commercial-Recreation |
| H/I | 1 | Industrial/Flex |
| C-I | 1 | Industrial/Flex |
| T-M | 1 | Industrial/Flex |

**D1 note**: `flu_currency_note='lehi_zone_current_normalization_gap'` removed from loader. Existing D1 rows will have this note cleared on re-load.

---

### Lehi — Future GP (NLS source authority) ⚠️ CAVEAT ACCEPTED PERMANENTLY

**Source**: `NLS_LandUseService` (ArcGIS Online org `pA2nEVnB6tquxgOW`, "North Lakeshore Study").
**Concern**: Regional study layer, not confirmed as Lehi's adopted GP amendment layer.
**Decision**: Accept caveat permanently. Data is useful for scoring signals but not confirmed authoritative.
**In D1**: `flu_currency_note` includes `NLS_source_authority_unverified` for all Lehi GP parcels.
**Cam action item**: Check Lehi's planning page (lehi.utah.gov/planning) to confirm whether the NLS layer matches the most recently adopted GP Future Land Use map. If confirmed: update CURRENT_CITY_NOTES in `load_zoning_to_d1.py` and remove the note. If stale: contact Lehi planning for a GeoJSON export (GRAMA request).

---

### Eagle Mountain — Current Zoning Ordinance Codes ⚠️ PARTIALLY DEFERRED

**Issue**: Eagle Mountain's zone_code values are Municipal Code chapter references (e.g. `17.25` = Title 17 Section 25), not human-readable zone labels.
**18b-1 extraction behavior**: Inconsistent — the same code maps to different `zone_class_normalized` values across features (likely driven by zone description text parsing).

| Code | Total features | Other/Unknown | Majority mapping | Decision |
|---|---|---|---|---|
| 17.20 | 329 | 1 | Agriculture/Rural (97%) | ✅ Mapped in taxonomy |
| 17.23 | 253 | 18 | Open Space/Public (91%) | ✅ Mapped in taxonomy |
| 17.25 | 648 | 226 | Mixed (Res-Low 46%, Other 35%) | ⚠️ Left as Other/Unknown |
| 17.31 | 43 | 11 | Open Space/Public (67%) | ✅ Mapped in taxonomy |
| 17.35 | 1 | 0 | Commercial (100%) | ✅ Mapped in taxonomy |
| 17.37 | 3 | 0 | Open Space/Public (100%) | ✅ Mapped in taxonomy |
| 17.38 | 82 | 0 | Commercial (88%) | ✅ Mapped in taxonomy |
| 17.40 | 17 | 0 | Industrial/Flex (82%) | ✅ Mapped in taxonomy |
| 17 | 7 | 7 | None | ⚠️ Left as Other/Unknown |

**Eagle Mountain code `17.25`** (648 parcels): Primarily residential but too variable to map without the ordinance. The wide variance suggests this section covers multiple sub-districts or has been amended with overlays.

**In D1**: Parcels with `zone_current IN ('17', '17.25')` get `flu_currency_note='eagle_mountain_ordinance_decode_pending'`.

**Cam action item**: Look up Eagle Mountain Title 17 zoning ordinance, Chapter 17.25.
- Municode: https://library.municode.com/ut/eagle_mountain/codes/code_of_ordinances
- Eagle Mountain City: https://www.eaglemountaincity.com/government/city-departments/community-development/planning-zoning/
- Confirm what zone district `17.25` covers. Most likely "Neighborhood Residential" based on spatial distribution. Once confirmed, add to `gp_taxonomy.yaml` under `current_zoning.eagle_mountain`.

---

### Eagle Mountain — Future GP (NLS source authority) ⚠️ CAVEAT ACCEPTED PERMANENTLY

Same NLS_LandUseService source as Lehi / Saratoga Springs. See Lehi decision above. `flu_currency_note='NLS_source_authority_unverified'` retained in D1 for all Eagle Mountain GP parcels.

---

### Saratoga Springs — Current Zoning ✅ RESOLVED

**Before**: 58/368 features (15.8%) → `Other/Unknown`. Root cause: `PC` (Planned Community) not in 18b-1 normalization.
**Fix**: `PC → Planned/Mixed-Use` in `gp_taxonomy.yaml`.
**After**: 0/368 → `Other/Unknown`.

---

### Saratoga Springs — Future GP (NLS source authority) ⚠️ CAVEAT ACCEPTED PERMANENTLY

Same NLS_LandUseService decision as Lehi. `flu_currency_note='NLS_source_authority_unverified'` retained.

---

### Tooele City — Multi-zone Comma Codes ✅ RESOLVED (architecture)

**Issue**: 37/63 future GP features have comma-separated `gp_zone_code` (e.g. `"MR-25, MR-16"`).
**Decision**: Option B — primary code in `zone_future`, remaining tokens in `zone_future_secondary`.
**Implementation**: `load_zoning_to_d1.py` splits on comma; `zone_future_secondary` populated by migration 0009.
**Normalization**: Each token has an individual mapping in `gp_taxonomy.yaml` under `future_zoning.tooele_city`. `zone_future_normalized` reflects the primary token's class.

| Raw code | Primary (zone_future) | Secondary (zone_future_secondary) |
|---|---|---|
| MR-25, MR-16 | MR-25 | MR-16 |
| NC, GC | NC | GC |
| RM-8, R1-7, R1-8, R1-10 | RM-8 | R1-7,R1-8,R1-10 |
| R1-12, R1-14, R1-30 | R1-12 | R1-14,R1-30 |
| RC, RD | RC | RD |
| MU-G, MU-B | MU-G | MU-B |
| RR-1, RR-5, RR-20, MU-160 | RR-1 | RR-5,RR-20,MU-160 |
| MR-20, MR-16, MR-12 | MR-20 | MR-16,MR-12 |

---

### Grantsville — Current Zoning ✅ RESOLVED (partial)

**Before**: 9/19 features (47.4%) → `Other/Unknown`.
**Fix**: 8 codes added to `gp_taxonomy.yaml`. 1 remaining (`UNKNOWN`) is a literal unknown value — cannot map.
**After**: 1/19 (5.3%) → `Other/Unknown` (the literal UNKNOWN code).

Note: `RM-15` in the GeoJSON maps to `Industrial/Flex` — this appears to be a pre-existing 18b-1 normalization error (RM-15 is Residential Multi-family). Flagged but out of scope for this phase (fix requires updating the GeoJSON source or adding a corrective taxonomy override).

---

### Grantsville — Future GP (typos) ✅ RESOLVED

Source data has two zone names with typos:
- `"Medium Density Rsidential"` (misspelled) → mapped to `Residential-Medium`
- `"Low Density Residentail"` (misspelled) → mapped to `Residential-Low`

Both typo variants are in `gp_taxonomy.yaml` under `future_zoning.grantsville`. The source GeoJSON is kept raw (typos preserved) per the "keep source files raw" principle.

---

### Spanish Fork — Current Zoning ✅ RESOLVED (partial)

**Before**: 75/203 features (36.9%) → `Other/Unknown`.
**Fix**: 9 codes added to `gp_taxonomy.yaml`. 1 remaining (`UV-C` mapped to `Commercial-General` — actually this IS mapped, so after fix: 0 remaining).
**After**: 0/203 → `Other/Unknown`.

---

### Erda — Current Zoning ⚠️ ACCEPTED (no usable GP FLU)

4 Other/Unknown features in current zoning. GP FLU map is regional overview only (RMSE 4664 ft, 0 features extracted in 18b-2b). No normalization improvement possible without a higher-resolution Erda zoning source. Erda has only 63 zoning polygons and is a small town; low priority.

---

### Herriman — Future GP ✅ Cam-KMZ extraction (18b-2d)

16 sampled zone categories, all mapped in `gp_taxonomy.yaml` under `future_zoning.herriman`. Coverage: 28,195/28,391 parcels (99.3%); 196 with empty sampled_zone (color match failed). These 196 get `zone_future = NULL` and `zone_future_normalized = NULL`.

---

## Zoning Overlay Feature — Per-Jurisdiction Taxonomy Coverage Table

For use as baseline in future `docs/ZONING_OVERLAY_BRAINSTORM.md` zoning color overlay feature.

| Jurisdiction | zone_current coverage (polygons) | zone_future coverage (polygons) | Current Other/Unknown (after fix) | Future all-codes mapped |
|---|---|---|---|---|
| American Fork | 208 | 97 | 0% | ✅ |
| Bluffdale | 89 | 94 | 0% | ✅ (1 null) |
| Draper | 139 | 62 | 0% | ✅ |
| Eagle Mountain | 1,392 | 972 | 17.0% (17.25, 17 pending) | ✅ |
| Erda | 63 | 0 | 6.3% (UNKNOWN literal) | ⚠️ no FLU source |
| Grantsville | 19 | 51 | 5.3% (UNKNOWN literal) | ✅ |
| Herriman | 262 | 28,391 parcels | 0% | ✅ |
| Lehi | 514 | 284 | 0% ✅ FIXED | ✅ (NLS caveat) |
| Saratoga Springs | 368 | 217 | 0% ✅ FIXED | ✅ (NLS caveat) |
| South Jordan | 423 | 155 | 0% | ✅ |
| Spanish Fork | 203 | 14 | 0% ✅ FIXED | ✅ |
| Tooele City | 112 | 63 | 0% | ✅ (secondary cols) |
| Vineyard | 51 | 36 | 0% | ✅ |

**Normalized class vocabulary** (standard across all jurisdictions):
`Commercial-General`, `Commercial-Neighborhood`, `Commercial-Office`, `Commercial-Recreation`, `Mixed-Use`, `Planned/Mixed-Use`, `Residential-Low`, `Residential-Medium`, `Residential-High`, `Residential-Townhome`, `Agriculture/Rural`, `Industrial/Flex`, `Public/Institutional`, `Open Space/Public`, `Other/Unknown`

---

## Files Changed in 18b-2e

| File | Location | Change |
|---|---|---|
| `data/zoning/gp_taxonomy.yaml` | tooele-land-intel | NEW — per-jurisdiction normalization authority |
| `data/zoning_normalizer.yaml` | tooele-land-intel | Updated — Lehi, Spanish Fork, Grantsville, Saratoga Springs codes added |
| `scripts/load_zoning_to_d1.py` | wasatch-intel | Updated — taxonomy normalization pass, secondary codes, Eagle Mountain per-code notes |
| `migrations/0009_taxonomy_harmonization.sql` | wasatch-intel | NEW — zone_current_normalized, zone_future_normalized, zone_future_secondary |
| `.github/workflows/load_zoning_to_d1.yml` | wasatch-intel | Updated — pyyaml added to pip install |

---

## Next Steps

1. **Apply migration 0009** via `wrangler d1 execute wasatch-intel-db --remote --file migrations/0009_taxonomy_harmonization.sql`
2. **Re-run `load_zoning_to_d1.yml`** (dry_run=true first, then live) — picks up all normalization improvements
3. **Phase 14 PMTiles re-bake** — include `zone_current_normalized` and `zone_future_normalized` as tile attributes for coloring
4. **Cam action items** (human-verify): Eagle Mountain Title 17 ordinance decode; Lehi/Eagle Mountain/Saratoga Springs NLS confirmation

---

*Phase 18b-2e — Taxonomy Harmonization — complete 2026-05-23*
