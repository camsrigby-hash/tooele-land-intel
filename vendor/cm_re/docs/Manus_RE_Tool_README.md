# Parcel Polygon Pipeline — Tier 1 + Tier 2

## What This Does

**Tier 1** renders your scored parcels as actual polygon boundaries (not dots) on satellite imagery, color-coded by vacancy status, with interactive filters.

**Tier 2** downloads NAIP aerial imagery and classifies land cover within each parcel — catching cases where UGRC says "vacant" but there's actually a building (or vice versa).

**Income scoring** flips the logic for gas station mode: lower-income areas score higher (C-store capture), higher-income areas score higher for miniflex/retail.

## Files

| File | Purpose |
|------|---------|
| `parcel_polygon_map.py` | Tier 1: Polygon overlay map generator |
| `land_cover_analyzer.py` | Tier 2: Satellite land cover classification + income scoring |
| `run_polygon_pipeline.py` | Wrapper that runs both tiers in sequence |
| `scored_export.py` | Adapter to export scored data from existing pipeline |
| `requirements.txt` | Python dependencies |
| `run.bat` | Windows one-click launcher |

## Setup

1. Copy all files into `C:\Users\camsr\OneDrive\Desktop\RE Identification Tool\`
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
   If rasterio fails on Windows, try: `conda install -c conda-forge rasterio`
   (Tier 1 works without rasterio — only Tier 2 needs it)

3. Add the JSON export to your existing pipeline. In `parcel_run.py`, after scoring:
   ```python
   from scored_export import export_scored_parcels
   export_scored_parcels(scored_results, mode="gas_station", county="davis")
   ```

## Running

**Option A — Double-click `run.bat`** (interactive menu)

**Option B — Command line:**
```
# Tier 1 only
python run_polygon_pipeline.py --mode gas_station

# Tier 1 + Tier 2
python run_polygon_pipeline.py --mode gas_station --land-cover

# Everything including income
python run_polygon_pipeline.py --mode gas_station --land-cover --income

# Point at specific scored data
python run_polygon_pipeline.py --input data/scored_parcels_gas_station.json --mode gas_station
```

## Output

- `data/parcel_polygon_map_gas_station.html` — Tier 1 interactive map
- `data/land_cover_results_gas_station.json` — Tier 2 enriched GeoJSON

Open the HTML in your browser. The control panel lets you:
- Toggle vacant/underutilized/developed parcels
- Adjust polygon opacity (see satellite through polygons)
- Set minimum score threshold
- Set minimum acreage
- Filter to corner parcels only

## Integration with Existing Pipeline

The pipeline flow is:

```
parcel_run.py (existing)
    ↓ scored parcel list
scored_export.py (new — saves JSON)
    ↓ data/scored_parcels_{mode}.json
run_polygon_pipeline.py (new)
    ↓ fetches polygon geometries from UGRC
    ↓ classifies vacancy
    ↓ generates Leaflet HTML
    data/parcel_polygon_map_{mode}.html  ← Tier 1 output
    ↓ (optional) downloads NAIP imagery
    ↓ classifies land cover per parcel
    data/land_cover_results_{mode}.json  ← Tier 2 output
```

## Notes

- Polygon fetch is capped at 500 parcels by default (adjustable with `--max-polygons`). This keeps UGRC query volume reasonable.
- Land cover analysis is capped at 200 parcels (`--max-lc`). Each parcel requires an imagery download.
- Income data comes from Census ACS at the block group level. Free, no API key needed.
- Gas station income scoring: lower income = higher score (C-store capture). Miniflex: higher income = higher score (discretionary spend).
