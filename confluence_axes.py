"""confluence_axes.py — continuous, deliberately orthogonal setup dimensions.

Replaces nothing yet. The four-dimension `_confluence` in signal_generator is
binary and near-collinear (trend is price>EMA50, structure is price>support, and
in an uptrend the second is implied by the first), which is why graded outcomes
came out flat across it: 2/4 won 17%, 3/4 26%, 4/4 25%.

These axes fix the three faults of that model:
  * each returns a CONTINUOUS 0..1 instead of a flag, so magnitude survives and
    a value oscillating around a threshold cannot flip the whole dimension;
  * they are chosen for different MECHANISMS, not to reach a round number;
  * every one is computable from close/open/volume alone, so the banked EOD
    history (`daily_eod`, ~1,200 bars/symbol) can score them over years rather
    than the ~50 days of intraday-derived bars.

EVERY AXIS IS UNMEASURED. Nothing here touches signal generation, by design —
this repo's graded history says the veto layer selected the WORSE subset of a
pool that had edge (emitted Buys 36% vs candidates 63%), so a new gate built on
unmeasured inputs is the one change most likely to destroy edge. Store, display,
measure with measure.render(); wire in only what earns it, and prefer ranking or
position sizing over a veto.

Deliberately ABSENT: money flow. A true CMF needs the day's High/Low, which PSX
DPS end-of-day does not carry. `technical['cmf']` remains the live source and is
limited to the intraday-derived daily_ohlc bars.
"""

import logging
import math
import statistics

log = logging.getLogger("confluence_axes")

TREND_SPAN = 20          # sessions of EMA50 slope
VOL_WINDOW = 20          # realised-volatility window
VOL_LOOKBACK = 250       # percentile reference (~1 trading year)
PERSIST_CAP = 20         # sessions; above this adds no further credit
MIN_BARS = 60            # below this the axes are not trustworthy


def _pct(values, x):
    """Fraction of `values` at or below x (0..1). Empty -> None."""
    if not values:
        return None
    return sum(1 for v in values if v <= x) / len(values)


def _ema(series, span):
    k = 2.0 / (span + 1.0)
    out, cur = [], series[0]
    for v in series:
        cur = v * k + cur * (1 - k)
        out.append(cur)
    return out


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _trend_quality(closes):
    """Is the intermediate trend RISING, and how firmly.

    Slope of the 50-EMA over TREND_SPAN sessions, expressed as a fraction of
    price so it compares across a PKR 17 name and a PKR 540 one. Distinct from
    'price is above the EMA': a price can sit above a flattening average, which
    is the setup that rolls over.
    """
    if len(closes) < 50 + TREND_SPAN:
        return None
    ema = _ema(closes, 50)
    now, then = ema[-1], ema[-1 - TREND_SPAN]
    if not then:
        return None
    # +/-10% over the span spans the full 0..1 range; beyond that it saturates.
    return _clamp01(0.5 + ((now - then) / then) * 5.0)


def _stability(closes):
    """Is this move ORDERLY or violent, relative to the symbol's own norm.

    Close-to-close realised volatility percentile, inverted so a calm advance
    scores higher than a whipsaw. This is the H/L-free stand-in for ATR, and the
    inversion is a HYPOTHESIS, not a finding — if measurement says choppy names
    do better, flip it rather than dropping the axis.
    """
    if len(closes) < VOL_WINDOW * 2:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1.0
            for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < VOL_WINDOW * 2:
        return None
    vols = [statistics.pstdev(rets[i - VOL_WINDOW:i])
            for i in range(VOL_WINDOW, len(rets) + 1)]
    ref = vols[-VOL_LOOKBACK:]
    p = _pct(ref, vols[-1])
    return None if p is None else _clamp01(1.0 - p)


def _participation(volumes):
    """Is REAL volume behind the move, judged against the symbol's own norm.

    Not a money-flow read — it has no direction, since without High/Low there is
    no way to tell whether volume traded into strength or into weakness. It says
    only that the market is paying attention.
    """
    vols = [v for v in volumes[-VOL_LOOKBACK:] if v]
    if len(vols) < 30:
        return None
    recent = vols[-5:]
    base = statistics.median(vols)
    if not base:
        return None
    # A 3x median session is a full score; log keeps one spike from dominating.
    return _clamp01(math.log1p(statistics.mean(recent) / base) / math.log1p(3.0))


def _structure(price, support, closes):
    """How much ROOM is there before the trade is wrong.

    Distance above support measured in daily-sigma units, not percent, so a
    quiet name and a volatile one are compared on the risk each actually
    carries. Below support scores 0 — the trade is already wrong.
    """
    if not price or not support or len(closes) < VOL_WINDOW + 1:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1.0
            for i in range(len(closes) - VOL_WINDOW, len(closes)) if closes[i - 1]]
    sigma = statistics.pstdev(rets) if len(rets) > 2 else None
    if not sigma:
        return None
    dist = (price - support) / price
    # 6 sigma of headroom is a full score; nearer than that scales down.
    return _clamp01((dist / sigma) / 6.0)


def _persistence(closes):
    """How LONG the setup has held — the one purely temporal axis.

    Consecutive sessions closing above the 50-EMA, capped. A setup in its first
    session and one in its twentieth are different trades; nothing else in the
    model can see the difference.
    """
    if len(closes) < 50 + 2:
        return None
    ema = _ema(closes, 50)
    n = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] > ema[i]:
            n += 1
            if n >= PERSIST_CAP:
                break
        else:
            break
    return _clamp01(n / PERSIST_CAP)


def compute(technical, closes, volumes):
    """Return {axes: {name: 0..1 or None}, composite, coverage, bars}.

    closes/volumes: oldest-first daily series (banked EOD history preferred).
    A missing input yields None for that axis and is EXCLUDED from the
    composite — never defaulted to a neutral 0.5, which would invent agreement
    the data does not support.
    """
    closes = [float(c) for c in (closes or []) if c]
    volumes = [float(v) for v in (volumes or []) if v is not None]
    rs = (technical or {}).get("relative_strength")

    axes = {
        "trend_quality": _trend_quality(closes),
        # Already 0-100 and cross-sectional — the only axis that compares this
        # symbol to the rest of the market rather than to its own past.
        "relative_strength": (_clamp01(rs / 100.0) if rs is not None else None),
        "stability": _stability(closes),
        "participation": _participation(volumes),
        "structure": _structure((technical or {}).get("price"),
                                (technical or {}).get("support"), closes),
        "persistence": _persistence(closes),
    }
    present = [v for v in axes.values() if v is not None]
    return {
        "axes": {k: (round(v, 4) if v is not None else None)
                 for k, v in axes.items()},
        "composite": (round(sum(present) / len(present), 4) if present else None),
        "coverage": f"{len(present)}/{len(axes)}",
        "bars": len(closes),
        "trustworthy": len(closes) >= MIN_BARS and len(present) >= 4,
    }


def for_symbol(symbol, technical, db):
    """Convenience: pull the deepest series available and compute.

    Prefers the banked EOD history (years) and falls back to the intraday-derived
    daily bars (~50 days) so this still returns something before the first
    backfill lands.
    """
    rows = db.get_eod_history(symbol)
    if len(rows) < MIN_BARS:
        rows = db.get_daily_ohlc(symbol, limit=400)
    return compute(technical,
                   [r.get("close") for r in rows],
                   [r.get("volume") for r in rows])
