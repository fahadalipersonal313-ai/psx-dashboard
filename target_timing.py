"""target_timing.py — how long a move of a given size has actually taken.

A target without a timeframe invites the worst habit in swing trading: holding a
dead position indefinitely because the target has not been "hit yet". But an
estimate must not be invented, and the obvious arithmetic — distance divided by
ATR — is wrong in a way that flatters: ATR measures how far a stock TRAVELS in a
day (high minus low), not how far it PROGRESSES, so a 2-ATR target does not take
two sessions. It typically takes many more, and sometimes never arrives.

So this measures it instead. For every historical session of a symbol it asks:
starting here, how many sessions until the high first reached close + k*ATR, and
did it reach it at all inside the horizon? That yields two numbers a card can
state honestly:

    * typical_sessions — the MEDIAN sessions-to-touch among the attempts that
      DID reach the level. Median, not mean, so one 40-session grind does not
      dominate.
    * hit_rate — the share of attempts that reached it within the horizon.

The hit rate is the important half and the half a "target in N days" label
normally hides. A 6-session median at a 45% hit rate is not a forecast that the
target arrives in six sessions; it is "when this worked it took about six, and
it worked under half the time".

Nothing here feeds a signal. It describes a level the technical layer already
chose, and per CLAUDE.md new findings are surfaced, not wired into the score.
"""

import numpy as np

import config
import database as db

HORIZON = 40          # sessions; beyond this a swing target is not "pending"
MIN_ATTEMPTS = 30     # below this the median is noise and None is returned


def _touch_sessions(high, close, atr, k, horizon=HORIZON):
    """-> (list of sessions-to-touch, attempts). Vectorised per start bar."""
    n = len(close)
    out = []
    attempts = 0
    for i in range(n - 1):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        level = close[i] + k * atr[i]
        window = high[i + 1:i + 1 + horizon]
        if len(window) < horizon:
            break                       # incomplete window: not a fair attempt
        attempts += 1
        hit = np.flatnonzero(window >= level)
        if hit.size:
            out.append(int(hit[0]) + 1)
    return out, attempts


def verified_history(symbol, cutoff=None):
    from session_calendar import last_completed
    from data_quality import bar_error, source_priority
    from corporate_actions import verified_bars
    cutoff = cutoff or last_completed()
    bars = [b for b in db.get_daily_ohlc(symbol, limit=100000) if b['date'] <= cutoff]
    if any(bar_error(b) or source_priority(b.get('source')) < 3 for b in bars):
        return []
    if len({b['date'] for b in bars}) != len(bars):
        return []
    bars.sort(key=lambda b: b['date'])
    bars = verified_bars(bars, db.get_corporate_actions(symbol), cutoff)
    if any(abs(b['close'] / a['close'] - 1) > .105 for a, b in zip(bars, bars[1:])):
        return []
    return bars


def for_symbol(symbol, k, horizon=HORIZON, cutoff=None):
    """Median sessions and hit rate for a move of `k` ATR, from this symbol's
    own history. None when the sample is too thin to mean anything."""
    bars = verified_history(symbol, cutoff)
    if len(bars) < 250:
        return None
    high = np.array([b["high"] for b in bars], dtype=float)
    close = np.array([b["close"] for b in bars], dtype=float)
    atr = _atr(bars)
    hits, attempts = _touch_sessions(high, close, atr, k, horizon)
    if attempts < MIN_ATTEMPTS:
        return None
    return {"k_atr": round(float(k), 2), "attempts": attempts,
            "hit_rate": round(len(hits) / attempts, 3),
            "typical_sessions": int(np.median(hits)) if hits else None,
            "horizon": horizon}


def _atr(bars, span=14):
    high = np.array([b["high"] for b in bars], dtype=float)
    low = np.array([b["low"] for b in bars], dtype=float)
    close = np.array([b["close"] for b in bars], dtype=float)
    prev = np.concatenate(([np.nan], close[:-1]))
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    out = np.full(len(tr), np.nan)
    if len(tr) > span:
        cs = np.convolve(tr, np.ones(span) / span, mode="valid")
        out[span - 1:] = cs
    return out


def estimate(symbol, price, target, atr=None, cutoff=None):
    """-> dict for a card, or None.

    `atr` should be the technical layer's own ATR so the card and the signal
    describe the same distance. The stored run row does not carry it, so it is
    recomputed from the symbol's bars when omitted — same 14-session true range.
    """
    if atr is None:
        bars = verified_history(symbol, cutoff)[-400:]
        if len(bars) < 60:
            return None
        series = _atr(bars)
        atr = float(series[-1]) if len(series) and np.isfinite(series[-1]) else None
    try:
        if not (price and target and atr) or target <= price or atr <= 0:
            return None
        k = (target - price) / atr
    except (TypeError, ZeroDivisionError):
        return None
    if k <= 0 or k > 10:
        return None
    stats = for_symbol(symbol, k, cutoff=cutoff)
    if not stats:
        return None
    stats["distance_pct"] = round((target / price - 1) * 100, 2)
    return stats
