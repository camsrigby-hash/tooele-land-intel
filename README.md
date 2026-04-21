# Tooele Land Intel

Analyze parcels in Tooele County, Utah for development potential. Queries live county GIS data (ArcGIS REST), scrapes planning commission agendas, and classifies nearby development activity.

## Features
- **Parcel lookup** — owner, acreage, zoning, jurisdiction, General Plan designation
- **Agenda scraper** — planning commission and city council agendas (Tooele County, Grantsville, Erda)
- **PDF extraction** — pulls text from agenda exhibit PDFs
- **Development classifier** — categorizes agenda items by type (rezone, subdivision, commercial, etc.)

## Quick start (local)

```bash
git clone https://github.com/camsrigby-hash/tooele-land-intel.git
cd tooele-land-intel
pip install -r requirements.txt

# Look up a parcel
python scripts/lookup_parcel.py 01-440-0-0019 --pretty

# Scrape all jurisdiction agendas
python scripts/scrape_agendas.py

# Scrape just Grantsville
python scripts/scrape_agendas.py --jurisdiction grantsville --output data/agendas.json
```

## Cloud / GitHub Actions usage

### Parcel lookup (on-demand)
1. Go to [Actions → Parcel Lookup](../../actions/workflows/parcel-lookup.yml)
2. Click **Run workflow**, enter parcel ID
3. Results print in job log and save as a downloadable artifact

### Weekly agenda scrape (scheduled)
- Runs automatically every Monday at 08:00 UTC
- Trigger manually: [Actions → Weekly Agenda Scrape](../../actions/workflows/agendas-watch.yml)

## File structure

```
scripts/
  lookup_parcel.py     Main parcel lookup (takes parcel ID, outputs JSON)
  scrape_agendas.py    Scrapes planning agendas from three jurisdictions
  arcgis.py            ArcGIS REST helper (query_layer, query_by_point)
  classify.py          Classifies agenda items by development type
  pdf_extract.py       Extracts text/tables from agenda PDFs

data/
  jurisdictions.yaml   ArcGIS endpoints, agenda URLs, field names
  agendas/             Scraped agenda output (gitignored)

docs/
  parcel_lookup_manual.md   Human-readable reference

skill/tooele-parcel-analysis/
  SKILL.md             Claude skill definition
  references/
    arcgis_endpoints.md     Verified ArcGIS endpoint reference
    jurisdictions.md        Zoning codes, GP categories, meeting schedules

.github/workflows/
  parcel-lookup.yml    Manual workflow_dispatch parcel lookup
  agendas-watch.yml    Scheduled weekly agenda scrape
```

## ArcGIS data sources
All data comes from Tooele County's public GIS server (no API key required):
- **Base:** `https://tcgisws.tooeleco.gov/server/rest/services`
- **Parcels:** `Parcels/MapServer/0` — field `Parcel_ID`
- **Zoning:** `Zoning/MapServer` — layers 1 (Erda), 4 (County), 7 (Grantsville)
- **General Plan:** `GeneralPlan_2022_LandUseCA/MapServer/0`
- **Municipalities:** `Municipalities/MapServer`

See `skill/tooele-parcel-analysis/references/arcgis_endpoints.md` for full field-name reference.

## Test parcel
Parcel `01-440-0-0019` — ~13 acres, Erda Way area, owner TITMUS SUNNIE JT, Greenbelt status.

## Requirements
- Python 3.11+
- `requests`, `PyYAML`, `pdfplumber`, `beautifulsoup4` (see `requirements.txt`)
- No API keys required — all data is public

## Jurisdictions covered
| Jurisdiction | Zoning layer | Agendas |
|---|---|---|
| Tooele County (unincorporated) | Layer 4 | tooeleco.org |
| Grantsville City | Layer 7 | grantsvillecity.org |
| Erda City | Layer 1 | erdacity.com |
