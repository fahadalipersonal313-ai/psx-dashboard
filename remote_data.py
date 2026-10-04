"""Live data for a dashboard deployed from a frozen code branch.

The dashboard deploys from dashboard-stable, which is deliberately NOT pushed
on every engine commit (each push restarts the app). Its bundled news files and
database therefore froze at the last code deploy: on 2026-10-02 it still
showed 2026-09-27 news after a reboot. Data is fetched from the branches that
are actually updated -- main (news ratings, hourly) and runtime-state (engine
database, every cycle) -- and the bundled copy is only the fallback.
"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing

import config

log = logging.getLogger("remote_data")

RAW = "https://raw.githubusercontent.com/fahadalipersonal313-ai/psx-engine/{branch}/{name}"
_cache = {}
_cache_nonce = 0


def fetch_json(name, branch="main", ttl=300, get=None, timeout=4):
    """Parsed JSON file from `branch`, cached for `ttl` seconds; None on failure."""
    key = (branch, name)
    now = time.time()
    hit = _cache.get(key)
    if hit and 0 <= now - hit[0] < ttl:
        return hit[1]
    try:
        import requests
        # Raw branch URLs can remain cached by intermediaries despite no-cache.
        # One URL per bounded refresh window retains normal caching while
        # preventing an older generation from sticking indefinitely.
        bucket = int(now // max(60, ttl))
        url = RAW.format(branch=branch, name=name) + f"?psx_refresh={bucket}-{_cache_nonce}"
        r = (get or requests.get)(url, timeout=timeout,
                                  headers={"Cache-Control": "no-cache"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("remote %s/%s unavailable: %s", branch, name, exc)
        data = None
    _cache[key] = (time.time(), data)
    return data


def clear_json_cache():
    """Explicit user refresh; does not alter source timestamps or runtime data."""
    global _cache_nonce
    _cache.clear()
    _cache_nonce = time.time_ns()


def _valid_db(path):
    try:
        with closing(sqlite3.connect(path)) as c:
            ok = c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            runs = c.execute("SELECT MAX(run_time) FROM runs").fetchone()[0]
        return ok and runs is not None
    except sqlite3.Error:
        return False


def _latest_run(path):
    try:
        with closing(sqlite3.connect(path)) as c:
            return c.execute("SELECT MAX(run_time) FROM runs").fetchone()[0] or ""
    except sqlite3.Error:
        return ""


def refresh_db(branches=("runtime-state", "main"), get=None, timeout=60, dest_dir=None):
    """Download the freshest valid engine database and point config.DB_PATH at it.

    Tries each branch, keeps the copy with the newest run, and only switches
    when it passes an integrity check and is newer than what is in use. Returns
    (path, branch) or (None, reason). The bundled database stays the fallback.
    """
    import requests
    dest_dir = dest_dir or tempfile.gettempdir()
    best = (None, None, _latest_run(config.DB_PATH))
    for branch in branches:
        tmp = os.path.join(dest_dir, f"psx_engine.{branch}.download")
        try:
            with (get or requests.get)(RAW.format(branch=branch, name="psx_engine.db"),
                                       timeout=timeout, stream=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
        except Exception as exc:
            log.warning("database from %s unavailable: %s", branch, exc)
            continue
        if not _valid_db(tmp):
            log.warning("database from %s failed its integrity check", branch)
            continue
        latest = _latest_run(tmp)
        if latest > best[2]:
            final = os.path.join(dest_dir, f"psx_engine.{branch}.db")
            shutil.move(tmp, final)
            best = (final, branch, latest)
    if best[0] is None:
        return None, "bundled database kept (no newer valid download)"
    config.DB_PATH = best[0]
    return best[0], best[1]


def newer(a, b, stamp_key="as_of"):
    """The JSON payload with the later timestamp; either may be None."""
    if not a:
        return b
    if not b:
        return a
    return a if str(a.get(stamp_key) or "") >= str(b.get(stamp_key) or "") else b
