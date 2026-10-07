"""Fetch runtime state without putting large mutable files in this Git history."""
import os, time
from pathlib import Path
import requests

BASE="https://raw.githubusercontent.com/fahadalipersonal313-ai/psx-engine/runtime-state/"
RUNTIME=Path("/tmp/psx-dashboard-runtime")
DB=RUNTIME/"psx_engine.db"
# Only what the dashboard reads (2026-10-07: intraday and short-horizon files
# retired with the research layer). Ratings are also read from main when newer.
FILES=["dashboard_snapshot.json","news_raw_24h.json","news_ai_ratings.json",
       "news_codex_ratings.json","news_signals.json"]

def _download(name,dest,timeout=45):
    r=requests.get(BASE+name,stream=True,timeout=timeout,headers={"Cache-Control":"no-cache"})
    r.raise_for_status()
    tmp=dest.with_suffix(dest.suffix+".tmp")
    with open(tmp,"wb") as f:
        for chunk in r.iter_content(1024*1024):
            if chunk: f.write(chunk)
    tmp.replace(dest)

def prepare():
    RUNTIME.mkdir(parents=True,exist_ok=True)
    # DB is authoritative for original history/drilldown tabs. Refresh at most
    # every five minutes; normal Streamlit reruns reuse the local copy.
    if not DB.exists() or time.time()-DB.stat().st_mtime>300:
        try: _download("psx_engine.db",DB,90)
        except Exception:
            if not DB.exists(): raise
    os.environ["PSX_DB_PATH"]=str(DB)
    # Small runtime JSONs are cheap and may change every 15 minutes.
    for name in FILES:
        dest=Path(__file__).parent/name
        try: _download(name,dest,8)
        except Exception: pass
