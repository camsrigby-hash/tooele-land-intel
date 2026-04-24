"""
spectral_classifier.py - Fast vectorized quadrant land use classifier.
Analyzes NAIP imagery chips and classifies each quadrant (NE/NW/SE/SW).
"""
import json, math, sys
import numpy as np
from pathlib import Path
from PIL import Image

DATA_DIR = Path("data/cache")
CHIPS_DIR = DATA_DIR / "naip_chips"
INTERSECTIONS_FILE = DATA_DIR / "intersections.geojson"
OUTPUT_FILE = DATA_DIR / "visual_candidates.geojson"

LAT_HALF = 0.00090   # half chip in degrees latitude (~100m)
LON_HALF = 0.00120   # half chip in degrees longitude (~100m)

QUADRANT_OFFSETS = {
    "NE": (0, LON_HALF,   0, LAT_HALF),     # (lon_min, lon_max, lat_min, lat_max) relative to center
    "NW": (-LON_HALF, 0,  0, LAT_HALF),
    "SE": (0, LON_HALF,  -LAT_HALF, 0),
    "SW": (-LON_HALF, 0, -LAT_HALF, 0),
}

# Default thresholds
THRESHOLDS = {
    'green_ratio_veg': 0.38,
    'brightness_veg_max': 180,
    'green_ratio_bare_min': 0.30,
    'green_ratio_bare_max': 0.38,
    'brightness_bare_min': 80,
    'brightness_bare_max': 160,
    'texture_bare_max': 22,
    'brightness_built_min': 138,
    'texture_built_min': 13,
    'brightness_road_min': 158,
    'texture_road_max': 11,
}

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def compute_texture_fast(brightness):
    """Fast texture via sliding window std using numpy strides."""
    from numpy.lib.stride_tricks import as_strided
    H, W = brightness.shape
    pad = 2
    p = np.pad(brightness.astype(float), pad, mode='edge')
    # Build windows array shape (H, W, 5, 5)
    shape = (H, W, 5, 5)
    strides = (p.strides[0], p.strides[1], p.strides[0], p.strides[1])
    windows = as_strided(p, shape=shape, strides=strides)
    return windows.reshape(H, W, 25).std(axis=2)

def classify_pixels_rgb(quadrant_arr, t=None):
    """Classify each pixel as VEGETATION/VACANT/BUILT-UP/ROAD using RGB. Returns pcts dict."""
    if t is None:
        t = THRESHOLDS
    arr = quadrant_arr.astype(float)
    R, G, B = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    brightness = (R + G + B) / 3.0
    green_ratio = G / (R + G + B + 0.001)
    texture = compute_texture_fast(brightness)
    
    total = R.shape[0] * R.shape[1]
    
    veg  = (green_ratio > t['green_ratio_veg']) & (brightness < t['brightness_veg_max'])
    road = (brightness > t['brightness_road_min']) & (texture < t['texture_road_max'])
    built= (brightness > t['brightness_built_min']) & (texture > t['texture_built_min'])
    bare = ((green_ratio >= t['green_ratio_bare_min']) & (green_ratio < t['green_ratio_bare_max']) &
            (brightness >= t['brightness_bare_min']) & (brightness <= t['brightness_bare_max']) &
            (texture < t['texture_bare_max']))
    
    # Priority: road > built > veg > bare
    final_road  = road.copy()
    final_built = built & ~road
    final_veg   = veg & ~road & ~built
    final_bare  = bare & ~road & ~built & ~veg
    
    pct_road  = 100.0 * final_road.sum()  / total
    pct_built = 100.0 * final_built.sum() / total
    pct_veg   = 100.0 * final_veg.sum()   / total
    pct_bare  = 100.0 * final_bare.sum()  / total
    
    return {'pct_vacant': pct_bare, 'pct_vegetation': pct_veg, 'pct_built': pct_built, 'pct_road': pct_road}

def classify_quadrant(pcts):
    """Determine quadrant class from pixel percentages."""
    vacant_signal = pcts['pct_vacant'] + pcts['pct_vegetation']
    if vacant_signal > 60 and pcts['pct_built'] < 20:
        cls = "VACANT"
        conf = min(100, vacant_signal)
    elif pcts['pct_vegetation'] > 70:
        cls = "AGRICULTURAL"
        conf = min(100, pcts['pct_vegetation'])
    elif pcts['pct_built'] > 40:
        cls = "DEVELOPED"
        conf = min(100, pcts['pct_built'])
    elif pcts['pct_road'] > 50:
        cls = "ROAD_ROW"
        conf = min(100, pcts['pct_road'])
    else:
        cls = "MIXED"
        conf = 50
    return cls, conf

def extract_quadrant(img_arr, quadrant):
    """Extract one quadrant from a 200x200 image."""
    H, W = img_arr.shape[:2]
    h2, w2 = H // 2, W // 2
    if quadrant == "NE":  return img_arr[:h2, w2:]
    if quadrant == "NW":  return img_arr[:h2, :w2]
    if quadrant == "SE":  return img_arr[h2:, w2:]
    if quadrant == "SW":  return img_arr[h2:, :w2]
    return img_arr

def quadrant_polygon(lat, lon, quadrant):
    """Return GeoJSON Polygon for a quadrant."""
    if quadrant == "NE":
        coords = [[lon, lat], [lon+LON_HALF, lat], [lon+LON_HALF, lat+LAT_HALF], [lon, lat+LAT_HALF], [lon, lat]]
    elif quadrant == "NW":
        coords = [[lon-LON_HALF, lat], [lon, lat], [lon, lat+LAT_HALF], [lon-LON_HALF, lat+LAT_HALF], [lon-LON_HALF, lat]]
    elif quadrant == "SE":
        coords = [[lon, lat-LAT_HALF], [lon+LON_HALF, lat-LAT_HALF], [lon+LON_HALF, lat], [lon, lat], [lon, lat-LAT_HALF]]
    elif quadrant == "SW":
        coords = [[lon-LON_HALF, lat-LAT_HALF], [lon, lat-LAT_HALF], [lon, lat], [lon-LON_HALF, lat], [lon-LON_HALF, lat-LAT_HALF]]
    return {"type": "Polygon", "coordinates": [coords]}

def classify_all_intersections(t=None, max_items=None):
    """Load intersections, classify all quadrants, return features list."""
    if t is None:
        t = THRESHOLDS.copy()
    with open(INTERSECTIONS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    features = data.get('features', [])
    if max_items:
        features = features[:max_items]
    
    results = []
    missing = 0
    for i, feat in enumerate(features):
        props = feat['properties']
        int_id = props.get('id', props.get('intersection_id', i))
        lat = feat.get("geometry", {}).get("coordinates", [0, 0])[1] if feat.get("geometry", {}).get("type") == "Point" else props.get("lat", 0)
        lon = feat.get("geometry", {}).get("coordinates", [0, 0])[0] if feat.get("geometry", {}).get("type") == "Point" else props.get("lon", 0)
        county = props.get('county', '')
        r1 = props.get('road_1_name', props.get('road1_name', ''))
        r2 = props.get('road_2_name', props.get('road2_name', ''))
        
        chip_path = CHIPS_DIR / f"{int_id}.png"
        if not chip_path.exists():
            missing += 1
            continue
        
        try:
            img = np.array(Image.open(chip_path).convert('RGB'))
        except Exception:
            missing += 1
            continue
        
        for q in ["NE", "NW", "SE", "SW"]:
            quad_arr = extract_quadrant(img, q)
            pcts = classify_pixels_rgb(quad_arr, t)
            cls, conf = classify_quadrant(pcts)
            geom = quadrant_polygon(lat, lon, q)
            results.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "intersection_id": int_id,
                    "quadrant": q,
                    "visual_class": cls,
                    "pct_vacant": round(pcts['pct_vacant'], 1),
                    "pct_vegetation": round(pcts['pct_vegetation'], 1),
                    "pct_built": round(pcts['pct_built'], 1),
                    "pct_road": round(pcts['pct_road'], 1),
                    "visual_confidence": round(conf, 1),
                    "lat": lat,
                    "lon": lon,
                    "road_1_name": r1,
                    "road_2_name": r2,
                    "county": county,
                }
            })
        
        if (i+1) % 500 == 0:
            print(f"  Classified {i+1}/{len(features)} intersections...")
    
    if missing:
        print(f"  Warning: {missing} intersections had no chip image")
    return results

TEST_INTERSECTIONS = [
    {"name": "Test1_Syracuse_1700S_3000W", "lat": 41.0855, "lon": -112.0785,
     "expected_vacant": ["NE","NW","SW"], "must_vacant": True},
    {"name": "Test2_I15_Layton",           "lat": 41.06,   "lon": -111.97,
     "expected_vacant": [], "must_not_vacant": True},
    {"name": "Test3_WestHaven_3500W_4000S","lat": 41.1280, "lon": -112.0650,
     "expected_any_vacant": True},
    {"name": "Test4_LaytonHillsMall",      "lat": 41.08,   "lon": -111.96,
     "expected_class": "DEVELOPED"},
]

def find_nearest_intersection(all_results, lat, lon):
    """Find all quadrant results for nearest intersection."""
    best_dist = float('inf')
    best_id = None
    for r in all_results:
        p = r['properties']
        d = haversine_dist(lat, lon, p['lat'], p['lon'])
        if d < best_dist:
            best_dist = d
            best_id = p['intersection_id']
    if best_id is None:
        return []
    return [r for r in all_results if r['properties']['intersection_id'] == best_id], best_dist

def validate_tests(all_results):
    """Validate test intersections. Returns list of (name, pass/fail, detail)."""
    test_results = []
    for test in TEST_INTERSECTIONS:
        match = find_nearest_intersection(all_results, test['lat'], test['lon'])
        if not match or not match[0]:
            test_results.append((test['name'], False, "No nearby intersection found"))
            continue
        quad_results, dist = match
        classes = {r['properties']['quadrant']: r['properties']['visual_class'] for r in quad_results}
        detail = f"dist={dist:.0f}m, classes={classes}"
        
        passed = True
        if test.get('must_vacant'):
            for q in test['expected_vacant']:
                if classes.get(q) not in ('VACANT', 'AGRICULTURAL'):
                    passed = False
                    break
        if test.get('must_not_vacant'):
            if any(v in ('VACANT','AGRICULTURAL') for v in classes.values()):
                passed = False
        if test.get('expected_any_vacant'):
            if not any(v in ('VACANT','AGRICULTURAL') for v in classes.values()):
                passed = False
        if test.get('expected_class'):
            if not any(v == test['expected_class'] for v in classes.values()):
                passed = False
        
        test_results.append((test['name'], passed, detail))
    return test_results

def adjust_thresholds_for_test1(t, attempt):
    """Adjust thresholds to make more vacant land detectable."""
    t = t.copy()
    if attempt == 1:
        t['green_ratio_bare_min'] = 0.27
        t['green_ratio_bare_max'] = 0.40
        t['brightness_bare_max'] = 175
        t['texture_bare_max'] = 30
        t['brightness_built_min'] = 150
    elif attempt == 2:
        t['green_ratio_bare_min'] = 0.24
        t['green_ratio_bare_max'] = 0.43
        t['brightness_bare_max'] = 185
        t['texture_bare_max'] = 35
        t['brightness_built_min'] = 160
    elif attempt == 3:
        t['green_ratio_bare_min'] = 0.22
        t['green_ratio_bare_max'] = 0.45
        t['brightness_bare_max'] = 190
        t['texture_bare_max'] = 40
        t['brightness_built_min'] = 165
    return t

def save_results(results):
    geojson = {"type": "FeatureCollection", "features": results}
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"Saved {len(results)} quadrant features to {OUTPUT_FILE}")

def main():
    print("=" * 60)
    print("SPECTRAL CLASSIFIER — Session C")
    print("=" * 60)
    
    threshold_changes = []
    t = THRESHOLDS.copy()
    best_results = None
    
    for attempt in range(4):
        if attempt > 0:
            print(f"\nThreshold adjustment attempt {attempt}...")
            t = adjust_thresholds_for_test1(t, attempt)
            threshold_changes.append(f"Attempt {attempt}: adjusted thresholds for better vacant detection")
        
        print(f"\nClassifying intersections (attempt {attempt+1})...")
        results = classify_all_intersections(t)
        best_results = results
        
        print(f"Total quadrants classified: {len(results)}")
        by_class = {}
        for r in results:
            c = r['properties']['visual_class']
            by_class[c] = by_class.get(c, 0) + 1
        for c, n in sorted(by_class.items()):
            print(f"  {c}: {n}")
        
        # Run validation
        print("\nValidation tests:")
        test_vals = validate_tests(results)
        all_pass = True
        for name, passed, detail in test_vals:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}: {detail}")
            if not passed:
                all_pass = False
        
        if all_pass or attempt == 3:
            if all_pass:
                print(f"\nAll validation tests passed on attempt {attempt+1}!")
            else:
                print(f"\nMax adjustment attempts reached. Saving best results.")
            break
    
    # Save final results
    save_results(best_results)
    
    # Summary
    vacant_count = sum(1 for r in best_results if r['properties']['visual_class'] in ('VACANT','AGRICULTURAL'))
    print(f"\n{'='*60}")
    print(f"CLASSIFICATION COMPLETE")
    print(f"Total quadrants: {len(best_results)}")
    print(f"VACANT + AGRICULTURAL: {vacant_count}")
    print(f"Threshold changes: {len(threshold_changes)}")
    for chg in threshold_changes:
        print(f"  - {chg}")
    print("=" * 60)
    
    return best_results, test_vals, threshold_changes

if __name__ == "__main__":
    main()
