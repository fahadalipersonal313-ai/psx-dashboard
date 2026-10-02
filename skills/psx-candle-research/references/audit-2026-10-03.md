# PSX candle research audit — 3 October 2026

## Decision

Profitability confidence is **not measured**. No defensible percentage supports a claim of unprecedented trading mastery or guaranteed profitable calls. A reusable research skill has been created; production calls and dashboard rules are unchanged.

## Snapshots and coverage

Deployment/audit repository: `fahadalipersonal313-ai/psx-dashboard`, baseline `be901f5a945566dbccb3fecba92ee53ba9fb1be6`. Its decision, scoring, signal, swing, technical, timing, intraday-tracking, data-quality, calendar and config modules were compared as normalized text with the audited engine code and are identical. Historical database snapshots came from `psx-engine`: main `50813fa` (2 October evening grading), runtime `1043699` (2 October). They are separate snapshots, not an automatically reconciled dataset. Main contains **70,594** daily candles for all **60** tracked stocks, from **1 September 2021 to 2 October 2026**. Runtime contains **70,534** through **1 October**. Main’s SLM has **76** candles; most other stocks have approximately 1,263.

Executed read-only query with the 60 `config.STOCKS` parameters:

```sql
SELECT symbol,COUNT(*),MIN(date),MAX(date)
FROM daily_ohlc WHERE symbol IN (<60 placeholders>) GROUP BY symbol;
SELECT source,COUNT(*) FROM daily_ohlc
WHERE symbol IN (<60 placeholders>) GROUP BY source;
```

Source labels are official PSX historical or market-summary downloads; labels do not establish independent reconciliation of every bar. No new bulk chart download was made. All stored candles were scanned numerically; the interactive gallery plots each stock’s latest 120 stored sessions. Older candles are used for research across regimes, not added to the engine’s existing recent-window decision.

## Verified audit findings

1. **Confidence is a heuristic score.** `scoring_engine.py:113–134` starts at quality 70 and returns `target_probability=None`; `swing_evaluation.py:25` freezes no probability. Current runtime has four Buys with score 70 and **zero resolved v8 emitted opportunities**, not enough evidence to calibrate a profitable-trade probability. `signal_generator.py:280` gates quality below 45. Engine-only `research_validation.py:22,35` supplies training separation/calibration helpers but has no production callers.

```sql
SELECT o.version,x.status,COUNT(*) FROM opportunities o
LEFT JOIN opportunity_outcomes x ON x.opportunity_id=o.id
GROUP BY o.version,x.status;
```

Runtime result: v7 five pending; v8 one pending. Engine candidate cohorts include rejected ideas (`trading_review.py:43`); their outcomes cannot substitute for emitted-Buy outcomes.

2. **Older history needs reconciliation before strong timing claims.** Applying `target_timing.py:57–70` to runtime’s 60 stock histories accepts 15 and blocks 45. Twenty-two have invalid OHLC and 36 have unresolved adjacent-close jumps above 10.5%; groups overlap. An unresolved jump is not proof of a corporate action.

```sql
SELECT verified,COUNT(*),COUNT(known_at)
FROM corporate_actions GROUP BY verified;
```

Result: `(0,122,0)`. The candle diagnostic separately flags 356 unusable rows, including invalid shapes and discontinuities. It blocks contaminated 41-candle contexts and marks contaminated outcomes unresolved; it does not invent adjustment factors.

3. **Existing candle recognition is descriptive.** `technical_analyzer.py:173–181` uses a four-close reversal proxy and adds its note at `:357–359` after scoring at `:353`. This production path is not a trained model of candle bodies, wicks or sequences. The separate `chart_analysis.py` contains OHLC pattern utilities; their existence does not establish measured profit probability.

4. **Target-time percentages answer a different question.** `target_timing.py:38–53,88` measures overlapping 40-session high touches and timing among successful touches. Frozen execution uses 30 sessions, costs, stops and fill constraints (`swing_evaluation.py:74–102`). A high touch does not establish positive net profit or target-before-stop probability.

5. **Universe selection creates historical bias.** The ten additions were chosen using later trend, score and liquidity (`config.py:106–111`). Production replay correctly applies the dated membership boundary (`swing_evaluation.py:180`; `config.py:943`). This new candle study explicitly labels its earlier history as a conditional diagnostic of today’s selected names, not unbiased tradable performance.

6. **Intraday learning has no trade-outcome labels and recent feed coverage failed.** Runtime archive has 129 runs, 7,740 observations and nine confirmed episodes across eight stocks. All nine episodes ended; closure is observed expiry, not an executable exit (`intraday_tracking.py:142–145`). No fill/trade-outcome table exists. All 2,400 observations across 30 September, 1 October and 2 October are Unavailable.

```sql
SELECT session,json_extract(payload,'$.reason'),COUNT(*)
FROM observations WHERE session>='2026-09-30'
GROUP BY session,json_extract(payload,'$.reason');
```

Result: 1,440 / 180 / 780 observations, respectively, with no usable current-session observation. This data path needs repair and prospective outcome capture before an intraday confidence claim.

**Additional source weakness:** benchmark `_history` checks finite closes but not provenance (`decision_engine.py:79`). An in-memory probe with unverified benchmark source strings still returned Buy in three cases. Inspected real benchmark sources were labelled official; actual contamination was not found.

**Verified safeguards:** NRL/EFERT/ATRL/ASL runtime snapshots reproduced their Buy and hashes. Appending invalid future bars left each complete decision unchanged. Completed-session cutoffs, official stock sources and coverage guards are enforced (`decision_engine.py:64–78,113–117,139–145`). Liquidity, frozen costs, prior-session capacity, stop-first ambiguity and unresolved locked bars remain guarded (`risk_manager.py:69–78`; `signal_generator.py:259–260`; `swing_evaluation.py:40–44,75–98`). This is a targeted check, not proof that every path is free of look-ahead.

## Fixed candle diagnostic

Four hypotheses were fixed before inspecting their measurements. Research version v2 additionally checks stock-session continuity against the union of stored dates, records code/data hashes and counts incomplete setups once; it produces the same priced return measurements here as v1. The union is a proxy and cannot detect sessions missing for every stock. Features use only the current and preceding 40 sessions; liquidity uses preceding 20-session median turnover. The price scenario enters at the next stored open and exits at the fifth later session’s close. Same-stock overlapping setups within each pattern are counted once. Split-boundary outcomes are purged; missing sessions, incomplete futures, questionable exit availability and bad candles remain unresolved/unfilled. Cost scenarios: 0, 70 and 100 round-trip basis points. Seventy is a **0.70 percentage-point assumed deduction**, not verified broker charges or actual fills.

The 2026 period was already available, so it is a chronological diagnostic test, **not an untouched holdout**. No hypothesis was selected or optimized from these results. Cross-stock, sector and cross-pattern dependence remains; no calibrated probability or independent-trade interval is produced. Priced statistics exclude outcomes with future data/fill issues and can therefore be biased; priced coverage is shown and the positive fraction is not a probability of profit.

| Pattern | Priced scenarios in 2026 | Priced coverage | Positive after assumed costs | Mean return after assumed costs | Unresolved / unfilled |
|---|---:|---:|---:|---:|---:|
| bullish_engulfing | 249 | 95.0% | 32.5% | -1.68% | 13 / 0 |
| hammer_after_decline | 126 | 95.5% | 34.9% | -1.51% | 3 / 3 |
| volume_breakout | 230 | 98.3% | 40.4% | -0.80% | 4 / 0 |
| trend_pullback | 192 | 98.0% | 28.1% | -1.67% | 3 / 1 |

Evidence: `docs/candle-research/specification.json`, `docs/candle-research/evidence.json`; detailed `observations.json` and `charts.html` remain local/generated artifacts rather than Git source; runner `tools/candle_research/study.py`. Dataset SHA-256: `9723be171592b79b2c5c0899a2014b6340d9de47ea6f74424e18e77563d8bbe4`.

Every tested pattern has a negative mean in this period. This does not re-measure or refute the brief’s §5 30-session engine results: rules, entry/exit contract, period, universe and treatment of actions differ. It does show that these candle names alone do not justify new profitable-trade claims.

## Continuing learning

The installed skill preserves this method and evidence. It can create a new immutable checkpoint when completed sessions or sourced corrections arrive. It does not retrain the language model. The user authorized daily cloud research with validation before rule changes. The new dashboard-repository `PSX Candle Research` workflow is scheduled at 03:20 PKT Tuesday–Saturday, after the prior trading day’s evening work, with a manual trigger. It downloads only pinned engine database snapshots, without cloning engine history or pushing to the engine. At most one compact checkpoint per completed session goes to the dashboard’s separate `research-evidence` branch; corrections/reruns remain artifacts. Daily research makes no main-branch push or Streamlit restart. GitHub can delay scheduled runs, and stale data causes an explicit failure. It reads stored snapshots, preserves evidence and research hypotheses, and changes no live signal rules. Deployment and an actual cloud execution must be verified before calling the schedule active. Freeze later prospective predictions and measure resolved, independent outcomes after actual execution assumptions. Compare net returns, drawdowns, coverage, calibration and uncertainty rather than trying to maximize a flattering win rate. Version rule changes and keep intraday/swing outcomes distinct. Continue to abstain from skill-generated trade calls while validation is absent.

## Verification and small repair

The audit originally ran `python -m unittest discover -q` against the matching engine code: all 191 existing tests passed; that is not a dashboard-wide test claim. The initial 21 research tests passed (six study and 15 daily), including future-data invariance, missing-session blocking, partition purging, incomplete-setup deduplication, close/publication cutoffs, immutable corrections and pending-contract retention. A real read-only main/runtime integration selected 2 October main data, matched the v2 dataset hash, covered 60 stocks and saved a 39 KB checkpoint; its one setup was marked retrospective. No probability was produced. The suite exposed a Windows database-download handle leak: `remote_data.py:17,48,58` now uses explicit connection closing before replacement. All six existing remote-data tests pass; no scoring changes were made. The dashboard-specific suite additionally validates pinned downloads, atomic failures, one checkpoint per session, baseline labelling, after-midnight freezing and artifact-only corrections. Its final test count is recorded by the verified cloud run.

## Sources and access

- Official daily interface: https://dps.psx.com.pk/historical
- Official downloads and access terms: https://dps.psx.com.pk/downloads
- Official EOD/historical products and permission scope: https://www.psx.com.pk/psx/product-and-services/data-services-vending
- TradingView PSX chart display is 15 minutes delayed: https://www.tradingview.com/data-coverage/
- TradingView display/non-display restrictions: https://www.tradingview.com/policies/
- Intraday chart history limits: https://www.tradingview.com/support/solutions/43000480679-historical-intraday-data-bars-and-limits-explained/
- Selection/overfitting research: https://escholarship.org/uc/item/4w1110bb

Free public display does not prove permission for automated ingestion. No free, unrestricted, complete PSX intraday archive was verified.

## Not verified

Complete historical corporate actions/dividend accounting, past membership/eligibility, broker fills, spreads, queue availability, taxes and actual fees, independent calibration, future profitable performance, and uninterrupted autonomous execution of this skill, repaired live intraday feed, and calibrated prospective performance. No mastery percentage or guarantee is claimed.
