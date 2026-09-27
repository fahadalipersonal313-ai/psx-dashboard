"""Pure benchmark regime and synchronized relative-strength calculations. Missing inputs return unavailable results. Fetching is an explicit separate adapter."""

import logging

import config
import pandas as pd
import numpy as np

log = logging.getLogger("market_regime")


def fetch_index():
    """Return (DataFrame[date, open, close, volume], meta) for the benchmark."""
    import data_fetcher
    return data_fetcher.fetch_eod(config.BENCHMARK_INDEX)


def assess_regime(index_eod=None):
    """Risk-on / risk-off from the index vs its EMA. Returns a dict; regime is
    'unknown' (gate disabled) when the index is missing or too short."""
    if index_eod is None or len(index_eod) < config.REGIME_EMA_SPAN:
        return {"regime": "unknown", "index": config.BENCHMARK_INDEX,
                "level": None, "ema": None, "pct_above": None,
                "note": f"{config.BENCHMARK_INDEX} unavailable — regime gate off this run."}
    close = index_eod["close"].astype(float)
    ema = close.ewm(span=config.REGIME_EMA_SPAN, adjust=False).mean()
    level, ema_last = float(close.iloc[-1]), float(ema.iloc[-1])
    pct_above = (level / ema_last - 1) * 100
    regime = "risk-on" if level >= ema_last else "risk-off"
    note = (f"{config.BENCHMARK_INDEX} {level:,.0f} is {abs(pct_above):.1f}% "
            f"{'above' if regime == 'risk-on' else 'below'} its "
            f"{config.REGIME_EMA_SPAN}-EMA ({ema_last:,.0f}) -> {regime}.")
    return {"regime": regime, "index": config.BENCHMARK_INDEX, "level": level,
            "ema": ema_last, "pct_above": round(pct_above, 2), "note": note}


def _ret(series, window):
    if len(series) <= window:
        return None
    return float(series.iloc[-1]) / float(series.iloc[-1 - window]) - 1


def relative_strength(stock_eod, index_eod=None):
    """Stock return minus index return over the configured windows, blended to a
    0-100 RS score (50 = tracks the index; >50 = outperforming). None if data is
    insufficient — never fabricated."""
    if stock_eod is None or index_eod is None:
        return None
    if any("date" not in frame or frame["date"].duplicated().any() for frame in (stock_eod, index_eod)):
        return None
    aligned = pd.merge(stock_eod[["date", "close"]], index_eod[["date", "close"]], on="date", suffixes=("_stock", "_index")).sort_values("date")
    if len(aligned) <= max(config.RS_LOOKBACKS.values()):
        return None
    if aligned["date"].iloc[-1] != stock_eod["date"].max() or aligned["date"].iloc[-1] != index_eod["date"].max():
        return None
    sc, ic = aligned["close_stock"].astype(float), aligned["close_index"].astype(float)
    if not np.isfinite(sc).all() or not np.isfinite(ic).all() or (sc <= 0).any() or (ic <= 0).any():
        return None
    rels, num, used_w = {}, 0.0, 0.0
    for name, w in config.RS_LOOKBACKS.items():
        sr, ir = _ret(sc, w), _ret(ic, w)
        if sr is None or ir is None:
            continue
        rel = sr - ir
        rels[name] = round(rel * 100, 1)
        wt = config.RS_WEIGHTS.get(name, 0)
        num += wt * rel
        used_w += wt
    if used_w == 0:
        return None
    blended = num / used_w                          # weighted avg outperformance
    rs_score = max(0.0, min(100.0, 50 + blended * 200))   # ±25% blend -> 0/100
    return {"rs_score": round(rs_score, 1), "outperforming": blended > 0,
            "rel": rels, "blended_pct": round(blended * 100, 1)}
