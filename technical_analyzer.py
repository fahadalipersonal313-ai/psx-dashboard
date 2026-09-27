"""Technical features from supplied history. Wilder RSI, prior-bar structure and true OHLC indicators. The score is experimental, not a probability."""

import logging
import numpy as np
import pandas as pd

import config

log = logging.getLogger("technical")


# ----------------------------- indicators ---------------------------------
def rsi(series, period=14):
    if period < 1:
        raise ValueError("period must be positive")
    values = series.astype(float)
    delta = values.diff().iloc[1:].to_numpy()
    gain = _wilder(np.maximum(delta, 0), period)
    loss = _wilder(np.maximum(-delta, 0), period)
    result = np.full(len(values), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = 100 - 100 / (1 + gain / loss)
    ratio[(loss == 0) & (gain > 0)] = 100
    ratio[(gain == 0) & (loss > 0)] = 0
    ratio[(gain == 0) & (loss == 0)] = 50
    result[1:] = ratio
    return pd.Series(result, index=series.index)


def macd(series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def bollinger(series, period=20, mult=2):
    mid = series.rolling(period).mean()
    sd = series.rolling(period).std()
    return mid + mult * sd, mid, mid - mult * sd


def obv(close, volume):
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def atr_from_close(close, period=14):
    """Approximate ATR using close-to-close ranges (EOD feed has no H/L)."""
    tr = close.diff().abs()
    return tr.rolling(period).mean()


def range_proxy(open_, close):
    """Daily true-range estimate from open+close (PSX EOD has no H/L).

    Uses max(body, overnight gap, close-to-close) so it captures opening gaps
    and the day's body — wider and more realistic than close-to-close alone,
    which makes ATR-based stops less likely to whipsaw. It is still an
    UNDER-estimate (intraday wicks are invisible) and is labelled as such.
    """
    prev_close = close.shift(1)
    body = (close - open_).abs()
    gap = (open_ - prev_close).abs()
    c2c = (close - prev_close).abs()
    return pd.concat([body, gap, c2c], axis=1).max(axis=1)


def adx_proxy(close, period=14):
    """Directional-strength proxy from close data (true ADX needs H/L).
    Returns 0-100 style trend-strength estimate, clearly labelled a proxy."""
    up = close.diff().clip(lower=0).rolling(period).sum()
    dn = (-close.diff().clip(upper=0)).rolling(period).sum()
    denom = (up + dn).replace(0, np.nan)
    dx = (abs(up - dn) / denom) * 100
    return dx.rolling(period).mean()


def _wilder(x, period):
    """Wilder's moving average (used for true ATR/ADX). Returns an array the same
    length as x, NaN until index period-1."""
    out = np.full(len(x), np.nan)
    if len(x) >= period:
        out[period - 1] = np.mean(x[:period])
        for i in range(period, len(x)):
            out[i] = (out[i - 1] * (period - 1) + x[i]) / period
    return out


def true_atr_adx(ohlc, period=14):
    """TRUE ATR and ADX from REAL daily OHLC bars (list of dicts oldest-first,
    banked from the intraday H/L feed). ATR needs ~period+1 bars; ADX needs
    ~2*period. Each is returned as None until enough bars exist, so the caller
    keeps using the close-based proxy for whichever isn't ready yet."""
    n = len(ohlc)
    if n < period + 2:
        return {"atr": None, "adx": None}
    high = np.array([b["high"] for b in ohlc], float)
    low = np.array([b["low"] for b in ohlc], float)
    close = np.array([b["close"] for b in ohlc], float)
    pc = close[:-1]
    tr = np.maximum.reduce([high[1:] - low[1:],
                            np.abs(high[1:] - pc), np.abs(low[1:] - pc)])
    up = high[1:] - high[:-1]
    dn = low[:-1] - low[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_arr = _wilder(tr, period)
    atr = None if np.isnan(atr_arr[-1]) else float(atr_arr[-1])
    s_tr, s_pdm, s_mdm = _wilder(tr, period), _wilder(pdm, period), _wilder(mdm, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * s_pdm / s_tr
        mdi = 100 * s_mdm / s_tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx_valid = dx[~np.isnan(dx)]
    adx = None
    if len(dx_valid) >= period:
        adx_arr = _wilder(dx_valid, period)
        if not np.isnan(adx_arr[-1]):
            adx = float(adx_arr[-1])
    return {"atr": atr, "adx": adx}


def obv_price_divergence(close, obv_series, lookback=30):
    """Bullish OBV/price divergence: price's most recent swing low is flat or
    lower than the prior swing low in the window, while OBV's low at that same
    point is HIGHER than its prior low — sellers pushing price down on
    shrinking volume pressure, the classic quiet-accumulation signature.
    Returns None when there isn't enough history."""
    if len(close) < lookback:
        return None
    c = close.tail(lookback).to_numpy()
    o = obv_series.tail(lookback).to_numpy()
    half = lookback // 2
    c1, c2, o1, o2 = c[:half], c[half:], o[:half], o[half:]
    i1, i2 = int(np.argmin(c1)), int(np.argmin(c2))
    price_low1, price_low2 = float(c1[i1]), float(c2[i2])
    obv_low1, obv_low2 = float(o1[i1]), float(o2[i2])
    bullish = bool(price_low2 <= price_low1 * 1.01 and obv_low2 > obv_low1)
    return {"bullish": bullish, "price_low1": price_low1, "price_low2": price_low2,
            "obv_low1": obv_low1, "obv_low2": obv_low2}


def chaikin_money_flow(ohlc, period=20):
    """Chaikin Money Flow from REAL daily OHLC bars (needs real high/low — the
    EOD feed alone can't compute this). Weights each day's volume by where the
    close sits within that day's range: +1 at the high (pure buying pressure),
    -1 at the low (pure selling). Returns None until enough real bars exist;
    the caller must not fall back to a close-only proxy for this one — a fake
    range would invert the whole point of the indicator."""
    if len(ohlc) < period:
        return None
    bars = ohlc[-period:]
    high = np.array([b["high"] for b in bars], float)
    low = np.array([b["low"] for b in bars], float)
    close = np.array([b["close"] for b in bars], float)
    volume = np.array([b["volume"] for b in bars], float)
    rng = high - low
    mfm = np.divide((close - low) - (high - close), rng, out=np.zeros_like(rng), where=rng != 0)
    mfv = mfm * volume
    vol_sum = volume.sum()
    return float(mfv.sum() / vol_sum) if vol_sum else None


def support_resistance(close, lookback=60):
    win = close.iloc[:-1].tail(lookback)
    if win.empty:
        raise ValueError("Prior session history required for structure")
    return float(win.quantile(.10)), float(win.quantile(.90)), float(win.min()), float(win.max())


def candle_signal(close):
    """Very simple pattern proxy from closes: 3-bar momentum reversal."""
    if len(close) < 4:
        return None
    c = close.tail(4).values
    if c[0] > c[1] > c[2] and c[3] > c[2]:
        return "potential bullish reversal (3-down then up)"
    if c[0] < c[1] < c[2] and c[3] < c[2]:
        return "potential bearish reversal (3-up then down)"
    return None


# ----------------------------- main entry ---------------------------------
def analyze(symbol, eod_df, quote, rs_score=None, ohlc=None):
    """eod_df: DataFrame[date, (open,) close, volume] (real fetched data) or None.
    quote: dict from data_fetcher.latest_quote.
    rs_score: optional 0-100 relative-strength vs the benchmark index.
    ohlc: optional list of REAL banked daily OHLC bars (oldest-first); when long
    enough, true ATR/ADX replace the close-based proxies. Pass None (e.g. in
    backtests) to force the proxies.
    Returns dict with score (0-100), classification, levels, and notes."""
    notes, missing = [], []
    out = {"symbol": symbol, "score": None, "classification": "No data",
           "notes": notes, "missing": missing}

    if eod_df is None or len(eod_df) < 30:
        notes.append("Insufficient price history (<30 sessions) — technical "
                     "score withheld rather than guessed.")
        out["score"] = 50.0  # neutral, low-confidence
        out["low_confidence"] = True
        return out

    df = eod_df.copy()
    close, vol = df["close"].astype(float), df["volume"].astype(float)
    price = float(quote["price"]) if quote.get("price") else float(close.iloc[-1])

    # --- core indicators
    df["rsi"] = rsi(close)
    macd_line, macd_sig, macd_hist = macd(close)
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema40 = close.ewm(span=40, adjust=False).mean()
    bb_up, bb_mid, bb_lo = bollinger(close)
    obv_series = obv(close, vol)
    if "open" in df.columns and df["open"].notna().sum() >= 20:
        atr = range_proxy(df["open"].astype(float), close).rolling(14).mean()
        atr_method = "open+close range (gaps & body; no intraday wicks)"
    else:
        atr = atr_from_close(close)
        atr_method = "close-to-close (open unavailable)"
    adx = adx_proxy(close)
    support, resistance, recent_lo, recent_hi = support_resistance(close)

    last_rsi = float(df["rsi"].iloc[-1]) if not np.isnan(df["rsi"].iloc[-1]) else None
    avg_vol = float(vol.iloc[:-1].tail(20).mean())
    today_vol = float(quote.get("volume") or vol.iloc[-1])
    vol_spike = today_vol > 1.8 * avg_vol if avg_vol else False
    last_atr = float(atr.iloc[-1]) if not np.isnan(atr.iloc[-1]) else None
    last_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else None
    adx_is_true = atr_is_true = False
    # Upgrade to TRUE ATR/ADX once enough real OHLC bars are banked (else proxy).
    if ohlc and len(ohlc) >= config.MIN_OHLC_BARS_FOR_TRUE:
        t = true_atr_adx(ohlc)
        if t["atr"] is not None:
            last_atr = t["atr"]; atr_is_true = True
            atr_method = "true ATR (real intraday H/L)"
        if t["adx"] is not None:
            last_adx = t["adx"]; adx_is_true = True
    # CMF needs real daily H/L, same gate as true ATR/ADX above.
    cmf = (chaikin_money_flow(ohlc) if ohlc and len(ohlc) >= config.MIN_OHLC_BARS_FOR_TRUE
           else None)
    atr_pct = (last_atr / price * 100) if (last_atr and price) else None
    momentum_20d = (price / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0
    obv_trend_up = float(obv_series.iloc[-1]) > float(obv_series.iloc[-10]) \
        if len(obv_series) > 10 else None
    obv_divergence = obv_price_divergence(close, obv_series)

    breakout = price > resistance and vol_spike
    breakdown = price < support

    # --- scoring (each component contributes to 0-100)
    score, max_pts = 0.0, 0.0

    def add(points, max_p, label, hit_note=None):
        nonlocal score, max_pts
        score += points
        max_pts += max_p
        if hit_note:
            notes.append(hit_note)

    # Short-history trend via 10/20/40 session EMAs (25 pts). The strategy
    # deliberately avoids EMA-200 because its input contract is 42 sessions.
    pts = 0
    if price > float(ema10.iloc[-1]): pts += 8
    if price > float(ema20.iloc[-1]): pts += 9
    if price > float(ema40.iloc[-1]): pts += 8
    add(pts, 25, "trend",
        f"Price vs EMAs: 10={ema10.iloc[-1]:.2f}, 20={ema20.iloc[-1]:.2f}, "
        f"40={ema40.iloc[-1]:.2f}")

    # RSI (15 pts) — reward healthy zone, penalise extremes
    if last_rsi is not None:
        if 45 <= last_rsi <= 65: r_pts = 15
        elif 35 <= last_rsi < 45 or 65 < last_rsi <= 72: r_pts = 9
        elif last_rsi < 30: r_pts = 5; notes.append("RSI oversold (<30) — possible bounce but weak trend")
        elif last_rsi > 75: r_pts = 2; notes.append("RSI overbought (>75) — chase risk")
        else: r_pts = 6
        add(r_pts, 15, "rsi", f"RSI(14)={last_rsi:.1f}")
    else:
        missing.append("RSI")

    # MACD (15 pts)
    m_pts = 0
    if float(macd_hist.iloc[-1]) > 0: m_pts += 8
    add(m_pts, 8, "macd", f"MACD hist={macd_hist.iloc[-1]:.3f}")

    # Momentum (10 pts)
    mo_pts = 10 if momentum_20d > 5 else 7 if momentum_20d > 0 else 3 if momentum_20d > -5 else 0
    add(mo_pts, 10, "momentum", f"20-day momentum {momentum_20d:+.1f}%")

    # Volume/OBV (15 pts)
    v_pts = 0
    if vol_spike: v_pts += 7; notes.append(f"Volume spike: today {today_vol:,.0f} vs 20d avg {avg_vol:,.0f}")
    if obv_trend_up: v_pts += 8
    add(v_pts, 15, "volume")

    # Breakout / breakdown (10 pts)
    if breakout:
        add(10, 10, "breakout", f"Breakout above resistance {resistance:.2f} on volume")
    elif breakdown:
        add(0, 10, "breakdown", f"BREAKDOWN below support {support:.2f}")
    else:
        add(5, 10, "range")

    # Trend strength ADX (10 pts) — true ADX once enough real OHLC bars exist.
    if last_adx is not None:
        add(min(10, last_adx / 5), 10, "adx",
            f"Trend strength ADX={last_adx:.0f}"
            + (" (true, from real H/L)" if adx_is_true else " (proxy, not true ADX)"))
    else:
        missing.append("ADX")

    # Bollinger Bands (10 pts). These were computed but UNUSED before. The value
    # here isn't another trend vote — it's volatility regime + position:
    #   * a "squeeze" (bands unusually tight) = coiled volatility, a move brewing;
    #     a squeeze resolving upward is a high-probability setup.
    #   * %B says where price sits in the band (top = strength, bottom = weakness).
    bb_u, bb_m, bb_l = float(bb_up.iloc[-1]), float(bb_mid.iloc[-1]), float(bb_lo.iloc[-1])
    bb_pct_b = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) else 0.5
    bb_bandwidth = (bb_u - bb_l) / bb_m if bb_m else None
    _recent_bw = ((bb_up - bb_lo) / bb_mid).dropna().tail(100)
    bb_squeeze = bool(bb_bandwidth is not None and len(_recent_bw) >= 20
                      and bb_bandwidth <= _recent_bw.quantile(0.25))
    if bb_squeeze and bb_pct_b >= 0.6:
        bb_pts = 10
        notes.append(f"Bollinger SQUEEZE resolving up (%B={bb_pct_b:.2f}) — coiled "
                     "volatility breaking higher")
    elif bb_pct_b > 1.0:
        bb_pts = 5
        notes.append(f"Above upper Bollinger band (%B={bb_pct_b:.2f}) — strong but extended")
    elif 0.5 <= bb_pct_b <= 1.0:
        bb_pts = 7
    elif bb_pct_b < 0.2:
        bb_pts = 2
        notes.append(f"Hugging lower Bollinger band (%B={bb_pct_b:.2f}) — weak")
    else:
        bb_pts = 5
    if bb_squeeze and bb_pct_b < 0.6:
        notes.append("Bollinger squeeze (low volatility) — breakout brewing, "
                     "direction unconfirmed")
    add(bb_pts, 10, "bollinger")

    # Relative strength (config.RS_POINTS) — is the stock BEATING the index? A
    # stock can be in an uptrend yet still LAG the market; RS catches that and
    # steers the score toward genuine leaders. Skipped if the index was missing.
    if rs_score is not None:
        add(rs_score / 100 * config.RS_POINTS, config.RS_POINTS, "relative_strength",
            f"Relative strength {rs_score:.0f}/100 vs {config.BENCHMARK_INDEX} "
            + ("(outperforming)" if rs_score >= 50 else "(LAGGING the market)"))

    final = round(score / max_pts * 100, 1) if max_pts else 50.0

    cls = ("Strong bullish" if final >= 80 else "Bullish" if final >= 65
           else "Neutral" if final >= 45 else "Weak" if final >= 30 else "Bearish")
    cs = candle_signal(close)
    if cs:
        notes.append("Candle proxy: " + cs)
    # Real open-vs-close read (Option A): PSX gaps often, and we now have `open`.
    if "open" in df.columns and pd.notna(df["open"].iloc[-1]) and len(close) >= 2:
        o_last, prev_c = float(df["open"].iloc[-1]), float(close.iloc[-2])
        if o_last > prev_c * 1.015:
            notes.append(f"Gap-up open ({(o_last/prev_c-1)*100:+.1f}% vs prev close)")
        elif o_last < prev_c * 0.985:
            notes.append(f"Gap-down open ({(o_last/prev_c-1)*100:+.1f}% vs prev close)")
    if atr_pct and atr_pct > config.RISK["max_volatility_pct"]:
        notes.append(f"High volatility: ATR≈{atr_pct:.1f}% of price ({atr_method})")

    stop_loss = round(max(support, price - config.RISK["default_stop_atr_mult"]
                          * (last_atr or price * 0.02)), 2)
    risk = price - stop_loss
    target1 = round(price + 2 * risk, 2)
    target2 = round(min(price + 3 * risk, recent_hi * 1.05), 2)
    if target2 <= target1:
        target2 = None
    rr = round((target1 - price) / risk, 2) if risk > 0 else None

    # --- REAL reward:risk to the nearest OVERHEAD ceiling. `rr` above is the
    # projected-target ratio (≈2.0 by construction) and can't catch "buying right
    # under resistance". headroom_rr measures the ACTUAL room above price:
    #   * below resistance      -> room is the gap up to that ceiling (small = trap)
    #   * cleared resistance     -> room up to the recent high
    #   * at/above the high      -> open space; use a measured-move target so a
    #                               genuine new-high breakout is NOT falsely flagged
    if price < resistance:
        reward_room = resistance - price
    elif price < recent_hi:
        reward_room = recent_hi - price
    else:
        reward_room = 2 * risk
    headroom_rr = round(reward_room / risk, 2) if risk > 0 else None
    headroom_pct = round((resistance - price) / price * 100, 1) if price else None

    # --- Overextension measure: how far price is stretched above the reference
    # EMA (config.PULLBACK_EMA_SPAN — 50 since 2026-08-12, was 20). Uses PERCENT
    # above that EMA (robust) plus 20-day momentum; the ATR-normalised distance
    # (ext_atr) is kept for info only because the EOD ATR proxy understates true
    # range and inflated it.
    ema20_last = float(ema20.iloc[-1])
    ema40_last = float(ema40.iloc[-1])
    ref_span = getattr(config, "PULLBACK_EMA_SPAN", 20)
    ref_series = ema40 if ref_span == 40 else ema20
    ref_ema = float(ref_series.iloc[-1])
    ext_pct = round((price / ref_ema - 1) * 100, 1) if ref_ema else None
    ext_atr = round((price - ref_ema) / last_atr, 2) if last_atr else None
    extended = bool((ext_pct is not None and ext_pct > config.RISK["max_extension_pct"])
                    or momentum_20d > config.RISK["max_extension_momentum_pct"])
    if extended:
        notes.append(f"EXTENDED: {ext_pct}% above EMA{ref_span}, 20d momentum "
                     f"{momentum_20d:+.1f}% — chase risk, a pullback entry is safer")

    # --- Accumulation candidate: a SEPARATE, lower-bar tag from the Buy signal —
    # "smart money may be quietly building a position" rather than "act now".
    # Deliberately NOT folded into the score (would double-count OBV/volume,
    # already 15 pts above); surfaced as its own flag so it can be spotted
    # BEFORE a stock ever reaches Buy. Heuristic, proxy-based — labelled as such.
    #
    # The defining feature of ACCUMULATION (vs an ordinary uptrend) is buying
    # pressure WHILE PRICE IS FLAT OR DOWN — quiet absorption inside a base, not
    # a stock that is already running. So a rising OBV only counts when price has
    # NOT advanced over the same window; otherwise it is just trend confirmation.
    price_chg_window = ((price / float(close.iloc[-10]) - 1) * 100
                        if len(close) > 10 else 0.0)
    price_flat_or_down = price_chg_window <= 2.0   # ≤+2% over the OBV window

    strong_reasons = []   # high-conviction signals (each can stand on its own)
    weak_reasons = []     # supporting signals (need a second one to count)
    if obv_divergence and obv_divergence["bullish"]:
        strong_reasons.append("bullish OBV/price divergence (price flat/down, OBV up)")
    if cmf is not None and cmf > 0.10:
        strong_reasons.append(f"strong CMF {cmf:+.2f} (real-H/L buying pressure)")
    if obv_trend_up and price_flat_or_down:
        weak_reasons.append(f"OBV rising while price flat/down ({price_chg_window:+.1f}%)")
    if vol_spike and not breakout and price_flat_or_down:
        weak_reasons.append("volume spike inside the range (no breakout yet)")
    if cmf is not None and 0.05 < cmf <= 0.10:
        weak_reasons.append(f"CMF {cmf:+.2f} (mild buying pressure, real H/L)")

    accum_reasons = strong_reasons + weak_reasons
    # Net selling pressure on REAL data overrides every proxy signal.
    if cmf is not None and cmf < -0.05:
        accum_reasons = strong_reasons = weak_reasons = []
    # Require genuine confluence: one high-conviction signal, OR at least two
    # independent supporting signals. A single weak signal is NOT enough — that
    # is what previously flagged most of the market as "accumulating".
    has_confluence = bool(strong_reasons) or len(weak_reasons) >= 2
    accumulation_candidate = bool(
        accum_reasons and has_confluence
        and not breakout and not breakdown and not extended)
    if accumulation_candidate:
        notes.append("ACCUMULATION candidate: " + "; ".join(accum_reasons))

    # --- Pullback-entry setup: an established uptrend that has retraced to its
    # rising reference EMA with momentum cooled but structure intact — the
    # lower-risk entry (buy the dip profit-takers create) vs chasing the
    # breakout. buy_zone is the band around that EMA, floored at support.
    #
    # The reference is configured for the short-window contract. Price inside
    # its pullback zone can sit slightly below the reference EMA, so a strict
    # `price > reference EMA` test would contradict the zone itself. The setup
    # instead requires a rising reference EMA and price above the 40-session
    # trend EMA. RSI uses 35-65 so momentum may cool during the retracement.
    ref_rising = (len(ref_series) > 10
                  and ref_ema > float(ref_series.iloc[-11]))
    buy_zone_high = round(ref_ema * 1.03, 2)
    buy_zone_low = round(max(support, ref_ema * 0.96), 2)
    in_buy_zone = bool(buy_zone_low <= price <= buy_zone_high)
    pullback_ready = bool(
        in_buy_zone and not breakdown and not extended
        and ref_rising
        and price > ema40_last
        and (last_rsi is not None and 35 <= last_rsi <= 65)
        and (bool(obv_trend_up) or float(macd_line.iloc[-1]) > 0))
    if pullback_ready:
        notes.append(f"PULLBACK setup: retraced into the EMA{ref_span} buy-zone "
                     f"({buy_zone_low}–{buy_zone_high}), uptrend intact, RSI cooled "
                     f"to {last_rsi:.0f} — lower-risk entry than chasing.")

    # --- which sub-indicators were bullish at this bar. Stored in the DB so
    # the learning loop can track per-indicator accuracy per symbol over time
    # and feed back into confidence (scoring_engine._indicator_accuracy_boost).
    # None = indicator not computable this run (data too short etc.).
    tech_flags = {
        "trend":    price > ema40_last,
        "rsi":      (last_rsi is not None and 40 <= last_rsi <= 72),
        "macd":     float(macd_hist.iloc[-1]) > 0,
        "obv":      bool(obv_trend_up) if obv_trend_up is not None else None,
        "momentum": momentum_20d > 0,
        "bb":       bb_pct_b >= 0.5,
        "rs":       bool(rs_score >= 50) if rs_score is not None else None,
        "accumulation": accumulation_candidate if accum_reasons else None,
    }

    out.update({
        "score": final, "classification": cls, "price": price,
        "volume": today_vol, "avg_volume": avg_vol, "volume_spike": vol_spike,
        "rsi": last_rsi, "macd_hist": float(macd_hist.iloc[-1]),
        "ema10": float(ema10.iloc[-1]), "ema20": float(ema20.iloc[-1]),
        "ema40": ema40_last, "ema50": ema40_last, "ema200": None,
        "bb_upper": bb_u, "bb_lower": bb_l, "bb_pct_b": round(bb_pct_b, 3),
        "bb_bandwidth": (round(bb_bandwidth, 4) if bb_bandwidth is not None else None),
        "bb_squeeze": bb_squeeze,
        "support": support, "resistance": resistance,
        "recent_high": recent_hi, "recent_low": recent_lo,
        "breakout": breakout, "breakdown": breakdown,
        "atr": last_atr, "atr_pct": atr_pct, "atr_method": atr_method,
        "adx_proxy": last_adx, "atr_is_true": atr_is_true, "adx_is_true": adx_is_true,
        "momentum_20d": momentum_20d, "obv_up": obv_trend_up,
        "stop_loss": stop_loss, "target1": target1, "target2": target2,
        "risk_reward": rr, "headroom_rr": headroom_rr, "headroom_pct": headroom_pct,
        "ext_pct": ext_pct, "ext_atr": ext_atr, "extended": extended,
        "buy_zone_low": buy_zone_low, "buy_zone_high": buy_zone_high,
        "in_buy_zone": in_buy_zone, "pullback_ready": pullback_ready,
        "buy_zone_ema_span": ref_span,
        "relative_strength": rs_score,
        "low_confidence": len(close) < config.MIN_STRATEGY_HISTORY,
        "tech_flags": tech_flags,
        "cmf": cmf,
        "obv_divergence_bullish": (obv_divergence["bullish"] if obv_divergence else None),
        "accumulation_candidate": accumulation_candidate,
        "accumulation_reasons": accum_reasons,
    })
    return out
