# Growth Signal Engine
## Davis & Weber County, Utah — Planning Intelligence Tool

Automatically scrapes Utah planning commission and city council meeting documents,
uses Claude AI to extract development signals, and generates a scored heat map
to identify high-opportunity areas for mini-flex and commercial real estate.

---

## What it does

1. **Scrapes** `utah.gov/pmn` for planning commission & city council notices
   across all Davis County and Weber County cities (30+ bodies)
2. **Downloads** meeting packet PDFs automatically
3. **AI parses** each PDF with Claude to extract:
   - Rezone requests
   - New subdivisions (rooftop growth signal)
   - Commercial development proposals
   - Mini-flex / light industrial opportunities
   - Infrastructure projects (roads, utilities)
   - Active developers in the market
4. **Scores** each city by growth signal strength (A–D grade)
5. **Generates** an interactive Leaflet.js heat map with popups

---

## Setup

```bash
cd growth_signal_engine
pip install requests beautifulsoup4 anthropic pdfplumber

# Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Usage

### Full pipeline (recommended first run)
```bash
python run.py
```

### Individual steps
```bash
python run.py --scrape-only        # Download PDFs only
python run.py --parse-only         # AI parse cached PDFs only
python run.py --aggregate-only     # Aggregate cached JSON only
python run.py --map-only           # Regenerate map from cached data
```

### Single city (good for testing)
```bash
python run.py --city "West Point"
python run.py --city "West Haven"
python run.py --city "Syracuse"
```

### Skip PDF download (faster, uses already-cached PDFs)
```bash
python run.py --no-download
```

---

## Output files

| File | Description |
|------|-------------|
| `data/pdfs/<city>/*.pdf` | Downloaded planning documents |
| `data/json/*.json` | Parsed signal JSON (one per PDF) |
| `data/json/city_growth_scores.json` | Aggregated scores by city |
| `data/json/miniflex_targets.json` | Ranked mini-flex opportunity targets |
| `data/json/summary.json` | High-level summary |
| `data/growth_signal_map.html` | Interactive heat map — open in browser |
| `logs/pipeline.log` | Full run log |
| `logs/scraper.log` | Scraper-specific log |

---

## Configured cities

### Davis County
Bountiful, Clearfield, Clinton, Davis County (unincorporated),
Farmington, Kaysville, Layton, North Salt Lake, South Weber,
Syracuse, West Bountiful, Woods Cross

### Weber County
Harrisville, Hooper, Ogden, Plain City, Pleasant View,
Riverdale, Roy, Sunset, Washington Terrace,
**West Haven** (your current project), **West Point** (primary target),
Weber County (unincorporated)

---

## Signal types extracted

| Signal | Weight | Description |
|--------|--------|-------------|
| MINIFLEX_OPPORTUNITY | 1.8x | Light industrial, flex, contractor condo proposals |
| COMMERCIAL_PROJECT | 1.6x | Commercial development applications |
| INFRASTRUCTURE | 1.5x | Roads, intersections, utilities |
| REZONE | 1.4x | Land use changes |
| ANNEXATION | 1.3x | New land entering city limits |
| LARGE_PROJECT | 1.3x | 50+ units or 5+ acres |
| GENERAL_PLAN_AMENDMENT | 1.2x | Future land use map changes |
| NEW_SUBDIVISION | 1.0x | Residential subdivisions (rooftop growth) |
| DEVELOPER_ACTIVITY | 0.8x | Named developers appearing in market |

---

## Mini-Flex Scoring Logic

Cities are scored for mini-flex opportunity based on:
- Overall growth signal score (50% weight)
- Commercial/flex/infrastructure signal count (8 pts each)
- Residential growth signal count (5 pts each — rooftop density)

This identifies areas with strong residential growth that are underserved
by flex/service commercial — the ideal mini-flex play.

---

## Next steps (planned)

- [ ] Parcel-level integration with existing Utah prospecting tool
- [ ] Owner lookup trigger for high-scoring parcels
- [ ] UDOT STIP future road project integration
- [ ] Building permit data layer
- [ ] Historical trend scoring (signal velocity over time)
