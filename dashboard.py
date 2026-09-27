"""Bootstrap the exact psx-engine Streamlit frontend without cloning its Git history."""
import os, sys, time, zipfile, tempfile, shutil
from pathlib import Path
import requests
import streamlit as st

ENGINE_ZIP="https://github.com/fahadalipersonal313-ai/psx-engine/archive/refs/heads/main.zip"
RUNTIME_DB="https://raw.githubusercontent.com/fahadalipersonal313-ai/psx-engine/runtime-state/psx_engine.db"
ROOT=Path("/tmp/psx_engine_frontend")
STAMP=ROOT/".ready"

def prepare():
    # Current-tree ZIP contains no 415 MB Git history. Refresh code at most once
    # per app process; Streamlit reruns reuse the extracted tree.
    if not STAMP.exists():
        shutil.rmtree(ROOT,ignore_errors=True)
        ROOT.mkdir(parents=True,exist_ok=True)
        zpath=ROOT/"engine.zip"
        with requests.get(ENGINE_ZIP,stream=True,timeout=60) as r:
            r.raise_for_status()
            with open(zpath,"wb") as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk: f.write(chunk)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(ROOT)
        zpath.unlink()
        src=next(ROOT.glob("psx-engine-*"))
        # Use runtime-state DB when available; main's compact DB remains fallback.
        try:
            with requests.get(RUNTIME_DB,stream=True,timeout=60) as r:
                r.raise_for_status()
                tmp=src/"psx_engine.db.tmp"
                with open(tmp,"wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk: f.write(chunk)
                os.replace(tmp,src/"psx_engine.db")
        except Exception:
            pass
        STAMP.write_text(str(time.time()))
    return next(ROOT.glob("psx-engine-*"))

src=prepare()
os.chdir(src)
sys.path.insert(0,str(src))
code=(src/"dashboard.py").read_text(encoding="utf-8")
exec(compile(code,str(src/"dashboard.py"),"exec"),{"__name__":"__main__","__file__":str(src/"dashboard.py")})
