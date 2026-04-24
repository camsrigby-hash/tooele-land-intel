# RE Development Intelligence Tool — Project Status v4

**Date:** 2026-03-30  
**Session:** v4 (Post-Crash Recovery + Full Commercial Pipeline Run)

---

## What Was Accomplished This Session

### 1. Road Adjacency Module (`road_adjacency.py`) — NEW

A full road adjacency scoring module was built and integrated using the **UGRC Utah Roads** ArcGIS feature service. This was the #1 blocker for A-grade gas station scores.

| Feature | Detail |
| :--- | :--- |
| Data source | UGRC Utah Roads FeatureServer (CARTOCODE 1–8) |
| Roads loaded | 2,064 Davis County + 2,284 Weber County = 4,348 segments |
| Key fields | `CARTOCODE`, `DOT_FCLASS`, `DOT_AADT`, `SPEED_LMT` |
| Corner detection | Two distinct roads within 100m of parcel centroid |
| AADT scoring | 0–100 scale: 150k+ AADT = 100, 50k = 70, 10k = 30 |
| Caching | Road data cached to `data/cache/roads_davis.json` / `roads_weber.json` |

**Impact:** Gas station scores now reach **A-grade (93.6/100)** on high-traffic corridors (I-15 adjacent parcels in Layton/Kaysville with 130k–147k AADT).

---

### 2. Layer 1 Growth Signals (`growth_signal_generator.py`) — NEW

A multi-source growth signal generator was built to produce `data/json/city_growth_scores.json`.

**Data sources used:**
- Utah Governor's Office of Planning and Budget (GOPB) 2024 building permit data
- UDOT STIP 2024 ArcGIS service — 415 active projects fetched for Davis+Weber bbox
- Planning commission agenda scraping (city websites) + OpenAI GPT-4.1-mini analysis

**Final growth scores (after STIP enrichment):**

| City | County | Score | STIP Projects | STIP Value |
| :--- | :--- | :--- | :--- | :--- |
| Layton | Davis | 93 | 24 | $34.8M |
| Kaysville | Davis | 80 | 24 | $919.1M |
| Clearfield | Davis | 80 | 42 | $558.4M |
| Syracuse | Davis | 80 | 25 | $166.0M |
| Clinton | Davis | 80 | 48 | $1,156.7M |
| Farmington | Davis | 80 | 21 | $217.3M |
| West Haven | Weber | 80 | 23 | $252.2M |
| Riverdale | Weber | 70 | 41 | $555.0M |
| Farr West | Weber | 70 | 49 | $197.0M |
| Roy | Weber | 61 | 54 | $1,070.9M |

Notable STIP projects:
- **SR-177 West Davis Highway** (I-15 to SR-193): **$880M** — transformative for Clinton/Syracuse/West Haven
- **SR-108** (SR-37 to 4275 South): $123M expansion
- **US-89 Interchange, 200 North, Kaysville**: Direct interchange upgrade

---

### 3. Full Commercial Pipeline Run (`run_commercial_pipeline.py`) — COMPLETED

**10,000 non-residential parcels** (5,000 Davis + 5,000 Weber) scored with full road adjacency.

#### Final Results

| Mode | A-grade | B-grade | C-grade | Total Qualifying |
| :--- | :--- | :--- | :--- | :--- |
| Gas Station | **256** | 661 | ~3,579 | 4,496 |
| Mini-Flex | **465** | 2,838 | ~2,447 | 5,000 (capped) |

#### Top Gas Station Sites (A-grade, score ~93.6/100)

| Address | City | Acres | AADT | Score |
| :--- | :--- | :--- | :--- | :--- |
| 77 N Main St | Layton | 1.68 | 129,000 | 93.6 |
| 385 W Golden Ave | Layton | 2.58 | 130,000 | 93.6 |
| 330 S Fort Ln | Layton | 2.76 | 129,000 | 93.6 |
| 720 S Main St | Layton | 2.75 | 147,000 | 93.6 |
| 530 W Old Mill Ln | Kaysville | 1.68 | 124,000 | 91.0 |
| 1399 W 2100 S | West Haven | 2.00 | 96,000 | 91.0 |

#### Top Mini-Flex Sites (A-grade, score ~82.6/100)

| Address | City | Acres | Nearest Arterial |
| :--- | :--- | :--- | :--- |
| 1592 N Main St | Layton | 1.00 | 0.04 mi |
| 1977 N Heritage Park Blvd | Layton | 1.35 | 0.04 mi |
| 1436 N Main St | Layton | 1.55 | 0.04 mi |
| 500 N Main St | Layton | 1.08 | 0.02 mi |
| 1530 N Main St | Layton | 1.01 | 0.03 mi |

---

## Current File Structure

```
re-development-tool/
├── parcel_fetcher.py          # Resilient ArcGIS fetcher (micro-batch + caching)
├── parcel_scorer.py           # Dual-mode scorer (gas_station + miniflex) w/ road adjacency
├── parcel_map.py              # Interactive Folium map generator
├── parcel_run.py              # Full county pipeline orchestrator
├── road_adjacency.py          # UGRC road centerline scoring module (NEW)
├── growth_signal_generator.py # Layer 1 growth signal generator (NEW)
├── run_commercial_pipeline.py # Targeted commercial parcel pipeline (NEW)
├── data/
│   ├── parcel_map.html        # Interactive scored parcel map (OUTPUT)
│   ├── scored_gas.geojson     # Gas station scored parcels (OUTPUT)
│   ├── scored_mf.geojson      # Mini-flex scored parcels (OUTPUT)
│   ├── pipeline.log           # Full pipeline log
│   ├── commercial_pipeline.log # Commercial run log
│   ├── json/
│   │   ├── city_growth_scores.json  # Layer 1 growth signals (OUTPUT)
│   │   └── stip_projects.geojson    # UDOT STIP 415 projects (OUTPUT)
│   ├── cache/
│   │   ├── commercial/        # Commercial parcel page caches
│   │   ├── growth/            # Growth signal caches
│   │   └── roads_davis.json   # Road segment cache
│   │   └── roads_weber.json
│   └── parcels/               # County-level parcel GeoJSON cache
└── PROJECT_STATUS_v4.md       # This document
```

---

## Known Limitations & Next Steps

### Immediate (Phase 2 Roadmap)

1. **Corner Lot Detection** — Currently returns 0 for all parcels because the road spatial index uses centroids, not parcel polygon boundaries. Fix: use parcel polygon edges for intersection detection.

2. **Duplicate Parcels in Top Results** — Some addresses appear 2–3 times (same parcel, different OBJECTIDs). Fix: deduplicate on `PARCEL_ID` before scoring.

3. **City Name Matching for Growth Signals** — `PARCEL_CITY` values like "LAYTON" don't match "Layton" in growth_scores dict. Fix: normalize to title case before lookup.

### Medium-Term (Phase 3 Roadmap)

4. **Full County Overnight Run** — Run `python3.11 parcel_run.py --refresh` overnight to process all 87k Davis + 200k Weber parcels. The caching system ensures crash recovery.

5. **Zoning Layer Integration** — UGRC has a statewide zoning layer; adding it would allow filtering by `C-1`, `C-2`, `M-1` zones for more precise commercial targeting.

6. **Parcel Polygon Edge Road Intersection** — Replace centroid-based road proximity with true polygon boundary intersection for accurate corner lot detection.

---

## How to Run

```bash
# Targeted commercial run (15 min, uses cached data on re-run)
cd /home/ubuntu/re-development-tool
python3.11 run_commercial_pipeline.py

# Full county run (2-3 hours, crash-safe with micro-batch caching)
python3.11 parcel_run.py --refresh

# Regenerate growth signals only
python3.11 growth_signal_generator.py

# View map
open data/parcel_map.html
```
