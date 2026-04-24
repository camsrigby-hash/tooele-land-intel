"""
Growth Signal Engine — Config
Public body IDs sourced from utah.gov/pmn
Each entry is a planning commission or city council body we want to monitor.
"""

# Format: { "body_id": { "city": ..., "county": ..., "body_type": ... } }
PMN_BODIES = {
    # ── WEST POINT ────────────────────────────────────────────
    "370":  {"city": "West Point",   "county": "Weber", "body_type": "Planning Commission"},
    "369":  {"city": "West Point",   "county": "Weber", "body_type": "City Council"},

    # ── DAVIS COUNTY (unincorporated) ─────────────────────────
    "1340": {"city": "Davis County", "county": "Davis", "body_type": "Planning Commission"},
    "1335": {"city": "Davis County", "county": "Davis", "body_type": "Commission"},

    # ── DAVIS COUNTY CITIES ───────────────────────────────────
    # Bountiful
    "38":   {"city": "Bountiful",    "county": "Davis", "body_type": "Planning Commission"},
    "37":   {"city": "Bountiful",    "county": "Davis", "body_type": "City Council"},
    # Clearfield
    "68":   {"city": "Clearfield",   "county": "Davis", "body_type": "Planning Commission"},
    "67":   {"city": "Clearfield",   "county": "Davis", "body_type": "City Council"},
    # Clinton
    "72":   {"city": "Clinton",      "county": "Davis", "body_type": "Planning Commission"},
    "71":   {"city": "Clinton",      "county": "Davis", "body_type": "City Council"},
    # Farmington
    "116":  {"city": "Farmington",   "county": "Davis", "body_type": "Planning Commission"},
    "115":  {"city": "Farmington",   "county": "Davis", "body_type": "City Council"},
    # Kaysville
    "195":  {"city": "Kaysville",    "county": "Davis", "body_type": "Planning Commission"},
    "194":  {"city": "Kaysville",    "county": "Davis", "body_type": "City Council"},
    # Layton
    "217":  {"city": "Layton",       "county": "Davis", "body_type": "Planning Commission"},
    "216":  {"city": "Layton",       "county": "Davis", "body_type": "City Council"},
    # North Salt Lake
    "282":  {"city": "North Salt Lake","county": "Davis","body_type": "Planning Commission"},
    "281":  {"city": "North Salt Lake","county": "Davis","body_type": "City Council"},
    # South Weber
    "356":  {"city": "South Weber",  "county": "Davis", "body_type": "Planning Commission"},
    "355":  {"city": "South Weber",  "county": "Davis", "body_type": "City Council"},
    # Syracuse
    "374":  {"city": "Syracuse",     "county": "Davis", "body_type": "Planning Commission"},
    "373":  {"city": "Syracuse",     "county": "Davis", "body_type": "City Council"},
    # West Bountiful
    "403":  {"city": "West Bountiful","county": "Davis","body_type": "Planning Commission"},
    "402":  {"city": "West Bountiful","county": "Davis","body_type": "City Council"},
    # West Haven (Weber County — your current project)
    "406":  {"city": "West Haven",   "county": "Weber", "body_type": "Planning Commission"},
    "405":  {"city": "West Haven",   "county": "Weber", "body_type": "City Council"},
    # Woods Cross
    "419":  {"city": "Woods Cross",  "county": "Davis", "body_type": "Planning Commission"},
    "418":  {"city": "Woods Cross",  "county": "Davis", "body_type": "City Council"},

    # ── WEBER COUNTY (unincorporated) ─────────────────────────
    "1711": {"city": "Weber County", "county": "Weber", "body_type": "Planning Commission"},

    # ── WEBER COUNTY CITIES ───────────────────────────────────
    # Harrisville
    "151":  {"city": "Harrisville",  "county": "Weber", "body_type": "Planning Commission"},
    # Hooper
    "161":  {"city": "Hooper",       "county": "Weber", "body_type": "Planning Commission"},
    # Ogden
    "289":  {"city": "Ogden",        "county": "Weber", "body_type": "Planning Commission"},
    "288":  {"city": "Ogden",        "county": "Weber", "body_type": "City Council"},
    # Plain City
    "307":  {"city": "Plain City",   "county": "Weber", "body_type": "Planning Commission"},
    # Pleasant View
    "309":  {"city": "Pleasant View","county": "Weber", "body_type": "Planning Commission"},
    # Riverdale
    "323":  {"city": "Riverdale",    "county": "Weber", "body_type": "Planning Commission"},
    # Roy
    "327":  {"city": "Roy",          "county": "Weber", "body_type": "Planning Commission"},
    "326":  {"city": "Roy",          "county": "Weber", "body_type": "City Council"},
    # Sunset
    "372":  {"city": "Sunset",       "county": "Weber", "body_type": "Planning Commission"},
    # Washington Terrace
    "399":  {"city": "Washington Terrace","county": "Weber","body_type": "Planning Commission"},
}

# How many months back to look for notices
LOOKBACK_MONTHS = 6

# Min score to include a signal in output (0-100)
MIN_SIGNAL_SCORE = 30

# Output paths
PDF_DIR   = "data/pdfs"
JSON_DIR  = "data/json"
LOG_DIR   = "logs"
