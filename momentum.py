"""momentum.py — Momentum-burst detector.

A burst is one session that breaks out of a stock's own recent behaviour: a
large single-day advance carried by volume well above its 20-day norm. It is
computed from `daily_ohlc` (real H/L/C bars), so it needs no extra fetch and
adds no write path to `full_run`.

MEASURED before it was built, and re-measured when the thresholds were loosened
(2026-08-17, day-deduped, forward move vs the same-day cohort median,
independence-checked via measure.py):

    >=3.0% & 1.5x   3d  n=42  beat 83.3%  +2.54%  |  7d  n=35  beat 71.4%  +1.92%
    >=2.0% & 1.3x   3d  n=66  beat 80.3%  +2.14%  |  7d  n=53  beat 71.7%  +2.13%

The looser trigger (now the default, for earlier entry) gives up ~3 points of
3-day accuracy but is identical at 7 days with a better median excess, on 57%
more signals. All four cohorts pass independence on both horizons.

Both horizons agree and neither is one sector's rally, which is more than can be
said for most ideas tried on this data (score velocity 45%, OBV divergence
negative, accumulation heuristics no edge).

The stricter variant that ALSO requires a 20-day closing high scored 91.3% at
3 days, but its 7-day sample is 16 rows and 69% one sector — so `at_high` is
carried as a TAG, not part of the trigger. Do not promote it without more data.

This is a WATCH tier, not a signal. It never becomes a Buy: the audit in
CLAUDE.md showed the gate layer selecting the worst subset of a pool that had
edge, so new findings are surfaced, not wired into the score.
"""

import datetime
import logging

import config
import database as db

log = logging.getLogger("momentum")

# Tunable in config.MOMENTUM_BURST so the trigger can be re-measured and moved
# without editing code. Defaults match the config values shipped 2026-08-17.
_CFG = getattr(config, "MOMENTUM_BURST", {}) or {}
MIN_GAIN_PCT = _CFG.get("min_gain_pct", 2.0)
MIN_VOL_MULT = _CFG.get("min_vol_mult", 1.3)
LOOKBACK = int(_CFG.get("lookback", 20))
MIN_SESSION_FRACTION = _CFG.get("min_session_fraction", 0.10)
# Liquidity floor. config.MIN_TURNOVER_PKR already existed but was never applied
# here, and it matters more since the universe grew from 50 to 168: on
# 2026-09-08 the panel surfaced FTMM (+9.98% on a 302,798 PKR median daily
# turnover — roughly USD 1,000 a day) and MERIT (634,858). A double-digit move
# on a name that trades nothing is a print, not a burst, and cannot be acted on.
MIN_TURNOVER = _CFG.get("min_turnover_pkr",
                        getattr(config, "MIN_TURNOVER_PKR", 5_000_000))


# Pakistan is UTC+5 year-round (no DST). Do NOT use datetime.now(): this code
# runs on GitHub runners (TZ=Asia/Karachi), on Streamlit Cloud (UTC) and in
# sandboxes (UTC), and a UTC clock puts every PSX session time before the open,
# which silently zeroed the intraday volume curve.
PKT = datetime.timezone(datetime.timedelta(hours=5))


def pkt_now():
    return datetime.datetime.now(PKT)


def session_fraction(now=None):
    """Median share of a session's FINAL volume that has traded by `now`,
    from config.INTRADAY_VOLUME_CURVE. 1.0 outside the session.

    Without this the detector compared volume-so-far against a whole-day
    average, so mid-session everything read 0.2-0.3x and no burst could fire
    while the market was open."""
    curve = getattr(config, "INTRADAY_VOLUME_CURVE", None)
    if not curve:
        return 1.0
    now = now or pkt_now()
    m = now.hour * 60 + now.minute
    if m <= curve[0][0]:
        return 0.0
    if m >= curve[-1][0]:
        return 1.0
    for (m0, f0), (m1, f1) in zip(curve, curve[1:]):
        if m0 <= m <= m1:
            span = (m1 - m0) or 1
            return f0 + (f1 - f0) * (m - m0) / span
    return 1.0


def _measured_cohort():
    """The symbols the 2026-08-17 beat rates were actually measured on: the
    original curated universe, before the lower-price expansion.

    This was a bar-count test (>=250 bars) until 2026-09-10. That was a proxy for
    "banked long enough to have been in the old universe", and the five-year
    backfill destroyed it -- 153 symbols cleared 250 bars overnight, so the
    caption silently started claiming the measurement covered names it had never
    seen. Read the curated list itself instead; it cannot drift.
    """
    names = (list(getattr(config, "DEFAULT_STOCKS", []))
             + list(getattr(config, "ADDITIONAL_STOCKS", []))
             + list(getattr(config, "EXTRA_STOCKS", [])))
    return set(names)


def _series(symbol, limit=60):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT date, close, volume FROM daily_ohlc
               WHERE symbol=? ORDER BY date DESC LIMIT ?""", (symbol, limit))]
    return list(reversed(rows))


def detect(symbol):
    """Burst dict for the most recent bar, or None. Never raises on thin data."""
    s = _series(symbol)
    if len(s) < LOOKBACK + 2:
        return None
    last, prev = s[-1], s[-2]
    if not (last["close"] and prev["close"] and last["volume"]):
        return None
    gain = (last["close"] / prev["close"] - 1) * 100
    window = s[-(LOOKBACK + 1):-1]
    vols = [b["volume"] for b in window if b["volume"]]
    if not vols:
        return None
    vavg = sum(vols) / len(vols)
    # Median, not mean: one frantic day in an otherwise dead name must not lift
    # it over the floor.
    turnovers = sorted(b["volume"] * b["close"] for b in window
                       if b["volume"] and b["close"])
    turnover = (turnovers[len(turnovers) // 2] if turnovers else 0.0)
    if MIN_TURNOVER and turnover < MIN_TURNOVER:
        return None
    # Compare like with like: a partial session's volume against the share of an
    # average day that would normally have traded by now.
    today = pkt_now().date().isoformat()
    frac = session_fraction() if last["date"] == today else 1.0
    partial = frac < 1.0
    if partial and frac < MIN_SESSION_FRACTION:
        return None                      # denominator too small to be meaningful
    expected = vavg * frac if frac else vavg
    mult = last["volume"] / expected if expected else 0
    if gain < MIN_GAIN_PCT or mult < MIN_VOL_MULT:
        return None
    highs = [b["close"] for b in window if b["close"]]
    return {"symbol": symbol, "date": last["date"], "gain_pct": gain,
            "vol_mult": mult, "close": last["close"], "turnover": turnover,
            # The 2026-08-17 beat rates were measured on the ORIGINAL 50-symbol
            # universe. A name added in the 168-symbol expansion was never in
            # that cohort, so the statistic does not describe it; the dashboard
            # scopes its claim on this flag rather than implying it does.
            "in_measured_cohort": symbol in _measured_cohort(),
            # True while the session is still open: the move can still fade, and
            # the measured beat rates below describe the END-OF-DAY trigger.
            "provisional": partial,
            "session_pct": round(frac * 100),
            # Stronger at 3 days but unconfirmed at 7 — a tag, not a trigger.
            "at_high": bool(highs and last["close"] >= max(highs))}


def scan(symbols=None):
    """Bursts across the universe, strongest advance first."""
    out = []
    for sym in (symbols or config.STOCKS):
        try:
            b = detect(sym)
        except Exception as e:                      # never break a caller
            log.warning("burst scan failed for %s: %s", sym, e)
            continue
        if b:
            latest = db.last_run(sym)
            b["signal"] = (latest or {}).get("signal")
            b["final_score"] = (latest or {}).get("final_score")
            b["sector"] = config.SECTORS.get(sym, "")
            out.append(b)
    return sorted(out, key=lambda x: -x["gain_pct"])
