"""
config.py — Central configuration for the PSX Shariah-Compliant Analysis Engine.

EDIT THIS FILE to change stocks, weights, risk rules, and data sources.
Never hard-code values elsewhere; all modules read from here.
"""

import os
import re as _re
import json as _json
from pathlib import Path as _Path

# ---------------------------------------------------------------------------
# 1. STOCK UNIVERSE
# ---------------------------------------------------------------------------
# Default 4 stocks requested by the user:
DEFAULT_STOCKS = ["PSO", "TREET", "FABL", "AIRLINK"]

# 6 additional candidates — chosen ONLY from the officially verified KMI-30
# constituent list (see SHARIAH section below). Diversified across sectors.
# GAL added to complete the full 30 official KMI-30 names (was previously
# missing even though it's in KMI30_VERIFIED).
ADDITIONAL_STOCKS = ["MEBL", "SYS", "LUCK", "FFC", "NRL", "DGKC",
    "OGDC", "PPL", "MARI", "HUBC", "ENGROH", "EFERT", "FCCL", "MLCF",
    "NML", "PAEL", "SEARL", "HCAR", "PRL", "ATRL", "SNGP", "SSGC",
    "SAZEW", "FFL", "CPHL", "GHNI", "GAL"]

# Broader KMI All-Share (Shariah) names — added 2026-06-18, each verified IN the
# official PSX-KMI All Share Islamic Index recomposition (screening 2025-12-31,
# effective 2026-06-05). These are compliant via KMIALLSHR_VERIFIED below (not
# necessarily KMI-30). User-requested batch.
KMIALLSHR_STOCKS = ["KEL", "PIBTL", "TELE", "DCL", "GGL", "BNL", "ILP", "FCL",
    "JVDC", "AGP", "WAVES", "TOMCL", "IMAGE", "SYM", "FCEPL", "KOHC"]
ADDITIONAL_STOCKS += KMIALLSHR_STOCKS

# Extra names beyond the strict KMI-30, compliant via the OTHER_COMPLIANT
# route below (each entry there carries a source + verify note).
EXTRA_STOCKS = ["SLM", "SLGL", "THCCL"]

STOCKS = DEFAULT_STOCKS + ADDITIONAL_STOCKS + EXTRA_STOCKS

SECTORS = {
    "PSO": "Oil Marketing", "TREET": "Diversified/Consumer", "FABL": "Islamic Banking",
    "AIRLINK": "Technology/Telecom Devices", "MEBL": "Islamic Banking",
    "SYS": "Technology/IT Exports", "LUCK": "Cement/Conglomerate",
    "FFC": "Fertilizer", "OGDC": "Oil & Gas Exploration", "MARI": "Oil & Gas Exploration",
    "NRL": "Refinery", "DGKC": "Cement",
    "PPL": "Oil & Gas Exploration", "HUBC": "Power Generation",
    "ENGROH": "Conglomerate", "EFERT": "Fertilizer", "FCCL": "Cement",
    "MLCF": "Cement", "NML": "Textile", "PAEL": "Electrical Goods",
    "SEARL": "Pharmaceuticals", "HCAR": "Auto Assembler", "PRL": "Refinery",
    "ATRL": "Refinery", "SNGP": "Gas Distribution", "SSGC": "Gas Distribution",
    "SAZEW": "Auto Assembler", "FFL": "Food", "CPHL": "Pharmaceuticals",
    "GHNI": "Glass/Holding", "GAL": "Textile/Synthetic Fibre",
    "SLM": "Tyre Manufacturing", "SLGL": "Logistics/Transport",
    "THCCL": "Cement",
    # KMI All-Share batch (2026-06-18)
    "KEL": "Power Generation", "PIBTL": "Logistics/Ports",
    "TELE": "Technology/Telecom", "DCL": "Cement", "GGL": "Glass/Holding",
    "BNL": "Food", "ILP": "Textile", "FCL": "Electrical Goods",
    "JVDC": "Real Estate", "AGP": "Pharmaceuticals", "WAVES": "Electrical Goods",
    "TOMCL": "Food", "IMAGE": "Textile", "SYM": "Technology/IT",
    "FCEPL": "Food", "KOHC": "Cement"}

# ---------------------------------------------------------------------------
# 2. SHARIAH COMPLIANCE — VERIFIED SOURCE OF TRUTH
# ---------------------------------------------------------------------------
# Official KMI-30 constituents per PSX re-composition notification,
# screening date 2025-12-31, effective 2026-05-25.
# Source: PSX notification (dps.psx.com.pk/download/attachment/277332-1.pdf),
# reported by Mettis Global, 2026-05-18.
# IMPORTANT: KMI-30 is recomposed semi-annually. Re-verify this list every
# 6 months. The engine warns when the verification date is older than
# SHARIAH_STALE_DAYS.
KMI30_VERIFIED = {
    "AIRLINK", "ATRL", "CPHL", "DGKC", "EFERT", "ENGROH", "FCCL", "FFC", "FFL",
    "GAL", "GHNI", "HCAR", "HUBC", "LUCK", "MARI", "MEBL", "MLCF", "NML", "NRL",
    "OGDC", "PAEL", "PPL", "PRL", "PSO", "SAZEW", "SEARL", "SNGP", "SSGC",
    "SYS", "TREET",
}
KMI30_VERIFICATION_DATE = "2026-05-25"   # effective date of recomposition
KMI30_SOURCE = "PSX KMI-30 recomposition notice (screening 2025-12-31)"
SHARIAH_STALE_DAYS = 200  # warn if verification older than this

# Broader KMI All-Share (Shariah) constituents — every symbol here was confirmed
# present and "Compliant" in the official PSX-KMI All Share Islamic Index
# recomposition notice (screening accounts 2025-12-31, effective 2026-06-05).
# These are shariah-compliant and ELIGIBLE for ranking even though they are not
# in the tighter KMI-30. Re-verify at the next recomposition (semi-annual).
# Source: psx.com.pk KMI-ALL-Share-Recomposition-Notice.pdf (verified 2026-06-18).
KMIALLSHR_VERIFIED = {
    "KEL", "PIBTL", "TELE", "DCL", "GGL", "BNL", "ILP", "FCL", "JVDC", "AGP",
    "WAVES", "TOMCL", "IMAGE", "SYM", "FCEPL", "KOHC",
}
KMIALLSHR_VERIFICATION_DATE = "2026-06-05"   # effective date of recomposition
KMIALLSHR_SOURCE = "PSX-KMI All Share recomposition notice (screening 2025-12-31)"

# Dated official membership and closing-price evidence for the user's additions.
_lower_price = _json.loads((_Path(__file__).parent / 'data_lower_price_stocks.json').read_text(encoding='utf-8'))
LOWER_PRICE_VERIFIED = {r['symbol'].split()[0] for r in _lower_price['stocks']}
LOWER_PRICE_VERIFIED_ON = _lower_price['verified_on']
LOWER_PRICE_SOURCE = _lower_price['source']
# Curated universe (2026-09-09, user instruction "cut to 60"): the 50 names above
# plus 10 screened additions, instead of every verified lower-price symbol.
#
# The 10 were selected AFTER the 2026-09-08 five-year backfill, so each was
# ranked on real history rather than the ~65 bars the expansion names had: at
# least 220 bars (so EMA200 is genuine), median 20-day turnover above
# MIN_TURNOVER_PKR, a scored signal rather than "No data", then ordered by
# trend quality (above the 20/40/200 EMAs with a rising 40) and score, capped at
# two per sector so the set does not pile into one theme.
#
# LOWER_PRICE_VERIFIED is left intact — it is the dated PSX membership and price
# evidence, not a switch — and dropping a symbol here does NOT delete its banked
# bars in daily_ohlc, so re-adding any name is immediate and lossless.
CURATED_ADDITIONS = ["IPAK", "ASL", "DCR", "GHGL", "POWER",
                     "CNERGY", "FTSM", "LOADS", "FIBLM", "EPCL"]
STOCKS = list(dict.fromkeys(
    STOCKS + [s for s in CURATED_ADDITIONS if s in LOWER_PRICE_VERIFIED]))
for _row in _lower_price['stocks']:
    SECTORS.setdefault(_row['symbol'].split()[0], 'PSX sector ' + _row['sector'])
# Official PSX /symbols directory, retrieved 2026-09-05; same sector grouping
# for both the original names and additions so exposure limits stay consistent.
SECTORS.update(_json.loads((_Path(__file__).parent / 'data_stock_sectors.json').read_text(encoding='utf-8')))

# Stocks compliant via another verified route (not in KMI-30 top-30 ranking
# but shariah compliant per company structure). Each entry MUST carry a
# reason and a manual re-check note. Anything not in KMI30_VERIFIED or here
# is marked "Needs manual verification" and EXCLUDED from the top-10 ranking.
OTHER_COMPLIANT = {
    "FABL": {
        "reason": ("Faysal Bank converted to a full-fledged Islamic bank "
                   "(conversion completed Jan 2023); operates under SBP Islamic "
                   "banking licence."),
        "verify_note": ("Confirm continued inclusion in PSX KMI All Share Index "
                        "and SECP shariah-compliant securities list each quarter."),
    },
    "SLM": {
        "reason": ("Service Long March Tyres Ltd. — deemed Shariah compliant under "
                   "the KMI All Share Index screening criteria and included in the "
                   "PSX-KMI All Share Islamic Index on listing (PSX, June 2026)."),
        "verify_note": ("Newly listed (15 Jun 2026) — confirm continued inclusion in "
                        "the PSX-KMI All Share Islamic Index each semi-annual "
                        "recomposition."),
    },
    "SLGL": {
        "reason": ("Secure Logistics-Trax Group Ltd. — reported as Shariah "
                   "compliant per PSX documentation (transport/logistics sector, "
                   "no interest-based core business)."),
        "verify_note": ("Source was a secondary aggregator, not the primary PSX "
                        "KMI All Share notice PDF — confirm against the latest "
                        "PSX-KMI All Share Islamic Index recomposition notice "
                        "before relying on this."),
    },
    "THCCL": {
        "reason": ("Thatta Cement Company Ltd. — cement sector, a sector where "
                   "most PSX names pass KMI screening (cf. DGKC/FCCL/MLCF already "
                   "in KMI30_VERIFIED)."),
        "verify_note": ("Not independently confirmed against the primary PSX-KMI "
                        "All Share Islamic Index notice — verify before relying on "
                        "this for trading decisions."),
    },
}

# ---------------------------------------------------------------------------
# 3. SCORING WEIGHTS (fixed per spec; change only deliberately)
# ---------------------------------------------------------------------------
# Score stays 100% technical as the BASE. News re-enabled 2026-08-25 on the
# user's explicit instruction, but as a bounded MODIFIER, not a blended weight.
#
# Why not a weight: the macro section is anchored at neutral 50 while a good
# technical score is 74-85, so blending it in drags every score DOWN. Measured
# at macro_news=0.30: a technical 78 with NO news scored 69.6 — under the 75
# Buy threshold — and even perfect causal-positive news only reached 73.8. A
# weight would have silently muted almost every Buy on the board. The modifier
# below is what "news moves the score" has to mean here: no news = no change.
WEIGHTS = {"technical": 1.0, "fundamentals": 0.0,
           "macro_news": 0.0, "sentiment": 0.0}

# Max points news can add to or subtract from the technical score. A rating of
# 90 (causal, highly positive, full confidence) gives +8; 10 gives -8; anything
# damped to neutral 50 by causality/confidence gives exactly 0. 8 points is
# deliberately enough to matter (it can lift a 74 into the Buy band or push a
# 78 out of it) without letting a headline overwhelm price action.
#
# UNMEASURED as shipped. CLAUDE.md's standing rule is to measure against the
# candidate pool first, and causality-tagged news has no graded outcomes yet.
# Run `python main.py measure` after ~2 weeks; if news-adjusted Buys do not
# beat pure-technical ones, set NEWS_SCORE_ADJUST_MAX = 0.0 to switch it off.
NEWS_SCORE_ADJUST_MAX = 0.0

# Sector news gets its own, smaller cap (2026-08-25, user's request: "10% or
# more depending whether causation or correlation"). A sector story moves every
# peer at once, so it is a weaker per-symbol claim than company-specific news —
# hence 4 points against company news's 8. Causality does the rest of the work:
# news_feed.sector_news_score() already scales by causality x confidence, so a
# CAUSAL sector call (a refining-margin policy) reaches the full 4 while a
# merely CORRELATED one is damped to ~35% of it and sector noise contributes 0.
# Combined ceiling is therefore 12 points, and the two are summed then clamped.
SECTOR_NEWS_SCORE_ADJUST_MAX = 0.0

# Buy threshold raised 70 -> 75 (2026-08-12 audit of day-deduped graded runs).
# Score band vs 3-day win rate: 70-75 won 30% (n=66), 75-80 won 68% (n=28),
# 80+ won 86% (n=7). Two-thirds of Buys were coming from the WORST band. At
# score>=75 the candidate win rate is 75% (n=81) vs 56% at >=70 (n=198) — fewer
# signals, materially better ones.
SIGNAL_THRESHOLDS = {   # final score -> base signal (before risk overrides)
    "strong_buy": 80, "buy": 75, "watch": 60, "hold": 50,
}

# Hysteresis dead-band around the SIGNAL_THRESHOLDS. A raw score grazing a
# threshold (e.g. 69.5 vs 70.5) shouldn't flip Buy↔Watch run-to-run — that's
# scoring noise, not signal. Once a stock is at level X, its final_score must
# cross the next threshold by at least this many points BEFORE flipping. Same
# pattern as the existing conviction-streak gate (which requires Strong Buy to
# confirm), but applied to band edges. Set to 0 to disable.
HYSTERESIS_BAND = 2

# Evidence-based Buy gates. Re-measured 2026-08-12 on day-deduped graded runs
# (15-min polling inflates raw counts ~20x, so every figure here is one row per
# symbol per day):
#   * The pullback-entry UPGRADE is REMOVED. Buys it created (final_score <70)
#     won 9% (n=57) against a 38% market base rate — it was subtracting edge,
#     not adding it. The pullback SETUP is still detected and displayed
#     (technical['pullback_ready'] + the buy-zone) as manual context.
#   * RS laggard veto raised 45 -> 55. RS<45 won 21%, 45-55 won 21%, 55-70 won
#     24%, 70+ won 36%. A 70 cut looks best alone, but stacked on the new
#     score>=75 threshold it gives NO accuracy gain (75% either way) while
#     halving trade count (n=81 -> 40); at 55 the stack wins 77% (n=53).
# RS=None (index unavailable) never vetoes: missing data must not block trades.
RS_LAGGARD_VETO = 55

# Money-flow confirmation for a Buy. Threshold moved 0.0 -> -0.15 on 2026-09-04
# after auditing it against the CANDIDATE POOL over 5 years of adjusted,
# tradeable history — the test that catches this repo's recurring failure, where
# a veto rejects a better subset than it passes.
#
# At 0.0 the veto rejected HALF the pool to capture almost nothing. The sweep is
# monotonic: a tighter cut rejects far fewer candidates AND captures more.
#   threshold   rejects   % of pool   5d diff   10d diff
#     0.00      12,824      50.1%     -0.07pp   -0.16pp
#    -0.10       6,655      26.0%     -0.17pp   -0.32pp
#    -0.15       4,276      16.7%     -0.30pp   -0.46pp
#    -0.20       2,704      10.6%     -0.37pp   -0.57pp
# -0.15 rejects a THIRD as many candidates while capturing FOUR TIMES the
# effect. All the information sits below -0.15; everything between -0.15 and
# +0.15 measured as noise (5d medians of 0.00% across four buckets).
#
# Independence at -0.15: n=4,276, 49 symbols, 25 sectors, top symbol 4%.
# Year split: 2024 -0.29pp, 2025 -0.56pp, 2026 -0.32pp — the three most recent
# years agree and the LIVE regime points the right way, which is exactly what
# the 15-40% extension zone failed (it flipped positive in 2026 and was
# rejected). Caveat kept: 2022 (+0.06pp) and 2023 (-0.01pp) show nothing, so the
# effect may be regime-dependent rather than permanent — re-run the year split
# before tightening it further.
#
# SUPERSEDES the 2026-08-13 note that credited CMF>0 with lifting the beat rate
# 70% -> 83% on n=56. That measurement compared the filtered subset against a
# baseline rather than asking whether the REJECTED days were worse than the KEPT
# ones, and on the larger, cleaner sample the cut at zero does not survive.
# CMF=None (not computable) never vetoes: missing data must not block a trade.
BUY_MIN_CMF = -0.15

# ---------------------------------------------------------------------------
# 3z. EARLY WATCH — lead time before a name reaches the Buy band (2026-08-13)
# ---------------------------------------------------------------------------
# Goal: flag a stock while it is still BUILDING, so there is time to prepare,
# instead of only after the score confirms and the move is under way.
#
# What the graded history actually supports (7-day forward vs same-day cohort
# median, day-deduped; 50% = no skill):
#   * CMF > 0.10 .................. 61% beat, +2.07% excess (n=62)  <- the ONLY
#     leading indicator with edge; it reads real high/low money flow.
#   * CMF > 0.10 inside score 60-75 ... 75% beat, +2.70% (n=16, small)
# What it does NOT support (measured, rejected — do not resurrect without new
# evidence):
#   * accumulation_candidate ...... 47% beat at 3d, 53% at 7d (no edge)
#   * OBV bullish divergence ...... 44% / 45% (NEGATIVE)
#   * OBV up while price flat ..... 40% / 37% (NEGATIVE)
#   * score velocity (3-day rise) . 45% when rising fast (NEGATIVE)
#
# Early watch is NOT a Buy and never becomes one on its own: it is a separate,
# clearly-labelled monitoring tier that leaves the validated Buy stack alone.
# It is graded on the 7-DAY horizon (a lead signal needs room to play out), so
# in a few weeks there will be real evidence for or against it.
EARLY_WATCH_ENABLED = True
EARLY_WATCH_MIN_CMF = 0.10      # real-H/L money flow; the one indicator with edge
EARLY_WATCH_SCORE_BAND = (55, 75)   # below the Buy band — the "building" zone
EARLY_WATCH_MIN_RS = 45         # don't flag names the whole market is beating

# ---------------------------------------------------------------------------
# 3a. PURE-TECHNICAL MODE (2026-08-12, user-directed risk-up)
# ---------------------------------------------------------------------------
# The score has been 100% technical since 2026-07-15, but news/sentiment could
# still MOVE a signal through the risk_manager `bad_news` / `manipulation_risk`
# vetoes. With PURE_TECHNICAL the engine's decisions come from price/volume
# only: those two vetoes are still WARNED about (and shown in the dashboard for
# manual cross-verification) but no longer downgrade Buy -> Watch, and they no
# longer count toward the High risk level. Shariah, breakdown, poor_rr, earnings
# and regime gates are unaffected — those are structural, not news-derived.
PURE_TECHNICAL = True

# Overextension ("chase") guard. It stepped a Buy down one notch whenever price
# ran far above the reference EMA. Disabled 2026-08-12 by user request to accept
# more risk: the engine no longer refuses to buy strength. technical['extended']
# is still computed and displayed, and still gates the pullback/accumulation
# tags — only the SIGNAL downgrade is off.
CHASE_GUARD_ENABLED = False

# Thin-headroom (poor_rr) downgrade. Measured 2026-08-17 on day-deduped stored
# rows vs the same-day cohort median, with independence checks (measure.py):
#
#   blocked by poor_rr            n=47  beat 68.1%  median excess +1.73%  (3d)
#   blocked by poor_rr, RS>=55    n=31  beat 77.4%  median excess +2.12%  (3d)
#   emitted Buys                  n=21  beat 52.4%  median excess +0.41%  (3d)
#
# 7d agrees (blocked 65.5%, +1.60%); all three cohorts span 11-17 symbols and
# 8-11 sectors, so this is not one sector's rally counted many times. The veto
# was the most active in the system (48 of 98 score>=75 candidates) and was
# rejecting a BETTER subset than it passed.
#
# Cause: headroom is measured to overhead resistance, and a leader making new
# highs has none by construction — the same "penalise strength" flaw as the
# chase guard. headroom_rr is still computed, still emitted as a warning and
# still shown on cards; only the Buy->Watch downgrade is off. Flip to True to
# restore.
POOR_RR_VETO_ENABLED = False

# User turned this off 2026-08-24: no portfolio-based downgrades. The
# `concentrated` veto used to downgrade a fresh Buy to Watch whenever the
# symbol was already above `RISK["max_existing_concentration_pct"]` of the
# real book. When False, neither the veto nor its warning line fires; the
# dashboard's focus-brief panel is also suppressed. Flip back to True to
# restore the guard.
CONCENTRATION_VETO_ENABLED = False

# Momentum burst (momentum.py) — one session breaking out of a stock's own norm.
# Loosened 2026-08-17 from 3.0%/1.5x to catch the move earlier. Measured both
# ways, day-deduped vs the same-day cohort median, independence-checked:
#
#   >=3.0% & 1.5x   3d n=42 beat 83.3% +2.54%  |  7d n=35 beat 71.4% +1.92%
#   >=2.0% & 1.3x   3d n=66 beat 80.3% +2.14%  |  7d n=53 beat 71.7% +2.13%
#
# The looser setting gives up ~3 points of 3-day accuracy but is IDENTICAL at
# 7 days (71.7% vs 71.4%) with a BETTER median excess, on 57% more signals. Both
# pass independence on both horizons. Earlier entry costs little here.
MOMENTUM_BURST = {
    "min_gain_pct": 2.0,     # single-session advance
    "min_vol_mult": 1.3,     # vs the 20-day average volume
    "lookback": 20,          # window for the volume norm and the high tag
    # Below this share of the day's volume having traded, an intraday burst is
    # not reported at all: a tiny denominator makes the pace ratio explode on
    # noise. ~0.10 corresponds to roughly 09:50 PKT.
    "min_session_fraction": 0.10,
}

# Empirical intraday volume curve, minute-of-day (PKT) -> median share of the
# session's FINAL volume traded by then. Measured 2026-08-18 from 865 stored
# symbol-days (runs.volume is cumulative day volume at each 15-min cycle),
# restricted to the PKT-timestamped era — older rows were written in UTC.
#
# Why this exists: the burst detector compared volume-SO-FAR against a
# full-day 20-day average, so mid-session every stock looked like 0.2-0.3x and
# NO burst could ever fire while the market was open — the exact opposite of
# the "chase earlier" intent. It only worked after the close.
#
# The curve is markedly non-linear: 16.6% of the day's volume trades in the
# first 28 minutes. A naive elapsed-time pro-rata would understate expected
# early volume and fire on ordinary mornings.
INTRADAY_VOLUME_CURVE = [
    (572, 0.00),   # 09:32 open
    (600, 0.166),  # 10:00
    (630, 0.268), (660, 0.359), (690, 0.417), (720, 0.487),
    (750, 0.546), (780, 0.606), (810, 0.657), (840, 0.708),
    (870, 0.799), (900, 0.905),
    (930, 1.00),   # 15:30 close
]

# Reference EMA for the pullback buy-zone and the extension (ext_pct) measure.
# The short-window contract uses the 20-session trend line for pullback entries.
# Must be one of the EMAs technical_analyzer computes (20 or 40).
PULLBACK_EMA_SPAN = 20

# ---------------------------------------------------------------------------
# 3b. MARKET REGIME & RELATIVE STRENGTH (Tier 2)
# ---------------------------------------------------------------------------
# Benchmark index for the regime gate + relative-strength ranking. PSX DPS
# serves index EOD at the same /timeseries/eod/{symbol} endpoint as stocks.
# KMI30 = the Shariah index matching this engine's universe (KSE100 = broad
# market). Confirmed live 2026-06-14: KSE100, KMI30, KSE30, ALLSHR, KMIALLSHR.
BENCHMARK_INDEX = "KMI30"
REGIME_EMA_SPAN = 40           # fits the 42-session contract while retaining a slow trend filter
REGIME_GATE_ENABLED = False    # market direction is a warning; stock-specific checks remain
# Relative strength: stock return minus index return over these trading-day
# windows, blended across roughly two weeks, one month, and two months.
RS_LOOKBACKS = {"2w": 10, "1m": 21, "2m": 41}
RS_WEIGHTS = {"2w": 0.25, "1m": 0.40, "2m": 0.35}
RS_POINTS = 15                 # relative strength's contribution to the technical score
# True ATR/ADX activate once this many REAL daily OHLC bars (banked from intraday
# H/L) exist for a symbol; below this the engine uses the close-based proxies.
# Banking started ~2026-06-12, so true values switch on automatically in early July.
MIN_OHLC_BARS_FOR_TRUE = 16

# Fundamentals table (manually maintained — the engine NEVER invents these).
# Fill per symbol from the latest audited quarterly/annual report. Any symbol
# left out scores a neutral 50 and is flagged low-confidence (see
# fundamentals_analyzer.py). Keys (all optional): pe, eps_growth (%), roe (%),
# de (debt/equity), div_yield (%).
FUNDAMENTALS_AS_OF = ""   # e.g. "2026-03-31 (Q3 FY26)"; blank = not yet filled
FUNDAMENTALS = {
    # "PSO": {"pe": 4.2, "eps_growth": 12, "roe": 18, "de": 0.6, "div_yield": 7},
}

# ---------------------------------------------------------------------------
# 4. RISK MANAGEMENT
# ---------------------------------------------------------------------------
RISK = {
    "max_risk_per_trade_pct": 1.5,     # % of total capital risked per trade
    "max_position_pct": 15.0,          # never put more than this % in one stock
    # Concentration cap measured against the REAL book (portfolio.json), not the
    # hypothetical new trade. Per-trade sizing is blind to what you already hold,
    # so a name that is already most of the account kept producing clean Buys.
    # Above this weight a fresh Buy is downgraded to Watch (`concentrated` veto);
    # trimming/holding is still fine, only ADDING is blocked.
    "max_existing_concentration_pct": 25.0,
    "min_risk_reward": 2.0,            # reject setups below 2:1 (projected-target R:R)
    "min_headroom_rr": 1.5,            # real room-to-resistance : risk; below -> thin
                                       # upside (price jammed under a ceiling) -> Watch
    "min_headroom_rr_riskon_floor": 1.1,# FLOOR for the risk-on relaxation of the
                                       # headroom-RR threshold. In a confirmed bull
                                       # most stocks sit close to recent highs (price
                                       # near "resistance" is the leadership default),
                                       # so requiring 1.5x headroom would mute the
                                       # whole leadership group. Threshold ramps DOWN
                                       # from min_headroom_rr (neutral / flat tape) to
                                       # this floor (strong rally). Set to 1.5 to
                                       # disable risk-on relaxation.
    "headroom_rr_riskon_full_pct": 8.0, # Rally strength (benchmark % above its regime EMA)
                                       # at which the headroom threshold reaches its
                                       # floor. Linear ramp between the two.
    "max_extension_pct": 11.0,         # price > this % above EMA20 -> extended (chase).
                                       # %-based, not ATR: the EOD ATR proxy understates
                                       # true range, which inflated ATR-normalised distance.
    "max_extension_momentum_pct": 22.0,# 20-day momentum above this% -> parabolic/extended
    "extension_riskon_multiplier": 1.8,# CEILING for the risk-on chase-guard widening.
                                       # In a confirmed risk-on rally "above EMA20" is
                                       # the market's DEFAULT state, so the chase guard
                                       # widens UP TO this factor (≈20% above EMA20 /
                                       # ≈40% 20-day momentum). The actual widening scales
                                       # with rally strength (see _full_pct below). Set to
                                       # 1.0 to keep the guard regime-neutral (old behaviour).
    "extension_riskon_full_pct": 8.0,  # Rally strength (benchmark % above its regime EMA) at
                                       # which the chase guard reaches its full widening.
                                       # The multiplier ramps linearly from 1.0 when the
                                       # index just crosses above its EMA (mild rally) to
                                       # the ceiling above when the index is this far above
                                       # it (strong, confirmed bull) — so a shaky breakout
                                       # loosens the guard only a little, a powerful trend
                                       # loosens it fully.
    "default_stop_atr_mult": 2.0,      # stop loss = entry - 2*ATR (or support)
    "min_avg_daily_volume": 100_000,   # below this -> illiquid warning
    "max_volatility_pct": 6.0,         # daily ATR% above this -> high risk
    "no_leverage": True,
    "manual_confirmation_required": True,
}

# ---------------------------------------------------------------------------
# 4b. PORTFOLIO-LEVEL RISK (Tier 2 #9)
# ---------------------------------------------------------------------------
# Per-trade sizing (above) caps the damage from ONE position. These caps apply
# ACROSS every open/recommended Buy at once, because the real account-killer is
# correlated risk: ten "safe" 1.5% trades that all gap down together, or a book
# that is 80% cement. The engine sizes each Buy, then admits them greedily by
# score until a cap binds — the rest are flagged "defer", never silently dropped.
PORTFOLIO_RISK = {
    "max_portfolio_heat_pct": 6.0,    # total capital at risk if EVERY open stop fills at once
    "max_sector_exposure_pct": 30.0,  # max % of capital deployed into any one sector
    "max_open_positions": 8,          # practical cap on concurrent positions
}

# ---------------------------------------------------------------------------
# 4c. BACKTEST METRICS (Tier 2 #8)
# ---------------------------------------------------------------------------
# Active evaluation uses REPLAY_LOOKBACK and the frozen EXECUTION contract below.
# It reports individual opportunity outcomes, not portfolio drawdown or a
# walk-forward profitability estimate. Obsolete unused backtest settings removed.

# ---------------------------------------------------------------------------
# 5. DATA SOURCES (public, no login, no protection bypass)
# ---------------------------------------------------------------------------
PSX_DPS_BASE = "https://dps.psx.com.pk"
PSX_INTRADAY_URL = PSX_DPS_BASE + "/timeseries/int/{symbol}"
PSX_EOD_URL = PSX_DPS_BASE + "/timeseries/eod/{symbol}"
PSX_COMPANY_URL = PSX_DPS_BASE + "/company/{symbol}"

# Public RSS feeds for news + sentiment (respecting robots/ToS — RSS is
# explicitly published for consumption).
NEWS_FEEDS = [
    ("Business Recorder", "https://www.brecorder.com/feeds/latest-news"),
    ("Dawn Business", "https://www.dawn.com/feeds/business"),
    ("The News Business", "https://www.thenews.com.pk/rss/1/8"),
    ("Tribune Business", "https://tribune.com.pk/feed/business"),
    # Profit (profit.pakistantoday.com.pk/feed) and Mettis (mettisglobal.news/rss)
    # were removed 2026-06-11: both feed URLs now return HTTP 404.
]

REQUEST_TIMEOUT = 15
REQUEST_HEADERS = {"User-Agent": "PSX-Research-Engine/1.0 (personal research tool)"}

# Drop any news headline whose PUBLISH date is older than this many days, so
# stale/irrelevant articles can't pollute scoring (filters on real publish
# date, not fetch time).
NEWS_MAX_AGE_DAYS = 3

# Per-company PUBLIC news/sentiment via Google News RSS search (login-free,
# published for consumption). Each query is scoped to the company so the
# sentiment module gets real per-symbol mentions instead of market-wide noise.
GOOGLE_NEWS_RSS = ("https://news.google.com/rss/search?q={query}"
                   "+when:2d&hl=en-PK&gl=PK&ceid=PK:en")
COMPANY_NEWS_QUERY = {
    "PSO": "Pakistan State Oil",
    "TREET": "Treet Corporation Pakistan",
    "FABL": "Faysal Bank",
    "AIRLINK": "Air Link Communication Pakistan",
    "MEBL": "Meezan Bank",
    "SYS": "Systems Limited Pakistan",
    "LUCK": "Lucky Cement",
    "FFC": "Fauji Fertilizer Company",
    "NRL": "National Refinery Limited Pakistan",
    "DGKC": "DG Khan Cement",
    "OGDC": "Oil and Gas Development Company Pakistan",
    "MARI": "Mari Petroleum Energies","PPL": "Pakistan Petroleum", "HUBC": "Hub Power Company",
    "ENGROH": "Engro Holdings", "EFERT": "Engro Fertilizers",
    "FCCL": "Fauji Cement", "MLCF": "Maple Leaf Cement", "NML": "Nishat Mills",
    "PAEL": "Pak Elektron", "SEARL": "Searle Company Pakistan",
    "HCAR": "Honda Atlas Cars", "PRL": "Pakistan Refinery",
    "ATRL": "Attock Refinery", "SNGP": "Sui Northern Gas",
    "SSGC": "Sui Southern Gas", "SAZEW": "Sazgar Engineering",
    "FFL": "Fauji Foods", "CPHL": "Citi Pharma",
    # KMI All-Share batch (2026-06-18)
    "KEL": "K-Electric", "PIBTL": "Pakistan International Bulk Terminal",
    "TELE": "Telecard Pakistan", "DCL": "Dewan Cement",
    "GGL": "Ghani Global Holdings", "BNL": "Bunnys Limited Pakistan",
    "ILP": "Interloop Limited", "FCL": "Fast Cables Pakistan",
    "JVDC": "Javedan Corporation Naya Nazimabad", "AGP": "AGP Limited pharma Pakistan",
    "WAVES": "Waves Corporation Pakistan", "TOMCL": "The Organic Meat Company Pakistan",
    "IMAGE": "Image Pakistan Limited", "SYM": "Symmetry Group Pakistan",
    "FCEPL": "FrieslandCampina Engro Foods", "KOHC": "Kohat Cement"
}

# Relevance anchors — a fetched headline is attributed to a symbol ONLY if it
# contains one of these distinctive name phrases (word-boundary matched). Google
# News RSS token-matches the company query loosely, so without this gate a
# "National Foods expands in UAE" story leaks onto NRL ("National Refinery") and
# gets rated as NRL news — a mis-attribution the engine must never make. Phrases
# are the distinctive core of each curated COMPANY_NEWS_QUERY name; ambiguous
# bare tickers are omitted on purpose (e.g. "NRL" is also National Rugby League).
COMPANY_NEWS_ANCHORS = {
    "PSO": ["pakistan state oil"],
    "TREET": ["treet corporation", "treet"],
    "FABL": ["faysal bank"],
    "AIRLINK": ["air link", "airlink"],
    "MEBL": ["meezan bank"],
    "SYS": ["systems limited", "systems ltd"],
    "LUCK": ["lucky cement"],
    "FFC": ["fauji fertilizer"],
    "NRL": ["national refinery"],
    "DGKC": ["dg khan cement", "d.g. khan"],
    "OGDC": ["oil and gas development", "ogdc"],
    "PPL": ["pakistan petroleum"],
    "MARI": ["mari petroleum", "mari energies"],
    "HUBC": ["hub power", "hubco"],
    "ENGROH": ["engro holdings"],
    "EFERT": ["engro fertilizer"],
    "FCCL": ["fauji cement"],
    "MLCF": ["maple leaf cement"],
    "NML": ["nishat mills"],
    "PAEL": ["pak elektron"],
    "SEARL": ["searle"],
    "HCAR": ["honda atlas"],
    "PRL": ["pakistan refinery"],
    "ATRL": ["attock refinery"],
    "SNGP": ["sui northern"],
    "SSGC": ["sui southern"],
    "SAZEW": ["sazgar"],
    "FFL": ["fauji foods"],
    "CPHL": ["citi pharma"],
    "KEL": ["k-electric", "k electric"],
    "PIBTL": ["pakistan international bulk", "pibt"],
    "TELE": ["telecard"],
    "DCL": ["dewan cement"],
    "GGL": ["ghani global"],
    "BNL": ["bunnys"],
    "ILP": ["interloop"],
    "FCL": ["fast cables"],
    "JVDC": ["javedan"],
    "AGP": ["agp limited"],
    "WAVES": ["waves corporation"],
    "TOMCL": ["organic meat"],
    "IMAGE": ["image pakistan"],
    "SYM": ["symmetry group"],
    "FCEPL": ["frieslandcampina", "engro foods"],
    "KOHC": ["kohat cement"],
    # Added 2026-08-13 from user-supplied company names, each cross-checked
    # against this file's own OTHER_COMPLIANT descriptions:
    "SLM": ["service long march", "long march tyres"],
    "SLGL": ["secure logistics"],   # registered name is hyphenated
                                    # ("Secure Logistics-Trax Group Ltd."); the
                                    # matcher joins tokens with \s+, so a
                                    # "secure logistics trax" anchor would NOT
                                    # match the hyphenated form. Two words are
                                    # distinctive enough on their own.
    "THCCL": ["thatta cement"],
    # GHNI / GAL confirmed user-side 2026-08-13. Anchored on TWO words each, not
    # the shared surname: both companies are "Ghandhara", so a bare "Ghandhara"
    # headline is ambiguous and deliberately matches NEITHER — the same
    # conservative rule that keeps NRL from swallowing National Foods.
    # NOTE: SECTORS in this file still maps GHNI to "Glass/Holding" and GAL to
    # "Textile/Synthetic Fibre". Both are automotive assemblers, so those labels
    # are wrong; they are left alone here because SECTORS drives the sector
    # exposure cap in portfolio_risk, and silently re-bucketing two names
    # changes book-level risk limits. Fix that separately and deliberately.
    "GHNI": ["ghandhara industries"],
    "GAL": ["ghandhara automobiles"],
}


def company_anchor_terms(symbol):
    """Anchor phrases for a symbol; falls back to the bare ticker when no
    curated name exists (strict word-boundary match keeps that conservative)."""
    return COMPANY_NEWS_ANCHORS.get(symbol) or [symbol.lower()]


def headline_matches_company(symbol, *texts):
    """True if any anchor for `symbol` appears (word-boundary matched) in the
    joined texts. Multi-word anchors match across flexible whitespace; each
    token is regex-escaped so dotted names (d.g. khan) match literally."""
    hay = " ".join(t for t in texts if t).lower()
    if not hay:
        return False
    return _anchors_match(company_anchor_terms(symbol), hay)


def _anchors_match(terms, hay):
    for term in terms:
        term = (term or "").lower().strip()
        if not term:
            continue
        pat = (r"(?<!\w)" + r"\s+".join(_re.escape(tok) for tok in term.split())
               + r"(?!\w)")
        if _re.search(pat, hay):
            return True
    return False


def headline_matches_sector(sector, *texts):
    """Word-boundary sector match. Plain substring matching routed 'shipping'
    and 'Philippines' to Power Generation via the 'ipp' anchor — the same class
    of mis-attribution the company gate was built to stop.

    SECTOR_NEWS_EXCLUDE vetoes a match outright: 'palm oil prices ease' hits the
    'oil prices' anchor but is a Food story, not a Refinery one."""
    hay = " ".join(t for t in texts if t).lower()
    if not hay:
        return False
    if _anchors_match(SECTOR_NEWS_EXCLUDE.get(sector) or [], hay):
        return False
    return _anchors_match(SECTOR_NEWS_ANCHORS.get(sector) or [], hay)

# ---------------------------------------------------------------------------
# 6. MACRO INPUTS — manually maintained (update from SBP/PBS releases).
#    The engine also scores macro news automatically; these anchors give it
#    a baseline. Each carries an as_of date; stale values trigger warnings.
# ---------------------------------------------------------------------------
MACRO_ANCHORS = {
    "policy_rate_pct":   {"value": 11.5,   "as_of": "2026-04-27", "source": "SBP MPC (raised +100bps to 11.5%)"},
    "cpi_yoy_pct":       {"value": 11.7,   "as_of": "2026-05-31", "source": "PBS (May 2026 CPI YoY)"},
    "usd_pkr":           {"value": 278.75, "as_of": "2026-06-14", "source": "Interbank"},
    "fx_reserves_usd_bn":{"value": 17.22,  "as_of": "2026-06-05", "source": "SBP-held reserves"},
}
MACRO_STALE_DAYS = 45

# Earnings-date awareness: within this many days BEFORE a known result/board-
# meeting date, a fresh Buy/Strong Buy is held at Watch (binary event risk). Only
# acts when a date is KNOWN — from EARNINGS_DATES below, or an optional
# "earnings_date" field the news routine adds to news_signals.json. Unknown =
# no effect (never fabricates a blackout).
EARNINGS_BLACKOUT_DAYS = 5
EARNINGS_DATES = {}          # manual override, e.g. {"LUCK": "2026-07-28"}

# ---------------------------------------------------------------------------
# 7. SCHEDULING
# ---------------------------------------------------------------------------
RUN_INTERVAL_MINUTES = 15
# Dashboard staleness flagging — when the latest run is older than these
# thresholds (in hours, PKT), the "Last updated" tile shifts amber then red and
# a banner warns the user that signals may not reflect current price action.
# Honest-by-design: better to flag stale than to let it pass as fresh.
DATA_FRESHNESS_AMBER_HOURS = 4
DATA_FRESHNESS_RED_HOURS = 24
# Dashboard tabs left open reload themselves this often (seconds) so they
# reconnect to the freshly-rebooted Streamlit Cloud server and re-read the
# committed DB. 0 disables. Skipped when DASHBOARD_PASSWORD is set.
DASHBOARD_REFRESH_SECONDS = 300
MARKET_OPEN = "09:15"     # PSX regular session (Mon-Thu 09:32-15:30 approx;
MARKET_CLOSE = "15:45"    # Fri split session). Slightly widened window.
MARKET_DAYS = [0, 1, 2, 3, 4]          # Mon..Fri
MORNING_REPORT_TIME = "09:00"
EVENING_REPORT_TIME = "21:00"
TIMEZONE = "Asia/Karachi"

# ---------------------------------------------------------------------------
# 8. STORAGE / LOGGING
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PSX_DB_PATH", os.path.join(BASE_DIR, "psx_engine.db"))
LOG_PATH = os.path.join(BASE_DIR, "engine.log")
REPORT_DIR = os.path.join(BASE_DIR, "reports_out")

# Authentic news feed — produced by the daily Claude news routine (see
# news_routine.md). news_signals.json holds per-symbol LLM-judged news verdicts
# with source URLs. The engine reads it via news_feed.py; if the file is missing
# or older than NEWS_SIGNALS_MAX_AGE_HOURS, it falls back to RSS/VADER scoring.
NEWS_SIGNALS_PATH = os.path.join(BASE_DIR, "news_signals.json")
# Raw auto-fetched last-24h headlines (news.yml on a cron writes this). Shown
# UNSCORED per-symbol in the dashboard for manual cross-verification; never
# weighted into the score.
NEWS_RAW_PATH = os.path.join(BASE_DIR, "news_raw_24h.json")
# Your real holdings + ready cash (read by portfolio_advisor for the dashboard's
# Portfolio tab). Edit portfolio.json or the dashboard table to keep it current.
PORTFOLIO_PATH = os.path.join(BASE_DIR, "portfolio.json")
# Symbol that gets the deep, position-aware morning brief (focus_brief.py):
# every indicator the engine computes, the real book position, the unscored
# news window and the symbol's own graded record, resolved into ONE action.
# It re-reads the engine's signal — it never creates a competing score.
FOCUS_SYMBOL = "NRL"

# Sector-level news routing. COMPANY_NEWS_ANCHORS deliberately requires a
# distinctive company name so a headline cannot be mis-attributed across
# companies — but that also means SECTOR news never reaches any symbol. The
# Brownfield refining policy (approved 2026-07-28) drove every refinery name in
# August while matching only PRL's anchor ("Pakistan Refinery"), so the single
# biggest driver of NRL's price was invisible in NRL's own news window.
# These phrases route a headline to EVERY symbol in the sector, and the result
# is labelled sector news — never presented as company-specific.
# Matched word-boundary via headline_matches_sector(). Coverage matters: 19 of
# the 26 sectors in SECTORS had NO anchors at all, so the biggest drivers on the
# board — crude, the fortnightly petrol/diesel notification — reached nobody, and
# the rater was handed only the sectors with no story and correctly called them
# noise. Broader anchors mean more sectors get rated; a rating of neutral/noise
# still moves a score by exactly 0.0, so the cost of a false route is bounded.
_FUEL_PRICE = ["petrol price", "diesel price", "fuel price", "ogra",
               "petroleum levy", "high speed diesel", "hsd", "furnace oil"]
# Verb forms are enumerated rather than anchoring on a bare "oil": word-boundary
# "oil" also matches "edible oil" and "palm oil", which are Food stories.
_CRUDE = ["crude", "crude oil", "oil price", "oil prices", "brent", "opec",
          "oil market", "oil markets", "oil supply", "oil demand", "oil futures",
          "oil climbs", "oil rises", "oil falls", "oil slips", "oil jumps",
          "oil drops", "oil gains", "oil eases", "oil steady", "oil surges"]
SECTOR_NEWS_ANCHORS = {
    "Refinery": ["refinery", "refineries", "refining", "refining policy",
                 "brownfield", "deemed duty", "crack spread"] + _CRUDE + _FUEL_PRICE,
    "Oil Marketing": ["oil marketing", "omc", "omcs", "fuel demand",
                      "petroleum products"] + _CRUDE + _FUEL_PRICE,
    "Oil & Gas Exploration": ["exploration and production", "e&p", "gas field",
                              "circular debt", "wellhead", "gas discovery",
                              "petroleum policy", "drilling"] + _CRUDE,
    "Cement": ["cement", "cement sector", "cement dispatches",
               "cement industry", "clinker", "construction"],
    "Cement/Conglomerate": ["cement", "cement dispatches", "cement industry",
                            "clinker", "construction"],
    "Fertilizer": ["fertiliser", "fertilizer", "urea", "dap", "farm",
                   "agriculture", "crop", "crops", "corn", "kharif", "rabi"],
    "Power Generation": ["ipp", "ipps", "independent power", "circular debt",
                         "power tariff", "electricity tariff", "nepra",
                         "power sector", "power purchase"],
    "Gas Distribution": ["gas tariff", "gas price", "rlng", "sui gas", "sngpl",
                         "ssgc", "ogra", "gas supply"],
    "Islamic Banking": ["policy rate", "monetary policy", "state bank", "kibor",
                        "banking sector", "deposit rate", "cash reserve",
                        "islamic banking", "advances to deposit"],
    # Listed peers are included deliberately: an assembler's results are the
    # main read-across the auto sector gets, and none of these are in STOCKS.
    "Auto Assembler": ["auto sector", "automobile", "auto assembler",
                       "car sales", "car prices", "vehicle sales", "auto policy",
                       "indus motor", "pak suzuki", "honda atlas", "sazgar"],
    "Textile": ["textile", "textiles", "cotton", "yarn", "apparel",
                "textile exports"],
    "Textile/Synthetic Fibre": ["textile", "polyester", "synthetic fibre",
                                "cotton", "yarn"],
    "Pharmaceuticals": ["pharmaceutical", "pharmaceuticals", "pharma", "drap",
                        "drug prices", "medicine prices"],
    "Technology/IT": ["it exports", "technology exports", "software exports",
                      "pseb", "it sector", "freelancers"],
    "Technology/IT Exports": ["it exports", "technology exports",
                              "software exports", "pseb", "it sector"],
    "Technology/Telecom": ["telecom", "pta", "spectrum auction", "broadband",
                           "internet", "starlink"],
    "Technology/Telecom Devices": ["telecom", "pta", "spectrum auction",
                                   "broadband", "mobile phones", "handsets"],
    "Food": ["food sector", "packaged food", "fmcg", "dairy", "food inflation",
             "sugar", "edible oil"],
    "Logistics/Ports": ["port", "ports", "port qasim", "terminal", "shipping",
                        "cargo", "container", "trade volumes"],
    "Logistics/Transport": ["logistics", "freight", "trucking", "supply chain"],
    "Tyre Manufacturing": ["tyre", "tyres", "tire", "rubber"],
    "Glass/Holding": ["glass", "float glass"],
    "Electrical Goods": ["appliances", "electrical goods", "cables",
                         "home appliances"],
    "Real Estate": ["real estate", "property", "housing", "construction"],
}
# Checked BEFORE the anchors: an edible-oil story must never reach the refiners.
_COOKING_OIL = ["edible oil", "palm oil", "cooking oil", "soybean oil",
                "sunflower oil", "olive oil", "ghee"]
SECTOR_NEWS_EXCLUDE = {
    "Refinery": _COOKING_OIL,
    "Oil Marketing": _COOKING_OIL,
    "Oil & Gas Exploration": _COOKING_OIL,
}
# PSX runs a +-10% circuit breaker (verified live: NRL prev close 517.58 ->
# limits 465.82 / 569.34). A move beyond it cannot happen in ordinary trading,
# which is what makes corporate-action detection reliable rather than a guess.
CIRCUIT_LIMIT_PCT = 10.0
# Shock-up entry deferral (2026-09-04). Measured on 5 years of adjusted,
# tradeable history: INSIDE the engine's own candidate pool (uptrend + positive
# RS), days carrying a >=2.5 sigma, >=3% up-move went on to return -0.41% median
# excess at 5 days against 0.00% for the rest, 45% positive against 49%, on
# n=1,169 across 49 symbols, consistently negative at 3/5/10/20 days.
# It DEFERS rather than vetoes: the name is held at Watch for that one run and
# is free to become a Buy the next day. The effect is real but small (-0.4pp),
# and every outright veto measured in this repo rejected a better subset than it
# passed, so rejecting the TIMING rather than the stock is the proportionate act.
SHOCK_UP_DEFER_ENABLED = True
SHOCK_UP_SIGMA = 2.5
SHOCK_UP_MIN_PCT = 3.0
# Minimum 20-day median turnover for a name to count as tradeable. Every current
# symbol clears this comfortably; it binds on thin historical periods and will
# bind again as the universe expands beyond KMI-30.
MIN_TURNOVER_PKR = 5_000_000

NEWS_SIGNALS_MAX_AGE_HOURS = 24         # strict 24h window per user spec; weekend gap means Mon's run starts neutral until refresh
# Authentic-or-neutral policy: when there is NO fresh authentic verdict for a
# stock, treat its news as NEUTRAL rather than keyword-scoring noisy RSS with
# VADER. Set True only to restore the old VADER fallback. False means news moves
# a signal ONLY when there is real, sourced news.
NEWS_FALLBACK_VADER = False
# Only these sources count as authentic for the news routine (no social/rumor).
# Narrowed to 3 desks (2026-06-14) to keep the routine token-frugal — the first
# full run naturally used only Mettis + BR anyway.
NEWS_SOURCE_ALLOWLIST = [
    "brecorder.com",                     # Business Recorder
    "dawn.com",                          # Dawn Business
    "mettisglobal.news",                 # Mettis Global
    "profit.pakistantoday.com.pk",       # Profit Pakistan Today
    "propakistani.pk",                   # ProPakistani (added 2026-08-24)
    "investify.pk",                      # Investify (added 2026-08-26, user)
]
# Credible publisher NAMES (lowercase substrings) for the UNSCORED raw-news
# window. The fetch-time host allowlist is bypassed by Google News redirect
# links (every link is news.google.com), so the raw file contains many
# off-desk publishers (Daily Times, MM News, etc.). The dashboard window
# filters to these names — parsed from the "Headline - Publisher" title — so
# the user only eyeballs credible desks. Purely a display filter; no scoring.
NEWS_DISPLAY_PUBLISHERS = [
    "business recorder", "brecorder", "dawn", "mettis", "profit",
    "propakistani",  # added 2026-08-24
    "investify",     # added 2026-08-26
    "the news", "tribune", "bloomberg", "reuters",
]
EXCEL_DIR = REPORT_DIR

# ---------------------------------------------------------------------------
# 9. NOTIFICATIONS / EMAIL  (secrets come from the ENVIRONMENT — never commit
#    them. On the cloud these are injected from GitHub Actions Secrets.)
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")                  # sending Gmail address
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")  # 16-char app password
EMAIL_TO = os.environ.get("EMAIL_TO")                    # recipient address
# How often to email: "off" (default) never emails; "actionable" emails only
# when a Buy/Strong Buy/Exit appears; "always" = every run. Disabled by default
# (2026-07-12) — the 15-min loop was emailing every run; the dashboard is the
# live surface, not the inbox. Re-enable per env if you want alerts back.
EMAIL_MODE = os.environ.get("EMAIL_MODE", "off")
ACTIONABLE_SIGNALS = {"Strong Buy", "Buy", "Exit"}

# Dashboard view password (set in Streamlit Cloud secrets; falls back to env).
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

DISCLAIMER = (
    "This tool is decision support, NOT financial advice. No system can "
    "guarantee profit or zero loss. Setups are labelled low/medium/high risk. "
    "Always confirm manually before trading and never risk money you cannot "
    "afford to lose."
)

# ---------------------------------------------------------------------------
# Hourly news routine window (2026-08-21)
# ---------------------------------------------------------------------------
# The news fetch runs hourly, but ONLY inside the PSX trading session, so a
# cron that fires at 02:00 PKT costs nothing and commits nothing. Windows are
# PKT wall-clock; Friday runs long because PSX splits the session around Jumma
# and closes later.
NEWS_WINDOW = {
    "mon_thu": ("09:00", "15:30"),
    "fri":     ("09:00", "16:30"),
}

# PSX market holidays, "YYYY-MM-DD" in PKT. DELIBERATELY EMPTY: the authentic
# PSX/SBP holiday calendar is not derivable from anything in this repo, and
# guessing dates would silently skip real trading days (or fetch on closed
# ones) with no symptom. Populate from the PSX "Trading Holidays" notice —
# https://dps.psx.com.pk/holidays — and note that Eid dates are moon-sighting
# dependent and get confirmed only days ahead, so this list needs a refresh
# each quarter. An empty list means "no holidays known": the routine then runs
# on every weekday, which is the safe failure (a fetch on a closed day just
# finds no fresh headlines and commits nothing).
PSX_HOLIDAYS = []

# Versioned technical research contract. No calibrated probability is available.
STRATEGY_VERSION = "technical_swing_short_v7"   # falling market warns but does not veto
REPLAY_LOOKBACK = 21
UNIVERSE_KNOWN_FROM = "2026-09-09"  # dated 60-name selection; earlier membership not inferred
FEATURE_HISTORY_LIMIT = 42
MIN_STRATEGY_HISTORY = 42
EXCHANGE_HOLIDAYS = []  # Populate from verified dated exchange notices.
SESSION_OVERRIDES = {}  # date -> [(open, close)]; [] explicitly closes a day.
PUBLICATION_DELAY_MINUTES = 30
# holding_sessions 10 -> 30 (2026-09-10, measured). At 10 sessions the exit rule
# was the binding constraint on the whole strategy: targets sit ~4 ATR away and
# each name's own history says a 4-ATR move takes a median 12-20 sessions, so
# 51.2% of trades expired undecided at +0.22% while the much nearer stop had the
# full window to trigger. Stops got 10 sessions to reach a near level; targets got
# 10 to reach a far one.
#
# Measured on 1,165 real historical entries (58 symbols, 2022-2026), holding the
# signals and stops fixed and varying ONLY the exit: expectancy rises monotonically
# with the horizon at a 4-ATR target -- 5s -0.19%, 10s +0.34%, 15s +0.83%,
# 20s +1.02%, 30s +1.28% -- while expiries collapse 51.6% -> 13.0%. It wins in
# 4 of 5 years (2022 is 34 trades), and through the Feb-2026 shock (-16.5% at 3x
# normal volatility) it held +0.08% where 10 sessions lost -0.50%.
#
# Nearer targets are the opposite of the fix: 1.0 ATR lifts the win rate to a
# flattering 69% and LOSES money (-0.41%, PF 0.85) because the wins stop paying
# for the stops. Do not optimise the win rate here.
EXECUTION = {"holding_sessions": 30, "entry_gap_limit": 0.03,
             "slippage_bps": 20, "fee_bps_per_side": 15,
             "max_volume_participation": 0.01,
             "capacity_basis": "decision_session"}
