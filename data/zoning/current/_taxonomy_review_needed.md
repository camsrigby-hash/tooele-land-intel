# Phase 18b-1 — Taxonomy review needed: Lehi City

## Issue

Lehi City's normalization has **215 of 514 features (41.8%) mapped to `Other/Unknown`**, making it the least useful city in the dataset for zoning-based scoring.

## Root cause

The normalization rules in `scripts/normalize_zone_code.py` (or equivalent) do not cover Lehi-specific zone codes. Lehi uses a distinct code vocabulary not found in the other 12 cities.

## Breakdown of Other/Unknown codes (Lehi)

| Zone code | Count | Expected normalization |
|---|---|---|
| TH-5 | 74 | `Residential-High` or `Residential-Townhome` (townhome/attached) |
| PF | 46 | `Public/Institutional` (public facilities) |
| A-1 | 44 | `Agriculture/Rural` (note: RA-1 already maps correctly, but A-1 does not) |
| C | 25 | `Commercial-General` |
| PC | 9 | `Planned/Master-Planned` |
| NC | 5 | `Commercial-Neighborhood` |
| BP | 4 | `Industrial/Flex` or `Commercial-Business-Park` |
| C-H | 2 | `Commercial-General` (highway commercial) |
| CR | 2 | TBD — likely Commercial Recreation |
| H/I | 1 | `Industrial-Heavy` |
| C-I | 1 | TBD — Commercial-Industrial |
| T-M | 1 | `Special/Employment` (technology-manufacturing) |
| HC | 1 | `Commercial-General` (highway commercial) |
| **TOTAL** | **215** | — |

The largest single gap: `TH-5` (74 features = 14.4% of all Lehi zoning) is townhome density — it should map to a residential subclass. `A-1` (44 features) is plain agricultural, a variant of `RA-1` already handled.

## Action required

Before Phase 18b-3 D1 load, the normalization rules for Lehi must be extended to cover these codes. Options:
1. **Update `normalize_zone_code.py`**: Add Lehi-specific entries (code prefix pattern or exact match) for TH-5, PF, A-1, C, PC, NC, BP, etc.
2. **City-specific override YAML**: Add a `lehi_overrides` section to `zoning_normalizer.yaml` (Phase 13b-5 taxonomy file).

Either way, the goal is to reduce Other/Unknown from 42% to <5% for Lehi. After fix, re-run normalization and update `lehi_ut_zoning.geojson`.

## Impact if not fixed

Other/Unknown features receive a zoning_score of 0 (or fallback to prop_class) in the scoring engine. For Lehi, 42% of parcels fall into this bucket — the score will be systematically biased toward the 58% of parcels with known normalization, understating value in the Other/Unknown parcels (which include high-density residential, commercial, and public/institutional land, all potentially high-value for spread signal).
