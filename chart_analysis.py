"""chart_analysis.py — the read a discretionary trader does by eye, computed.

Everything here comes from the banked daily OHLC (`daily_ohlc`), which since the
2026-09-01 backfill carries the exchange's OFFICIAL High/Low for ~1,240 sessions
per symbol. Nothing is fetched and nothing is inferred from a close-only series:
swing structure, trendlines and candle patterns are all range-dependent, and
computing them from closes alone would invent shapes that are not on the chart.

What it produces, per symbol:

  * swing pivots      — fractal highs/lows, the anchors every hand-drawn line uses
  * trend structure   — the HH/HL vs LH/LL sequence, which IS the trend definition
  * trendlines        — least-squares fits through the last N pivot highs and lows,
                        kept only when they have >=2 touches and price respected them
  * horizontal levels — pivots clustered into zones, ranked by touch count
  * moving averages   — EMA 20/50/200 stack, slope and price position
  * candle patterns   — engulfing / hammer / shooting star / doji / marubozu
  * volume            — the session's volume against the symbol's own 20-day median

A pattern is reported ONLY where it is present; there is no "closest match"
fallback. An empty list means the chart shows nothing of that kind, which is
information rather than a gap to fill.
"""

import numpy as np
import pandas as pd

import database as db

PIVOT_WINDOW = 5           # bars either side; the standard 5-bar fractal
TREND_PIVOTS = 4           # pivots a trendline is fitted through
LEVEL_TOLERANCE = 0.02     # 2% — how close two pivots must sit to be one zone
MIN_TOUCHES = 2
MAX_LINE_DISTANCE = 0.25   # a line >25% from price is history, not a level
DOJI_BODY = 0.10           # body <=10% of range
MARUBOZU_BODY = 0.85
LONG_WICK = 2.0            # wick at least 2x the body


def load(symbol, bars=260):
    """Daily OHLC as a DataFrame, oldest first. `bars` ~ one trading year."""
    rows = db.get_daily_ohlc(symbol, limit=100000)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.tail(bars).reset_index(drop=True)


# ---------------------------------------------------------------- structure

def pivots(df, window=PIVOT_WINDOW):
    """Fractal swing points: a high with `window` lower highs either side.

    The last `window` bars can never qualify — a pivot is only confirmed once
    price has moved away from it. That lag is real and is not smoothed away:
    a trader cannot draw through a swing that has not formed yet.
    """
    hi, lo = df["high"].values, df["low"].values
    out = []
    for i in range(window, len(df) - window):
        seg_h, seg_l = hi[i - window:i + window + 1], lo[i - window:i + window + 1]
        if hi[i] == seg_h.max() and (seg_h.argmax() == window):
            out.append({"i": i, "date": df["date"][i], "price": hi[i], "kind": "high"})
        elif lo[i] == seg_l.min() and (seg_l.argmin() == window):
            out.append({"i": i, "date": df["date"][i], "price": lo[i], "kind": "low"})
    return out


def structure(pv):
    """Higher-highs/higher-lows sequence — the textbook trend definition."""
    highs = [p for p in pv if p["kind"] == "high"][-3:]
    lows = [p for p in pv if p["kind"] == "low"][-3:]
    hh = len(highs) >= 2 and all(b["price"] > a["price"]
                                 for a, b in zip(highs, highs[1:]))
    lh = len(highs) >= 2 and all(b["price"] < a["price"]
                                 for a, b in zip(highs, highs[1:]))
    hl = len(lows) >= 2 and all(b["price"] > a["price"]
                                for a, b in zip(lows, lows[1:]))
    ll = len(lows) >= 2 and all(b["price"] < a["price"]
                                for a, b in zip(lows, lows[1:]))
    if hh and hl:
        label = "uptrend — higher highs and higher lows"
    elif lh and ll:
        label = "downtrend — lower highs and lower lows"
    elif hh or hl:
        label = "upward bias — structure only partly confirms"
    elif lh or ll:
        label = "downward bias — structure only partly confirms"
    else:
        label = "range — no directional swing sequence"
    return {"label": label, "higher_highs": hh, "higher_lows": hl,
            "lower_highs": lh, "lower_lows": ll,
            "swing_highs": [{"date": str(p["date"].date()), "price": round(p["price"], 2)}
                            for p in highs],
            "swing_lows": [{"date": str(p["date"].date()), "price": round(p["price"], 2)}
                           for p in lows]}


# ---------------------------------------------------------------- trendlines

def _line_through(a, b):
    """Two-point line in (bar index, price) space."""
    if b["i"] == a["i"]:
        return None
    slope = (b["price"] - a["price"]) / (b["i"] - a["i"])
    return slope, a["price"] - slope * a["i"]


def _score_line(df, slope, intercept, anchor_i, side, tol=0.015):
    """How well price actually respected the line after its second anchor.

    A trendline is only real if price came back to it and turned. So a candidate
    is scored by TOUCHES (a bar whose low/high came within `tol` of the line)
    and penalised by VIOLATIONS (a CLOSE decisively on the wrong side). Closes,
    not wicks, define a break — an intraday spike through a line is a test, and
    treating it as a break would discard almost every line a trader draws.
    """
    touches, violations = 0, 0
    n = len(df)
    for i in range(anchor_i, n):
        line = slope * i + intercept
        if line <= 0:
            continue
        if side == "support":
            if abs(df["low"].iloc[i] / line - 1) <= tol:
                touches += 1
            if df["close"].iloc[i] < line * (1 - tol):
                violations += 1
        else:
            if abs(df["high"].iloc[i] / line - 1) <= tol:
                touches += 1
            if df["close"].iloc[i] > line * (1 + tol):
                violations += 1
    return touches, violations


def _best_line(df, pv, kind, side, candidates=6):
    """Pick the anchor PAIR a trader would use, then validate it.

    Every pair drawn from the most recent pivots is tried; the winner is the one
    price respected most (touches) and violated least. A least-squares fit
    through all pivots was tried first and rejected: with three flat pivots and
    one far higher it extrapolated a "level" 10% away that price had never
    traded against. Real lines are anchored, not averaged.
    """
    pts = [p for p in pv if p["kind"] == kind][-candidates:]
    price_now = float(df["close"].iloc[-1])
    best = None
    for x in range(len(pts) - 1):
        for y in range(x + 1, len(pts)):
            a, b = pts[x], pts[y]
            fit = _line_through(a, b)
            if not fit:
                continue
            slope, intercept = fit
            touches, violations = _score_line(df, slope, intercept, b["i"], side)
            if touches < MIN_TOUCHES:
                continue
            score = touches - 2 * violations
            # Relevance: a line price has left 100%+ behind is history, not a
            # level anyone would trade against today. Bound it before scoring,
            # otherwise a shallow line from months ago wins on touch count while
            # sitting far below the current price (PRL scored 18 touches on a
            # line 163% away).
            value_now = slope * (len(df) - 1) + intercept
            if value_now <= 0 or abs(price_now / value_now - 1) > MAX_LINE_DISTANCE:
                continue
            # Ties break toward the more recent anchor — the newer line is the
            # one currently being respected.
            if best is None or (score, b["i"]) > (best["score"], best["anchors"][1]["i"]):
                best = {"score": score, "slope": slope, "intercept": intercept,
                        "touches": touches, "violations": violations,
                        "anchors": [a, b]}
    if best is None or best["score"] <= 0:
        return None
    n = len(df)
    price = float(df["close"].iloc[-1])
    value_now = best["slope"] * (n - 1) + best["intercept"]
    return {"slope": float(best["slope"]), "intercept": float(best["intercept"]),
            "touches": best["touches"], "violations": best["violations"],
            "value_now": float(value_now),
            "distance_pct": round((price / value_now - 1) * 100, 2),
            "direction": ("rising" if best["slope"] > 0 else
                          "falling" if best["slope"] < 0 else "flat"),
            "broken": (price < value_now if side == "support" else price > value_now),
            "from": str(best["anchors"][0]["date"].date()),
            "anchors": [{"i": int(a["i"]), "date": str(a["date"].date()),
                         "price": round(a["price"], 2)} for a in best["anchors"]]}


def trendlines(df, pv):
    """Rising support line through pivot lows, falling/rising resistance through
    pivot highs. Either can be None — a chart in a range has no clean line, and
    drawing one anyway is how a trader talks themselves into a trade."""
    return {"support": _best_line(df, pv, "low", "support"),
            "resistance": _best_line(df, pv, "high", "resistance")}


def levels(df, pv):
    """Horizontal zones: pivots clustered by price, ranked by how often tested."""
    if not pv:
        return []
    price = float(df["close"].iloc[-1])
    clusters = []
    for p in sorted(pv, key=lambda x: x["price"]):
        if clusters and abs(p["price"] / clusters[-1]["mean"] - 1) <= LEVEL_TOLERANCE:
            c = clusters[-1]
            c["prices"].append(p["price"])
            c["last"] = max(c["last"], p["date"])
            c["mean"] = float(np.mean(c["prices"]))
        else:
            clusters.append({"prices": [p["price"]], "mean": p["price"],
                             "last": p["date"]})
    out = []
    for c in clusters:
        if len(c["prices"]) < MIN_TOUCHES:
            continue
        out.append({"price": round(c["mean"], 2), "touches": len(c["prices"]),
                    "last_tested": str(c["last"].date()),
                    "side": "resistance" if c["mean"] > price else "support",
                    "distance_pct": round((price / c["mean"] - 1) * 100, 2)})
    return sorted(out, key=lambda z: (-z["touches"], abs(z["distance_pct"])))


# ---------------------------------------------------------------- MAs, candles

def moving_averages(df):
    close = df["close"]
    price = float(close.iloc[-1])
    out = {"price": round(price, 2)}
    for span in (20, 50, 200):
        if len(close) < span // 2:
            out[f"ema{span}"] = None
            continue
        e = close.ewm(span=span, adjust=False).mean()
        v = float(e.iloc[-1])
        prior = float(e.iloc[-11]) if len(e) > 11 else v
        out[f"ema{span}"] = {
            "value": round(v, 2),
            "price_vs_pct": round((price / v - 1) * 100, 2),
            "slope_10d_pct": round((v / prior - 1) * 100, 2) if prior else None,
            "rising": v > prior}
    e20, e50, e200 = out["ema20"], out["ema50"], out["ema200"]
    if all((e20, e50, e200)):
        if e20["value"] > e50["value"] > e200["value"]:
            out["stack"] = "bullish — 20 > 50 > 200"
        elif e20["value"] < e50["value"] < e200["value"]:
            out["stack"] = "bearish — 20 < 50 < 200"
        else:
            out["stack"] = "mixed — moving averages interleaved"
    else:
        out["stack"] = None
    return out


def _candle(o, h, l, c, prev):
    rng, body = h - l, abs(c - o)
    if rng <= 0:
        return []
    upper, lower = h - max(o, c), min(o, c) - l
    found = []
    if body / rng <= DOJI_BODY:
        found.append(("Doji", "indecision — buyers and sellers finished level"))
    if body / rng >= MARUBOZU_BODY:
        found.append(("Bullish marubozu" if c > o else "Bearish marubozu",
                      "one side controlled the whole session"))
    # Hammer and hanging man are the SAME shape — only the trend it appears in
    # decides which it is, and candle colour does not decide it. The shape is
    # named for what it shows; the caller supplies the context.
    if body > 0 and lower >= LONG_WICK * body and upper <= body:
        found.append(("Long lower wick (hammer / hanging man)",
                      "sellers drove price down and were rejected by the close"))
    if body > 0 and upper >= LONG_WICK * body and lower <= body:
        found.append(("Long upper wick (shooting star / inverted hammer)",
                      "buyers drove price up and were rejected by the close"))
    if prev is not None:
        po, pc = prev
        if c > o and pc < po and c >= po and o <= pc:
            found.append(("Bullish engulfing",
                          "today's body covers yesterday's down body"))
        if c < o and pc > po and c <= po and o >= pc:
            found.append(("Bearish engulfing",
                          "today's body covers yesterday's up body"))
    return found


def candles(df, lookback=5):
    out = []
    for i in range(max(1, len(df) - lookback), len(df)):
        r = df.iloc[i]
        prev = (float(df["open"].iloc[i - 1]), float(df["close"].iloc[i - 1]))
        pats = _candle(float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), prev)
        if pats:
            out.append({"date": str(r["date"].date()),
                        "patterns": [{"name": n, "meaning": m} for n, m in pats]})
    return out


def volume_read(df):
    v = df["volume"].dropna()
    if len(v) < 20:
        return None
    med = float(v.tail(20).median())
    last = float(v.iloc[-1])
    up = df["close"].iloc[-1] > df["open"].iloc[-1]
    return {"last": last, "median_20d": med,
            "ratio": round(last / med, 2) if med else None,
            "direction": "up day" if up else "down day",
            "note": ("expansion — conviction behind the move" if med and last > 1.5 * med
                     else "contraction — the move lacks participation" if med and last < 0.7 * med
                     else "average participation")}


def analyse(symbol, bars=260):
    df = load(symbol, bars)
    if df is None or len(df) < 60:
        return None
    pv = pivots(df)
    return {"symbol": symbol, "bars": len(df),
            "as_of": str(df["date"].iloc[-1].date()),
            "structure": structure(pv), "trendlines": trendlines(df, pv),
            "levels": levels(df, pv)[:6], "moving_averages": moving_averages(df),
            "candles": candles(df), "volume": volume_read(df),
            "pivots": [{"i": p["i"], "date": str(p["date"].date()),
                        "price": round(p["price"], 2), "kind": p["kind"]} for p in pv],
            "ohlc": [{"d": str(r["date"].date()), "o": r["open"], "h": r["high"],
                      "l": r["low"], "c": r["close"], "v": r["volume"]}
                     for _, r in df.iterrows()]}
