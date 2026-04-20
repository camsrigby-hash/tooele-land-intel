# Tooele Land Intelligence

A free, browser-based system for analyzing land development opportunities in Tooele County, Utah (Grantsville, Erda, Tooele City, and unincorporated county).

Runs entirely on **GitHub Actions** — no local installs, no servers, no cost. You interact with it from any web browser.

## What it does

Two automated workflows plus a Claude Skill:

1. **Parcel Lookup** (`parcel-lookup.yml`) — On demand. You enter a Tooele County parcel ID; it returns jurisdiction, current zoning, future land use, acreage, owner, lat/lon, and a static map. Posts results back as a comment on a GitHub issue.

2. **Agendas Watch** (`agendas-watch.yml`) — Weekly (Mondays 6am Mountain). Scrapes planning commission and city council agendas from Tooele County, Grantsville City, and Erda City. Downloads new PDFs, extracts text, classifies items (rezone / subdivision / annexation / CUP / site plan / general plan amendment), pulls any address or parcel reference, and appends rows to `data/agenda_items.csv`.

3. **Tooele Parcel Analysis Skill** (`skill/tooele-parcel-analysis/`) — A Claude Skill that combines parcel lookup output + the agenda CSV into a highest-and-best-use memo, comparable-sales context, and a target buyer/developer list. Drop it into Claude.ai and point it at any parcel ID.

## Setup (one time, ~10 minutes)

1. Create a free GitHub account at github.com if you don't have one.
2. Create a new **public** repo named `tooele-land-intel` (public = free Actions minutes are unlimited).
3. Upload the files in this folder (drag and drop in the GitHub web UI works).
4. Go to the **Actions** tab and enable workflows.
5. (Optional) For the parcel lookup, open an issue titled `lookup: 01-440-0-0019` (replace with any parcel ID) and the workflow will respond.

## Files

```
.github/workflows/
  parcel-lookup.yml         # On-demand parcel research
  agendas-watch.yml         # Weekly agenda scraper
scripts/
  lookup_parcel.py          # ArcGIS REST query against UGRC + city GIS layers
  scrape_agendas.py         # Scrapes 3 jurisdictions, parses PDFs
  utils/
    arcgis.py               # ArcGIS REST helpers
    pdf_extract.py          # Pull text from agenda PDFs
    classify.py             # Lightweight rule-based agenda-item classifier
data/
  agenda_items.csv          # Append-only log of scraped items
  jurisdictions.yaml        # Sources, URLs, selectors per jurisdiction
skill/tooele-parcel-analysis/
  SKILL.md                  # Claude Skill definition
  references/               # Zoning code summaries per jurisdiction
docs/
  parcel_lookup_manual.md   # How to do this by hand if the script breaks
```

## How the pieces connect

```
┌─────────────────┐    ┌──────────────────┐
│  GitHub Issue   │───▶│ parcel-lookup.yml │──┐
│  "lookup: ..."  │    └──────────────────┘  │
└─────────────────┘                          ▼
                                       ┌──────────┐
┌─────────────────┐    ┌──────────────┐│  Issue   │
│  Cron schedule  │───▶│agendas-watch ││ comment  │
│  Weekly Mon 6am │    │     .yml     │└──────────┘
└─────────────────┘    └──────────────┘
                              │
                              ▼
                       data/agenda_items.csv
                              │
                              ▼
                    ┌──────────────────────┐
                    │  Claude.ai + Skill   │  ◄── you, asking analytical
                    │  reads CSV + lookup  │      questions
                    │  → analysis memo     │
                    └──────────────────────┘
```

## Cost

$0. Public-repo Actions minutes are unmetered. Storage is well under the 1GB free tier.

## Disclaimer

Zoning, ownership, and entitlement information is informational only. Always verify with the relevant city/county before any acquisition or development decision.
