"""
parcel_polygon_map.py — Tier 1: Smart Polygon Overlay on Satellite Imagery
============================================================================
Renders scored parcels as actual POLYGON boundaries (not dots) on satellite
imagery. Color-codes by vacancy confidence. Adds transparency slider, vacancy
filter toggle, score threshold filter, and mode toggle.

Integration:
    Called after parcel_scorer.py produces scored output. Fetches polygon
    geometries from UGRC for the scored subset only (not all 287k parcels).

Usage:
    from parcel_polygon_map import generate_polygon_map
    generate_polygon_map(scored_parcels, mode="gas_station")

    Or standalone:
    python parcel_polygon_map.py --input data/scored_parcels.json --mode gas_station

Dependencies:
    requests

New file — does NOT replace parcel_map.py.
"""

import json
import logging
import time
import math
import argparse
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    raise ImportError("pip install requests")

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── UGRC Feature Service Config ────────────────────────────────────────────────

UGRC_BASE = "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services"
COUNTIES = {
    "davis":  "Parcels_Davis_LIR",
    "weber":  "Parcels_Weber_LIR",
}
BATCH_SIZE = 50          # Parcel IDs per UGRC query (URL length safe)
REQUEST_DELAY = 0.35     # Seconds between UGRC requests (rate limiting)
MAX_RETRIES = 3

# ── Vacancy Classification ─────────────────────────────────────────────────────

# Prop classes that indicate undeveloped or rezone-opportunity land
VACANT_PROP_CLASSES = [
    "vacant", "agricultural", "greenbelt", "unimproved", "farm",
    "a-1", "a-2", "a-3", "ag", "open space",
]

# Prop classes that indicate developed land unlikely to be acquisition targets
DEVELOPED_PROP_CLASSES = [
    "residential", "commercial", "industrial", "condo", "apartment",
    "institutional", "government", "church", "school",
]


def classify_vacancy(ugrc_props: dict) -> dict:
    """
    Classify a parcel's vacancy status from UGRC LIR attributes.
    Returns dict with vacancy_status, vacancy_color, vacancy_confidence.
    """
    bldg_sqft = ugrc_props.get("BLDG_SQFT") or 0
    built_yr  = ugrc_props.get("BUILT_YR") or 0
    prop_class = (ugrc_props.get("PROP_CLASS") or "").strip().lower()

    # ── Classification cascade (most confident first) ──────────────────────
    # 1. No building footprint and no build year → high-confidence vacant
    if bldg_sqft < 200 and (built_yr == 0 or built_yr is None):
        return {
            "vacancy_status": "vacant",
            "vacancy_label": "Vacant (no building)",
            "vacancy_color": "#00e639",       # Bright green
            "vacancy_confidence": 0.95,
        }

    # 2. Ag/vacant prop class regardless of building data
    if any(vc in prop_class for vc in VACANT_PROP_CLASSES):
        # Ag with no real building → rezone opportunity
        if bldg_sqft < 500:
            return {
                "vacancy_status": "ag_vacant",
                "vacancy_label": "Agricultural / Rezone Opportunity",
                "vacancy_color": "#ffd700",   # Gold
                "vacancy_confidence": 0.85,
            }
        # Ag with building (farmhouse) → possible acquisition / rezone
        return {
            "vacancy_status": "ag_improved",
            "vacancy_label": "Ag w/ Structure (rezone candidate)",
            "vacancy_color": "#ffaa00",       # Dark gold
            "vacancy_confidence": 0.60,
        }

    # 3. Very small building on otherwise large parcel → underutilized
    if bldg_sqft < 1000:
        return {
            "vacancy_status": "underutilized",
            "vacancy_label": f"Underutilized ({int(bldg_sqft)} sqft bldg)",
            "vacancy_color": "#ff8c00",       # Orange
            "vacancy_confidence": 0.50,
        }

    # 4. Recent construction (built after 2000 with real building) → developed
    if built_yr > 2000 and bldg_sqft >= 1000:
        return {
            "vacancy_status": "developed_new",
            "vacancy_label": f"Developed ({built_yr}, {int(bldg_sqft)} sqft)",
            "vacancy_color": "#ff3333",       # Red
            "vacancy_confidence": 0.05,
        }

    # 5. Older building → may be teardown candidate
    if built_yr > 0 and built_yr <= 2000 and bldg_sqft >= 1000:
        return {
            "vacancy_status": "developed_old",
            "vacancy_label": f"Older Structure ({built_yr}, {int(bldg_sqft)} sqft)",
            "vacancy_color": "#cc4444",       # Dark red
            "vacancy_confidence": 0.15,
        }

    # 6. Fallback
    return {
        "vacancy_status": "unknown",
        "vacancy_label": "Insufficient data",
        "vacancy_color": "#888888",
        "vacancy_confidence": 0.30,
    }


# ── UGRC Polygon Fetch ─────────────────────────────────────────────────────────

def fetch_parcel_polygons(parcel_ids: list, county: str) -> dict:
    """
    Fetch GeoJSON polygon geometries from UGRC for specific parcel IDs.
    Returns a GeoJSON FeatureCollection with WGS84 coordinates.
    """
    service = COUNTIES.get(county.lower())
    if not service:
        log.warning(f"Unknown county '{county}' — skipping polygon fetch")
        return {"type": "FeatureCollection", "features": []}

    url = f"{UGRC_BASE}/{service}/FeatureServer/0/query"
    all_features = []
    total_batches = math.ceil(len(parcel_ids) / BATCH_SIZE)

    for batch_idx in range(0, len(parcel_ids), BATCH_SIZE):
        batch = parcel_ids[batch_idx : batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1

        # Build WHERE clause with escaped IDs
        id_list = ",".join(f"'{pid}'" for pid in batch)
        params = {
            "where": f"PARCEL_ID IN ({id_list})",
            "outFields": "PARCEL_ID,PARCEL_ACRES,BLDG_SQFT,BUILT_YR,PROP_CLASS,"
                         "PARCEL_CITY,Shape__Area,TOTAL_MKT_VALUE",
            "outSR": "4326",
            "f": "geojson",
            "returnGeometry": "true",
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                if "features" in data:
                    all_features.extend(data["features"])
                    log.info(f"  Batch {batch_num}/{total_batches}: "
                             f"{len(data['features'])} polygons fetched")
                elif "error" in data:
                    log.warning(f"  Batch {batch_num} UGRC error: {data['error']}")
                break

            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning(f"  Batch {batch_num} failed (attempt {attempt+1}), "
                                f"retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    log.error(f"  Batch {batch_num} failed permanently: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Total polygons fetched for {county.title()}: {len(all_features)}")
    return {"type": "FeatureCollection", "features": all_features}


# ── Merge Scored Data + Vacancy Classification ─────────────────────────────────

def merge_and_classify(geojson: dict, score_lookup: dict) -> dict:
    """
    For each polygon feature, merge the pipeline's scored attributes and
    add vacancy classification.
    """
    enriched = []
    for feature in geojson.get("features", []):
        pid = feature.get("properties", {}).get("PARCEL_ID")
        if not pid:
            continue

        # Merge scored pipeline data
        scored = score_lookup.get(pid, {})
        props = feature["properties"]
        props["score"]             = scored.get("score", 0)
        props["grade"]             = scored.get("grade", "")
        props["mode"]              = scored.get("mode", "")
        props["corner"]            = scored.get("is_corner", False)
        props["aadt"]              = scored.get("aadt", 0)
        props["competition_score"] = scored.get("competition_score", 0)
        props["competition_note"]  = scored.get("competition_note", "")
        props["growth_signal"]     = scored.get("growth_signal_score", 0)
        props["stip_nearby"]       = scored.get("stip_nearby", False)
        props["address"]           = scored.get("address", "")

        # Classify vacancy from UGRC attributes
        vacancy_info = classify_vacancy(props)
        props.update(vacancy_info)

        # Compute acres (Davis has null PARCEL_ACRES)
        acres = props.get("PARCEL_ACRES")
        if not acres and props.get("Shape__Area"):
            acres = round(props["Shape__Area"] * 0.000139, 2)
        props["acres"] = acres or 0

        feature["properties"] = props
        enriched.append(feature)

    geojson["features"] = enriched
    return geojson


# ── HTML Map Generator ─────────────────────────────────────────────────────────

def _build_polygon_html(geojson: dict, mode: str = "gas_station") -> str:
    """Build the full Leaflet HTML with polygon overlay and controls."""

    geojson_str = json.dumps(geojson)
    mode_label = "Gas Station / C-Store" if mode == "gas_station" else "Mini-Flex / Light Industrial"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Parcel Polygon Map — {mode_label}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  #map {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; z-index: 1; }}

  /* Control panel */
  .control-panel {{
    position: absolute; top: 12px; right: 12px; z-index: 1000;
    background: rgba(15, 15, 25, 0.92); color: #eee;
    border-radius: 10px; padding: 16px 18px;
    font-size: 13px; min-width: 260px; max-width: 300px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    backdrop-filter: blur(8px);
    max-height: 90vh; overflow-y: auto;
  }}
  .control-panel h3 {{
    margin: 0 0 12px 0; font-size: 15px; color: #fff;
    border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 8px;
  }}
  .control-group {{
    margin-bottom: 14px;
  }}
  .control-group label {{
    display: block; margin-bottom: 4px; font-size: 12px;
    color: #aaa; text-transform: uppercase; letter-spacing: 0.5px;
  }}
  .control-group input[type=range] {{
    width: 100%; cursor: pointer; accent-color: #00e639;
  }}
  .control-group .value-display {{
    float: right; color: #00e639; font-weight: 600;
  }}
  .control-group select {{
    width: 100%; padding: 6px 8px; background: #222; color: #eee;
    border: 1px solid #444; border-radius: 4px; font-size: 13px;
  }}

  /* Toggle switch */
  .toggle-row {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 8px;
  }}
  .toggle-row span {{ font-size: 13px; }}
  .toggle {{
    position: relative; width: 44px; height: 24px; cursor: pointer;
  }}
  .toggle input {{ display: none; }}
  .toggle .slider {{
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: #444; border-radius: 12px; transition: 0.3s;
  }}
  .toggle .slider::before {{
    content: ''; position: absolute; width: 18px; height: 18px;
    left: 3px; bottom: 3px; background: #fff; border-radius: 50%;
    transition: 0.3s;
  }}
  .toggle input:checked + .slider {{ background: #00e639; }}
  .toggle input:checked + .slider::before {{ transform: translateX(20px); }}

  /* Legend */
  .legend-item {{
    display: flex; align-items: center; margin-bottom: 5px; font-size: 12px;
  }}
  .legend-swatch {{
    width: 16px; height: 16px; border-radius: 3px; margin-right: 8px;
    border: 1px solid rgba(255,255,255,0.3); flex-shrink: 0;
  }}

  /* Stats bar */
  .stats-bar {{
    position: absolute; bottom: 12px; left: 12px; z-index: 1000;
    background: rgba(15, 15, 25, 0.88); color: #eee;
    border-radius: 8px; padding: 10px 16px; font-size: 12px;
    backdrop-filter: blur(8px);
  }}
  .stats-bar span {{ margin-right: 16px; }}
  .stats-bar .count {{ color: #00e639; font-weight: 700; }}
</style>
</head>
<body>
<div id="map"></div>

<!-- Control Panel -->
<div class="control-panel">
  <h3>Parcel Scanner — {mode_label}</h3>

  <!-- Vacancy Filter -->
  <div class="control-group">
    <div class="toggle-row">
      <span>Vacant / Rezone Only</span>
      <label class="toggle">
        <input type="checkbox" id="vacancyToggle" checked>
        <span class="slider"></span>
      </label>
    </div>
    <div class="toggle-row">
      <span>Include Underutilized</span>
      <label class="toggle">
        <input type="checkbox" id="underutilizedToggle" checked>
        <span class="slider"></span>
      </label>
    </div>
    <div class="toggle-row">
      <span>Show Developed (red)</span>
      <label class="toggle">
        <input type="checkbox" id="developedToggle">
        <span class="slider"></span>
      </label>
    </div>
  </div>

  <!-- Polygon Opacity -->
  <div class="control-group">
    <label>Polygon Opacity <span class="value-display" id="opacityVal">60%</span></label>
    <input type="range" id="opacitySlider" min="0" max="100" value="60" step="5">
  </div>

  <!-- Score Threshold -->
  <div class="control-group">
    <label>Min Score <span class="value-display" id="scoreVal">0</span></label>
    <input type="range" id="scoreSlider" min="0" max="100" value="0" step="5">
  </div>

  <!-- Acreage Filter -->
  <div class="control-group">
    <label>Min Acres <span class="value-display" id="acresVal">0.0</span></label>
    <input type="range" id="acresSlider" min="0" max="10" value="0" step="0.25">
  </div>

  <!-- Corner Only Filter -->
  <div class="control-group">
    <div class="toggle-row">
      <span>Corner Parcels Only</span>
      <label class="toggle">
        <input type="checkbox" id="cornerToggle">
        <span class="slider"></span>
      </label>
    </div>
  </div>

  <!-- Legend -->
  <div class="control-group" style="margin-top: 16px;">
    <label>Legend</label>
    <div class="legend-item"><div class="legend-swatch" style="background:#00e639"></div>Vacant (no building)</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#ffd700"></div>Ag / Rezone Opportunity</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#ffaa00"></div>Ag w/ Structure</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#ff8c00"></div>Underutilized</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#ff3333"></div>Developed (new)</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#cc4444"></div>Developed (old / teardown?)</div>
  </div>
</div>

<!-- Stats Bar -->
<div class="stats-bar" id="statsBar">
  <span>Visible: <span class="count" id="visibleCount">0</span></span>
  <span>Vacant: <span class="count" id="vacantCount">0</span></span>
  <span>Avg Score: <span class="count" id="avgScore">0</span></span>
</div>

<script>
// ── Initialize Map ────────────────────────────────────────────────────────────
const map = L.map('map', {{
  center: [41.1, -111.97],
  zoom: 11,
  zoomControl: true,
}});

// Satellite base layer (Esri World Imagery)
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution: 'Tiles &copy; Esri &mdash; Maxar, Earthstar Geographics',
  maxZoom: 19,
}}).addTo(map);

// Road + label overlay
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  maxZoom: 19, opacity: 0.55,
}}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  maxZoom: 19, opacity: 0.6,
}}).addTo(map);

// ── Load GeoJSON ──────────────────────────────────────────────────────────────
const parcelData = {geojson_str};
let polygonLayer = null;

function getPopupContent(props) {{
  const score = props.score || 0;
  const grade = props.grade || '—';
  const acres = props.acres ? props.acres.toFixed(2) : '—';
  const addr  = props.address || props.PARCEL_CITY || '—';
  const pid   = props.PARCEL_ID || '—';
  const vac   = props.vacancy_label || '—';
  const conf  = props.vacancy_confidence ? (props.vacancy_confidence * 100).toFixed(0) + '%' : '—';
  const corner = props.corner ? '✅ Yes' : '❌ No';
  const aadt  = props.aadt ? props.aadt.toLocaleString() : '—';
  const comp  = props.competition_score || '—';
  const compNote = props.competition_note || '';
  const growth = props.growth_signal || '—';
  const stip  = props.stip_nearby ? '✅ STIP project nearby' : '';
  const owner = props.OWNER_NAME || '—';
  const mkt   = props.TOTAL_MKT_VALUE ? '$' + props.TOTAL_MKT_VALUE.toLocaleString() : '—';
  const bldg  = props.BLDG_SQFT ? props.BLDG_SQFT.toLocaleString() + ' sqft' : 'None';
  const yr    = props.BUILT_YR || '—';

  return `
    <div style="font-family: -apple-system, sans-serif; min-width: 280px; font-size: 13px;">
      <div style="background: #1a1a2e; color: #fff; padding: 10px 12px; margin: -10px -10px 10px; border-radius: 4px 4px 0 0;">
        <strong style="font-size: 15px;">${{addr}}</strong><br>
        <span style="opacity: 0.7;">Parcel: ${{pid}}</span>
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px;">
        <div><strong>Score:</strong> ${{score}}/100 (${{grade}})</div>
        <div><strong>Acres:</strong> ${{acres}}</div>
        <div><strong>Vacancy:</strong> ${{vac}}</div>
        <div><strong>Confidence:</strong> ${{conf}}</div>
        <div><strong>Corner:</strong> ${{corner}}</div>
        <div><strong>AADT:</strong> ${{aadt}}</div>
        <div><strong>Competition:</strong> ${{comp}}/100</div>
        <div><strong>Growth Signal:</strong> ${{growth}}/100</div>
        <div><strong>Building:</strong> ${{bldg}}</div>
        <div><strong>Built Year:</strong> ${{yr}}</div>
        <div><strong>Market Value:</strong> ${{mkt}}</div>
        <div><strong>Owner:</strong> ${{owner}}</div>
      </div>
      ${{compNote ? '<div style="margin-top:8px;padding:6px 8px;background:#e8f5e9;border-radius:4px;font-size:12px;">🏁 ' + compNote + '</div>' : ''}}
      ${{stip ? '<div style="margin-top:4px;padding:6px 8px;background:#fff3e0;border-radius:4px;font-size:12px;">🚧 ' + stip + '</div>' : ''}}
    </div>
  `;
}}

// ── Vacancy status sets for filtering ─────────────────────────────────────────
const VACANT_STATUSES     = ['vacant', 'ag_vacant', 'ag_improved'];
const UNDERUTIL_STATUSES  = ['underutilized'];
const DEVELOPED_STATUSES  = ['developed_new', 'developed_old', 'unknown'];

function shouldShow(props) {{
  const vacancyOn     = document.getElementById('vacancyToggle').checked;
  const underutilOn   = document.getElementById('underutilizedToggle').checked;
  const developedOn   = document.getElementById('developedToggle').checked;
  const minScore      = parseInt(document.getElementById('scoreSlider').value);
  const minAcres      = parseFloat(document.getElementById('acresSlider').value);
  const cornerOnly    = document.getElementById('cornerToggle').checked;

  const status = props.vacancy_status || 'unknown';
  const score  = props.score || 0;
  const acres  = props.acres || 0;
  const corner = props.corner || false;

  // Vacancy filter
  let passVacancy = false;
  if (vacancyOn && VACANT_STATUSES.includes(status))    passVacancy = true;
  if (underutilOn && UNDERUTIL_STATUSES.includes(status)) passVacancy = true;
  if (developedOn && DEVELOPED_STATUSES.includes(status)) passVacancy = true;
  // If no category toggles are on, show everything
  if (!vacancyOn && !underutilOn && !developedOn) passVacancy = true;

  if (!passVacancy) return false;
  if (score < minScore) return false;
  if (acres < minAcres) return false;
  if (cornerOnly && !corner) return false;

  return true;
}}

function renderPolygons() {{
  if (polygonLayer) {{
    map.removeLayer(polygonLayer);
  }}

  const opacity = parseInt(document.getElementById('opacitySlider').value) / 100;
  let visibleCount = 0;
  let vacantCount = 0;
  let scoreSum = 0;

  polygonLayer = L.geoJSON(parcelData, {{
    filter: function(feature) {{
      return shouldShow(feature.properties);
    }},
    style: function(feature) {{
      const color = feature.properties.vacancy_color || '#888';
      return {{
        fillColor: color,
        fillOpacity: opacity,
        color: '#ffffff',
        weight: 1.5,
        opacity: 0.7,
      }};
    }},
    onEachFeature: function(feature, layer) {{
      visibleCount++;
      scoreSum += (feature.properties.score || 0);
      if (VACANT_STATUSES.includes(feature.properties.vacancy_status)) {{
        vacantCount++;
      }}
      layer.bindPopup(getPopupContent(feature.properties), {{
        maxWidth: 340, className: 'parcel-popup',
      }});
      // Highlight on hover
      layer.on('mouseover', function() {{
        this.setStyle({{ weight: 3, color: '#00e639', fillOpacity: Math.min(opacity + 0.2, 1) }});
        this.bringToFront();
      }});
      layer.on('mouseout', function() {{
        polygonLayer.resetStyle(this);
      }});
    }},
  }}).addTo(map);

  // Update stats
  document.getElementById('visibleCount').textContent = visibleCount;
  document.getElementById('vacantCount').textContent = vacantCount;
  document.getElementById('avgScore').textContent =
    visibleCount > 0 ? (scoreSum / visibleCount).toFixed(0) : '0';
}}

// ── Wire up controls ──────────────────────────────────────────────────────────
['vacancyToggle', 'underutilizedToggle', 'developedToggle', 'cornerToggle'].forEach(id => {{
  document.getElementById(id).addEventListener('change', renderPolygons);
}});

document.getElementById('opacitySlider').addEventListener('input', function() {{
  document.getElementById('opacityVal').textContent = this.value + '%';
  renderPolygons();
}});

document.getElementById('scoreSlider').addEventListener('input', function() {{
  document.getElementById('scoreVal').textContent = this.value;
  renderPolygons();
}});

document.getElementById('acresSlider').addEventListener('input', function() {{
  document.getElementById('acresVal').textContent = parseFloat(this.value).toFixed(2);
  renderPolygons();
}});

// ── Initial render ────────────────────────────────────────────────────────────
renderPolygons();

// Auto-fit bounds to data
if (parcelData.features.length > 0) {{
  const bounds = polygonLayer.getBounds();
  if (bounds.isValid()) {{
    map.fitBounds(bounds, {{ padding: [30, 30] }});
  }}
}}
</script>
</body>
</html>"""
    return html


# ── Main orchestrator ──────────────────────────────────────────────────────────

def generate_polygon_map(
    scored_parcels: list,
    mode: str = "gas_station",
    output_path: str = None,
    min_score: int = 0,
    max_parcels: int = 500,
):
    """
    Main entry point. Takes scored parcel dicts from parcel_scorer.py,
    fetches polygon geometries from UGRC, classifies vacancy, and
    generates the interactive HTML map.

    Args:
        scored_parcels: List of dicts from parcel_scorer.py, each must have:
            - parcel_id, county, score, grade, lat, lon
            - Plus any scored fields (aadt, is_corner, competition_score, etc.)
        mode: 'gas_station' or 'miniflex'
        output_path: Where to write the HTML (default: data/parcel_polygon_map.html)
        min_score: Only include parcels scoring >= this value
        max_parcels: Safety cap on polygon count (UGRC query volume)
    """
    if output_path is None:
        output_path = f"data/parcel_polygon_map_{mode}.html"

    # Filter by score and cap
    filtered = [p for p in scored_parcels if (p.get("score", 0) >= min_score)]
    filtered.sort(key=lambda p: p.get("score", 0), reverse=True)
    if len(filtered) > max_parcels:
        log.info(f"Capping from {len(filtered)} to {max_parcels} parcels for polygon fetch")
        filtered = filtered[:max_parcels]

    log.info(f"Processing {len(filtered)} parcels for polygon map (mode={mode})")

    # Build lookup by parcel_id
    score_lookup = {}
    for p in filtered:
        pid = p.get("parcel_id")
        if pid:
            score_lookup[pid] = p

    # Group by county and fetch polygons
    by_county = {}
    for p in filtered:
        county = (p.get("county") or "").lower()
        if county not in by_county:
            by_county[county] = []
        pid = p.get("parcel_id")
        if pid:
            by_county[county].append(pid)

    all_geojson = {"type": "FeatureCollection", "features": []}
    for county, pids in by_county.items():
        if not pids:
            continue
        log.info(f"Fetching {len(pids)} polygons from {county.title()} County...")
        county_geojson = fetch_parcel_polygons(pids, county)
        all_geojson["features"].extend(county_geojson.get("features", []))

    # Merge scores + classify vacancy
    all_geojson = merge_and_classify(all_geojson, score_lookup)

    # Count stats
    statuses = {}
    for f in all_geojson["features"]:
        s = f["properties"].get("vacancy_status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    log.info(f"Vacancy breakdown: {json.dumps(statuses, indent=2)}")

    # Generate HTML
    html = _build_polygon_html(all_geojson, mode)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"✅ Polygon map saved: {output_path} ({len(all_geojson['features'])} parcels)")
    return output_path


# ── CLI Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tier 1: Parcel Polygon Map Generator")
    parser.add_argument("--input", required=True, help="Path to scored_parcels.json from pipeline")
    parser.add_argument("--mode", default="gas_station", choices=["gas_station", "miniflex"])
    parser.add_argument("--min-score", type=int, default=40, help="Minimum score threshold")
    parser.add_argument("--max-parcels", type=int, default=500, help="Max polygons to render")
    parser.add_argument("--output", default=None, help="Output HTML path")
    args = parser.parse_args()

    with open(args.input, "r") as f:
        scored = json.load(f)

    generate_polygon_map(
        scored_parcels=scored,
        mode=args.mode,
        output_path=args.output,
        min_score=args.min_score,
        max_parcels=args.max_parcels,
    )
