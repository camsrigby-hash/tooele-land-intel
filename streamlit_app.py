"""Tooele Land Intel — MVP dashboard."""
import subprocess, sys, json
from pathlib import Path
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
CSV = ROOT / "data" / "agenda_items.csv"

st.set_page_config(page_title="Tooele Land Intel", page_icon="◆", layout="wide")
st.markdown("""<style>
  h1,h2,h3 { font-family: Georgia, serif; letter-spacing: -0.01em; }
  .city-header { color:#b04a1f; border-bottom:2px solid #b04a1f; padding-bottom:6px; margin-top:24px; }
  .pill { display:inline-block; padding:2px 8px; border-radius:3px; font-size:11px; font-weight:600; text-transform:uppercase; }
  .pill-rezone { background:#fce4d8; color:#b04a1f; }
  .pill-subdiv { background:#e3e8d4; color:#6e7b56; }
  .pill-annex  { background:#f5e6c4; color:#b08438; }
  .pill-cup    { background:#dde3ec; color:#5b6f8a; }
  .pill-other  { background:#eee; color:#666; }
  .meta { color:#87806f; font-size:12px; }
</style>""", unsafe_allow_html=True)

c1, c2 = st.columns([3,1])
with c1:
    st.markdown("# ◆ Tooele Land Intel")
    st.caption("Wasatch Front + Tooele Valley · Development Intelligence")
with c2:
    if CSV.exists():
        st.metric("Last refresh", pd.Timestamp.fromtimestamp(CSV.stat().st_mtime).strftime("%b %d, %Y"))

mode = st.radio("Mode", ["Territory", "Parcel"], horizontal=True, label_visibility="collapsed")

if mode == "Territory":
    if not CSV.exists():
        st.warning("No agenda data yet. Trigger the agendas-watch workflow from the Actions tab.")
        st.stop()
    df = pd.read_csv(CSV)
    df["meeting_date"] = pd.to_datetime(df["meeting_date"], errors="coerce")
    df = df.sort_values("meeting_date", ascending=False, na_position="last")

    with st.sidebar:
        st.markdown("### Filters")
        cities = sorted(df["jurisdiction"].dropna().unique())
        selected_cities = st.multiselect("City", cities, default=cities)
        types = sorted(df["item_type"].dropna().unique())
        selected_types = st.multiselect("Item type", types, default=types) if types else []
        days_back = st.selectbox("Period", [30,90,180,365,730], index=2, format_func=lambda d: f"Last {d} days")

    cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days_back)
    fdf = df[df["jurisdiction"].isin(selected_cities)
             & (df["item_type"].isin(selected_types) | df["item_type"].isna())
             & ((df["meeting_date"] >= cutoff) | df["meeting_date"].isna())]

    s1,s2,s3,s4 = st.columns(4)
    s1.metric("Items", len(fdf))
    s2.metric("Cities", fdf["jurisdiction"].nunique())
    s3.metric("Rezones", (fdf["item_type"]=="rezone").sum())
    s4.metric("Subdivisions", (fdf["item_type"]=="residential_subdivision").sum())

    for city in selected_cities:
        cdf = fdf[fdf["jurisdiction"]==city]
        if cdf.empty: continue
        st.markdown(f'<h2 class="city-header">{city} · {len(cdf)} items</h2>', unsafe_allow_html=True)
        for _, row in cdf.head(15).iterrows():
            ptype = (row.get("item_type") or "other").lower()
            cls = {"rezone":"pill-rezone","residential_subdivision":"pill-subdiv",
                   "annexation":"pill-annex","conditional_use":"pill-cup"}.get(ptype, "pill-other")
            mdate = row["meeting_date"].strftime("%b %d, %Y") if pd.notna(row["meeting_date"]) else "—"
            body = row.get("body") or ""
            st.markdown(f"""<div style="border-bottom:1px solid #e6dfce; padding:10px 0;">
                <span class="pill {cls}">{ptype.replace('_',' ')}</span>
                <strong style="margin-left:8px;">{row['title']}</strong>
                <div class="meta">{mdate} · {body} · <a href="{row['url']}" target="_blank">source</a></div>
                </div>""", unsafe_allow_html=True)
        if len(cdf) > 15:
            with st.expander(f"Show {len(cdf)-15} more from {city}"):
                st.dataframe(cdf.tail(len(cdf)-15)[["meeting_date","title","item_type","url"]],
                             use_container_width=True, hide_index=True)
else:
    st.markdown("### Parcel Opportunity Analysis")
    parcel_id = st.text_input("Tooele County parcel ID", value="01-440-0-0019")
    if st.button("Run Analysis", type="primary"):
        with st.spinner(f"Analyzing {parcel_id}..."):
            try:
                r = subprocess.run([sys.executable, "scripts/analyze_opportunity.py", parcel_id, "--pretty"],
                                   capture_output=True, text=True, timeout=60, cwd=ROOT)
                if r.returncode == 0:
                    st.success(f"Analysis complete for {parcel_id}")
                    st.json(json.loads(r.stdout))
                else:
                    st.error(f"Failed:\n{r.stderr[:1000]}")
            except Exception as e:
                st.error(f"Error: {e}")

st.markdown("---")
st.caption("Tooele Land Intel · Build 0.5 · GitHub Actions + Streamlit Cloud")
