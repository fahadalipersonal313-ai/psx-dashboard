"""Versioned target-before-stop replay and legacy benchmark-relative grading. The legacy functions retain their original label definition for inspection; ordinary outcome updates use the separate immutable opportunity ledger."""

import json
import logging
from datetime import datetime, timedelta

import pandas as pd

import config
import database as db
import data_fetcher
import technical_analyzer

log = logging.getLogger("backtester")


# ---------------------------------------------------------------------------
# Benchmark cache — real KMI30 EOD as the "market" for grading Avoid/Exit. Far
# more honest than the cohort-median proxy (which is the engine's own filtered
# universe), but the cohort median stays as a fallback when the index isn't
# fetchable (offline / 403). Cached once per process; never fabricated.
# ---------------------------------------------------------------------------
_BENCH_CACHE = {"eod": None, "tried": False}


def _benchmark_eod():
    if not _BENCH_CACHE["tried"]:
        try:
            import market_regime
            eod, _ = market_regime.fetch_index()
            _BENCH_CACHE["eod"] = eod
        except Exception as e:
            log.warning("Benchmark index fetch failed for grading: %s", e)
        _BENCH_CACHE["tried"] = True
    return _BENCH_CACHE["eod"]


def _benchmark_forward_move(start_date, days=3):
    """Real benchmark (KMI30) % move from start_date over `days` calendar days.
    None when the index data isn't reachable or doesn't span the window."""
    eod = _benchmark_eod()
    if eod is None or len(eod) == 0:
        return None
    eod_sorted = eod.sort_values("date")
    start = pd.Timestamp(start_date).normalize()
    sub0 = eod_sorted[eod_sorted["date"] >= start]
    if sub0.empty:
        return None
    p0 = float(sub0["close"].iloc[0])
    sub1 = eod_sorted[eod_sorted["date"] >= start + pd.Timedelta(days=days)]
    if sub1.empty:
        return None
    p1 = float(sub1["close"].iloc[0])
    return (p1 / p0 - 1) * 100


# ---------------------------------------------------------------------------
# Outcome tracking
# ---------------------------------------------------------------------------
def _price_on_or_after(eod, when):
    # EOD timestamps sit at midnight; run_time carries an intraday time. Without
    # normalising, "3 days after a 15:17 run" skips that day's midnight EOD and
    # lands a session late. Compare on calendar date so the horizon is exact.
    sub = eod[eod["date"] >= pd.Timestamp(when).normalize()]
    return float(sub["close"].iloc[0]) if len(sub) else None


def update_outcomes():
    """Fill pending forward prices from real EOD data and grade signals."""
    pend = db.pending_outcomes()
    if not pend:
        return 0
    eod_cache = {}
    updated = 0
    for run in pend:
        sym = run["symbol"]
        if sym not in eod_cache:
            eod_cache[sym], _ = data_fetcher.fetch_eod(sym)
        eod = eod_cache[sym]
        if eod is None:
            continue
        t0 = datetime.fromisoformat(run["run_time"])
        for field, days in (("price_1d", 1), ("price_3d", 3), ("price_7d", 7)):
            if run[field] is None and datetime.now() >= t0 + timedelta(days=days):
                p = _price_on_or_after(eod, t0 + timedelta(days=days))
                if p:
                    db.update_outcome(run["id"], field, p)
                    run[field] = p
                    updated += 1
        # grade once 3-day price exists
        if run["outcome"] is None and run["price_3d"] is not None and run["price"]:
            _grade_and_attribute(run)
        # 7-day grade: a LEAD signal (early watch) needs room to play out, so it
        # is judged on the longer horizon. Stored separately — it never touches
        # `outcome`, the 3-day grade the Buy/Avoid stats are built on.
        if run["price_7d"] is not None and run["price"] and \
                _col(run, "outcome_7d") is None:
            db.update_outcome(run["id"], "outcome_7d",
                              "worked" if _beat_market_7d(run) else "failed")
    log.info("Outcome tracker updated %d fields", updated)
    return updated


def _col(run, name):
    """sqlite3.Row has no .get(); a column added by a later migration may be
    absent in an older row object."""
    try:
        return run[name]
    except (IndexError, KeyError):
        return None


def _beat_market_7d(run):
    """Did this name beat the market over 7 days? Same honest benchmark chain as
    the 3-day grade (real KMI30 -> cohort median -> 0). Used for the early-watch
    tier, whose whole point is lead time: 3 days is too short to judge it."""
    chg = (run["price_7d"] / run["price"] - 1) * 100
    market = _benchmark_forward_move(run["run_time"][:10], days=7)
    if market is None:
        market = db.cohort_forward_move(run["run_time"][:10],
                                        exclude_symbol=run["symbol"], days=7)
    return chg > (market if market is not None else 0.0)


def _signal_worked(run):
    """Did the signal call play out? Real, rule-based — no fabrication.

      * Buy/Strong Buy — BEAT the real benchmark (KMI30) 3-day forward move
                         without breaching the stop. Was an absolute ">1% in 3
                         days", which mostly measured the market: it scored 22%
                         against a 38% base rate for "any symbol rose >1%", so a
                         Buy could "fail" in a down market while still being the
                         right relative call (and vice versa). Same benchmark →
                         cohort-median → "did not fall" fallback chain as
                         Avoid/Exit, so Buy and Avoid are finally comparable.
      * Avoid/Exit     — RELATIVE to the REAL benchmark (KMI30) forward move:
                         the stock underperformed the actual market index. Falls
                         back to the cohort median (engine's own universe) when
                         the index isn't reachable, then to "did not rise" when
                         even the cohort is too thin to benchmark. Three honest
                         fallbacks — never fabricated.
      * Watch/Hold     — graded loosely: didn't lose more than 3%.
    """
    chg = (run["price_3d"] / run["price"] - 1) * 100
    sig = run["signal"]
    if sig in ("Buy", "Strong Buy", "Avoid", "Exit"):
        market = _benchmark_forward_move(run["run_time"][:10], days=3)
        if market is None:
            market = db.cohort_forward_move(run["run_time"][:10],
                                            exclude_symbol=run["symbol"])
        bar = market if market is not None else 0.0
        if sig in ("Avoid", "Exit"):
            return chg < bar
        # A Buy is right when it OUTPERFORMS the market and the stop held. The
        # stop condition stays absolute: a stopped-out trade is a real loss no
        # matter what the index did.
        return chg > bar and (run["stop_loss"] is None
                              or run["price_3d"] > run["stop_loss"])
    return chg > -3.0


def _grade_and_attribute(run):
    """Grade one run and credit/blame the sections + sub-indicators that drove it."""
    worked = _signal_worked(run)
    sym = run["symbol"]
    db.update_outcome(run["id"], "outcome", "worked" if worked else "failed")
    # credit/blame the dominant section (section-level)
    b = {"technical": run["technical_score"],
         "sentiment": run["sentiment_score"],
         "macro_news": run["macro_news_score"]}
    dominant = max(b, key=lambda k: b[k] or 0)
    db.bump_indicator(dominant, sym, worked)
    # sub-indicator attribution: each bullish flag earns a hit or miss
    # so scoring_engine can later boost/penalise per-indicator confidence.
    if run.get("tech_flags"):
        try:
            for ind, bullish in json.loads(run["tech_flags"]).items():
                if bullish is True:
                    db.bump_indicator(f"tech_{ind}", sym, worked)
        except Exception:
            pass
    return worked


def regrade_all():
    """One-time / maintenance: re-grade EVERY completed run under the current
    rules and rebuild indicator_accuracy from scratch. Needed whenever the
    grading logic changes (e.g. Avoid moving from absolute to relative), since
    update_outcomes only ever grades rows whose outcome is still NULL."""
    db.reset_indicator_accuracy()
    runs = db.gradeable_runs()
    flipped = 0
    for run in runs:
        before = run["outcome"]
        worked = _grade_and_attribute(run)
        if before is not None and before != ("worked" if worked else "failed"):
            flipped += 1
    log.info("Re-graded %d runs (%d outcomes changed)", len(runs), flipped)
    return {"regraded": len(runs), "flipped": flipped}


# Versioned replay replaces the old close-only simulation. Legacy grading is
# retained under explicit names; it must not consume versioned decisions.
legacy_update_outcomes = update_outcomes
from swing_evaluation import backtest, backtest_portfolio, update_outcomes
