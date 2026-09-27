"""fundamentals_analyzer.py — Scores a stock's audited fundamentals to 0-100.

Reads ratios from config.FUNDAMENTALS (a manually maintained table — same honest
pattern as MACRO_ANCHORS). The engine NEVER invents fundamentals: any symbol with
no entry scores a neutral 50 and is flagged low_confidence so the weight-aware
confidence penalty kicks in. Fill the table from the latest quarterly/annual
report to activate this layer.

Per-symbol dict keys (all optional): pe, eps_growth (%), roe (%),
de (debt/equity ratio), div_yield (%).
"""

import json
import os
from collections import defaultdict

import config

log_neutral = 50.0
_METRICS = ("pe", "eps_growth", "roe", "de", "div_yield")
_MIN_PEERS = 3            # sector-relative needs at least this many peers w/ a metric

# Auto-fetched ratios cache (written by fundamentals_fetcher.py). Manual entries
# in config.FUNDAMENTALS override anything here.
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "fundamentals.json")


def _load_cache():
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"data": {}, "as_of": None}


_CACHE = _load_cache()


_SECTOR_VALUES = None


def _sector_peer_values():
    """{sector: {metric: [peer values]}} from the merged cache+config, memoized.
    Lets each ratio be scored relative to its sector, not just absolute bands."""
    global _SECTOR_VALUES
    if _SECTOR_VALUES is not None:
        return _SECTOR_VALUES
    cache = _CACHE.get("data", {})
    cfg = getattr(config, "FUNDAMENTALS", {}) or {}
    sv = defaultdict(lambda: defaultdict(list))
    for sym in set(cache) | set(cfg):
        d = dict(cache.get(sym, {})); d.update(cfg.get(sym, {}))
        sec = config.SECTORS.get(sym, "?")
        for m in _METRICS:
            v = d.get(m)
            if v is not None:
                sv[sec][m].append(float(v))
    _SECTOR_VALUES = {s: dict(mv) for s, mv in sv.items()}
    return _SECTOR_VALUES


def _rel_pct(values, x, lower_is_better):
    """Score x's standing among its sector peers, 20-100 (best=100). None if too
    few peers. Percentile-rank based, so it's robust to outliers/small n."""
    n = len(values)
    if n < _MIN_PEERS:
        return None
    beats = (sum(1 for v in values if v > x) if lower_is_better
             else sum(1 for v in values if v < x))      # peers x is better than
    frac = beats / (n - 1) if n > 1 else 0.5            # exclude self
    return 20 + max(0.0, min(1.0, frac)) * 80


def _lin(x, lo, hi, lo_score, hi_score):
    """Linear map x in [lo,hi] -> [lo_score,hi_score], clamped."""
    if x is None:
        return None
    if hi == lo:
        return hi_score
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return lo_score + t * (hi_score - lo_score)


def analyze(symbol):
    # Merge auto-fetched cache with manual config overrides (config wins).
    data = dict(_CACHE.get("data", {}).get(symbol, {}))
    data.update((getattr(config, "FUNDAMENTALS", {}) or {}).get(symbol, {}))
    as_of = (getattr(config, "FUNDAMENTALS_AS_OF", "") or _CACHE.get("as_of")
             or "n/a")
    if not data:
        return {"symbol": symbol, "score": log_neutral, "low_confidence": True,
                "as_of": as_of, "have": [],
                "notes": ["No fundamentals data — neutral 50. Run "
                          "`python fundamentals_fetcher.py` or add "
                          f"config.FUNDAMENTALS['{symbol}']."]}

    # Each ratio: absolute band score, blended 50/50 with a sector-relative
    # percentile when the sector has >= _MIN_PEERS peers for that ratio. So
    # "cheap P/E" is judged vs sector norms, not one universal band.
    sv = _sector_peer_values().get(config.SECTORS.get(symbol, "?"), {})
    parts, have, notes = [], [], []
    rel_used = [False]

    def metric(key, lo, hi, lo_s, hi_s, lower_better, label, suffix=""):
        x = data.get(key)
        if x is None or (key == "pe" and x <= 0):
            return
        a = _lin(x, lo, hi, lo_s, hi_s)
        r = _rel_pct(sv.get(key, []), x, lower_better)
        if r is not None:
            parts.append(round(0.5 * a + 0.5 * r, 1)); rel_used[0] = True
        else:
            parts.append(a)
        have.append(f"{label} {x}{suffix}")

    metric("pe", 5, 25, 100, 20, True, "P/E")
    metric("eps_growth", -10, 25, 10, 100, False, "EPSg", "%")
    metric("roe", 5, 25, 20, 100, False, "ROE", "%")
    metric("de", 0.3, 2.0, 100, 20, True, "D/E")
    metric("div_yield", 0, 8, 40, 100, False, "DY", "%")

    parts = [p for p in parts if p is not None]
    if not parts:
        return {"symbol": symbol, "score": log_neutral, "low_confidence": True,
                "as_of": as_of, "have": [],
                "notes": ["Fundamentals entry present but no usable ratios — "
                          "neutral 50."]}

    score = round(sum(parts) / len(parts), 1)
    thin = len(parts) < 2                         # 1 ratio = thin, low confidence
    notes.append(f"Fundamentals ({as_of}): " + ", ".join(have)
                 + (" [sector-relative blend]" if rel_used[0] else "")
                 + ". Verify against the latest quarterly before acting.")
    return {"symbol": symbol, "score": score, "low_confidence": thin,
            "as_of": as_of, "have": have, "notes": notes}
