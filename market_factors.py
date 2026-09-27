"""market_factors.py — split every daily move into market, sector and stock.

The question "was that move the market, the sector, or the company?" is
answerable from price data alone. For each stock-day:

    r_stock = beta_mkt x r_market + beta_sector x r_sector_orth + residual

  * r_market is the CROSS-SECTIONAL MEDIAN return of the universe. Median, not
    mean, so one limit-up name cannot define "the market" — the same choice
    measure.py already makes for its cohort benchmark, and it needs no index
    fetch, so it covers the full banked history.
  * r_sector EXCLUDES the stock itself. Including it would guarantee a near-zero
    residual for any thin sector and quietly label every idiosyncratic move
    "sector-driven".
  * r_sector is orthogonalised against the market first, so a sector that simply
    tracks the index does not steal the market's share of the move.

Betas are estimated on a trailing window and SHIFTED ONE DAY, so the day being
decomposed never contributes to its own betas. Without that the decomposition
would look impressively tight and be worthless out of sample.
"""

import logging

import numpy as np
import pandas as pd

import config

log = logging.getLogger("market_factors")

BETA_WINDOW = 250          # ~1 trading year
BETA_MIN_PERIODS = 120
MIN_SECTOR_PEERS = 2       # below this a sector return is not meaningful


def load_panel(db, symbols=None, adjust=True):
    """-> dict of DataFrames (close/open/high/low/volume), dates x symbols.

    adjust=True back-adjusts for detected splits/bonuses. Leaving it off is only
    correct when DETECTING those actions, which needs the raw series.
    """
    syms = list(symbols or config.STOCKS)
    frames = {}
    for s in syms:
        bars = db.get_daily_ohlc(s, limit=100000)
        if len(bars) < BETA_MIN_PERIODS:
            continue
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        frames[s] = df.set_index("date")
    if not frames:
        return {}
    out = {}
    for field in ("open", "high", "low", "close", "volume"):
        out[field] = pd.DataFrame({s: f[field] for s, f in frames.items()}).sort_index()
    if adjust:
        import corporate_actions as ca
        stored = db.get_corporate_actions()
        out = ca.adjust(out, stored or ca.detect(out))
    return out


def _roll_beta(y, x, window=BETA_WINDOW, minp=BETA_MIN_PERIODS):
    """Rolling univariate beta of y on x, shifted so day t uses data to t-1."""
    cov = y.rolling(window, min_periods=minp).cov(x)
    var = x.rolling(window, min_periods=minp).var()
    return (cov / var.replace(0, np.nan)).shift(1)


def decompose(panel):
    """-> dict of DataFrames: ret, mkt_contrib, sec_contrib, residual, plus the
    betas and the sector-coverage mask. All aligned dates x symbols."""
    close = panel["close"]
    ret = close.pct_change()

    market = ret.median(axis=1)
    sectors = {}
    for sym in ret.columns:
        sec = config.SECTORS.get(sym)
        peers = [s for s in ret.columns if s != sym and config.SECTORS.get(s) == sec]
        sectors[sym] = (ret[peers].median(axis=1) if len(peers) >= MIN_SECTOR_PEERS
                        else pd.Series(np.nan, index=ret.index))
    sector = pd.DataFrame(sectors)

    beta_mkt, beta_sec = {}, {}
    mkt_c, sec_c = {}, {}
    for sym in ret.columns:
        bm = _roll_beta(ret[sym], market)
        beta_mkt[sym] = bm
        mkt_c[sym] = bm * market

        s = sector[sym]
        if s.notna().sum() >= BETA_MIN_PERIODS:
            # Strip the market out of the sector before charging the stock for it.
            s_orth = s - _roll_beta(s, market) * market
            after_mkt = ret[sym] - mkt_c[sym]
            bs = _roll_beta(after_mkt, s_orth)
            beta_sec[sym] = bs
            sec_c[sym] = bs * s_orth
        else:
            beta_sec[sym] = pd.Series(np.nan, index=ret.index)
            sec_c[sym] = pd.Series(0.0, index=ret.index)

    mkt_c = pd.DataFrame(mkt_c)
    sec_c = pd.DataFrame(sec_c).fillna(0.0)
    return {
        "ret": ret, "market": market, "sector": sector,
        "beta_mkt": pd.DataFrame(beta_mkt), "beta_sec": pd.DataFrame(beta_sec),
        "mkt_contrib": mkt_c, "sec_contrib": sec_c,
        "residual": ret - mkt_c - sec_c,
        "has_sector": sector.notna(),
    }


def classify(r, mkt_c, sec_c, resid, dominant=0.5):
    """Name the biggest share of a move. 'mixed' when nothing owns half of it —
    an honest answer, not a failure."""
    if r is None or not np.isfinite(r) or abs(r) < 1e-9:
        return None
    parts = {"market": abs(mkt_c or 0), "sector": abs(sec_c or 0),
             "idiosyncratic": abs(resid or 0)}
    top = max(parts, key=parts.get)
    return top if parts[top] >= dominant * abs(r) else "mixed"
