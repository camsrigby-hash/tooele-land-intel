#!/usr/bin/env python3
"""Merge scraper JSON outputs into a single deduplicated CSV."""
import csv, json, re, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
AGENDA_DIR = ROOT / "data" / "agendas"
CSV_PATH = ROOT / "data" / "agenda_items.csv"
DATE_IN_URL = re.compile(r'(\d{1,2})[.\-](\d{1,2})[.\-](20\d{2})')
CSV_FIELDS = ["id","jurisdiction","body","meeting_date","title","item_type","confidence","url","agenda_text","source","scraped_at"]

def extract_meeting_date(url, text=""):
    if url:
        m = DATE_IN_URL.search(url)
        if m:
            mm,dd,yy = m.groups()
            try: return datetime(int(yy),int(mm),int(dd)).date().isoformat()
            except ValueError: pass
    if text:
        m = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})', text)
        if m:
            try: return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
            except ValueError: pass
    return ""

def normalize_pmn(notice, body, juris):
    md = notice.get("event_date_iso","")[:10] if notice.get("event_date_iso") else ""
    return {"id":f"pmn_{notice.get('notice_id','')}","jurisdiction":juris,"body":body,
            "meeting_date":md,"title":notice.get("title",""),"item_type":"","confidence":"",
            "url":notice.get("notice_url",""),"agenda_text":(notice.get("agenda_text") or "")[:2000],
            "source":"pmn","scraped_at":datetime.utcnow().isoformat()+"Z"}

def normalize_web(item):
    pdf = item.get("pdf_url","")
    text = item.get("pdf_text_excerpt","") or ""
    cls = item.get("classification",{}) or {}
    return {"id":f"web_{abs(hash(pdf))}","jurisdiction":item.get("jurisdiction",""),"body":"",
            "meeting_date":extract_meeting_date(pdf,text),"title":item.get("link_text",""),
            "item_type":cls.get("type",""),"confidence":cls.get("confidence",""),
            "url":pdf,"agenda_text":text[:2000],"source":"web",
            "scraped_at":item.get("scraped_at",datetime.utcnow().isoformat()+"Z")}

def collect():
    rows = []
    if not AGENDA_DIR.exists(): return rows
    for path in sorted(AGENDA_DIR.glob("*.json")):
        try: data = json.load(open(path))
        except Exception as e:
            print(f"  [WARN] {path.name}: {e}", file=sys.stderr); continue
        if isinstance(data,dict) and "notices" in data:
            for n in data["notices"]:
                rows.append(normalize_pmn(n, data.get("public_body",""), data.get("jurisdiction","")))
        elif isinstance(data,list):
            for item in data:
                if item.get("type") == "agenda_pdf":
                    rows.append(normalize_web(item))
    return rows

def dedupe(rows):
    seen = {}
    for r in rows:
        key = (r["jurisdiction"], r["url"], r["meeting_date"])
        if key not in seen or r["scraped_at"] > seen[key]["scraped_at"]:
            seen[key] = r
    return list(seen.values())

def main():
    rows = dedupe(collect())
    rows.sort(key=lambda r:(r["jurisdiction"], r["meeting_date"] or "0000", r["title"]))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"✓ Wrote {len(rows)} rows to {CSV_PATH}")

if __name__ == "__main__": main()
