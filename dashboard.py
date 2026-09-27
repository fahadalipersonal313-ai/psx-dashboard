"""Lightweight Streamlit view for the PSX engine runtime."""
import time, hmac, requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="PSX Shariah Engine", page_icon="📈", layout="wide")
BASE="https://raw.githubusercontent.com/fahadalipersonal313-ai/psx-engine/runtime-state/"

def get_json(name, timeout=5):
    r=requests.get(BASE+name, timeout=timeout, headers={"Cache-Control":"no-cache"})
    r.raise_for_status()
    return r.json()

def auth():
    try: pw=st.secrets.get("DASHBOARD_PASSWORD")
    except Exception: pw=None
    if not pw: return
    if st.session_state.get("auth_until",0)>time.time(): return
    st.title("🔒 PSX Shariah Engine")
    entered=st.text_input("Dashboard password",type="password")
    if entered and hmac.compare_digest(str(entered),str(pw)):
        st.session_state["auth_until"]=time.time()+3600
        st.rerun()
    if entered: st.error("Incorrect password.")
    st.stop()

@st.cache_data(ttl=60,show_spinner=False)
def snapshot(): return get_json("dashboard_snapshot.json")

@st.cache_data(ttl=120,show_spinner=False)
def news():
    try: return get_json("news_raw_24h.json")
    except Exception: return {}

auth()
st.title("PSX Shariah Engine")
st.caption("15 minute engine runtime view")

try: snap=snapshot()
except Exception as exc:
    st.error("Engine runtime snapshot is temporarily unavailable.")
    st.caption(str(exc))
    st.stop()

rows=snap.get("rows") or []
generated=snap.get("generated_at") or "unknown"
a,b=st.columns(2)
a.metric("Tracked stocks",len(rows))
b.metric("Snapshot generated",generated.replace("T"," ").replace("+00:00"," UTC"))

if not rows:
    st.warning("No current stock rows are available.")
    st.stop()

df=pd.DataFrame(rows)
preferred=["symbol","price","signal","final_score","technical_score","risk_level","run_time","main_reason","main_risk"]
cols=[c for c in preferred if c in df.columns]
if "final_score" in df.columns: df=df.sort_values("final_score",ascending=False)
st.subheader("Current signals")
st.dataframe(df[cols] if cols else df,use_container_width=True,hide_index=True)

payload=news(); items=payload.get("items") or []
st.subheader("Latest news")
st.caption(f"{len(items)} items in the current engine news window")
for it in items[:25]:
    title=it.get("title") or "Untitled"; url=it.get("url"); pub=it.get("source") or it.get("publisher") or ""; ts=it.get("published") or ""
    if url: st.markdown(f"**[{title}]({url})**  \n{pub} · {ts}")
    else: st.markdown(f"**{title}**  \n{pub} · {ts}")

st.caption("Signals are displayed exactly as published by psx-engine runtime-state.")
