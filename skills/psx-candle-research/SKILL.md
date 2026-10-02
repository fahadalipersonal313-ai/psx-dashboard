---
name: psx-candle-research
description: Analyse and improve PSX candle evidence for the engine’s 60 tracked stocks, using dated data, frozen experiments and measured outcomes. Use for candle research or confidence audits; never treat a chart score as a guaranteed-profit probability.
---

# PSX candle research

Produce a dated, reproducible assessment of candle structure and its measured value. This skill stores a research method and evidence; it does not retrain the underlying language model.

## Repository and data

Use the user’s lightweight psx-dashboard repository for research code and publication. Do not clone the engine’s full history or add research pushes to it. Read its current operating constraints and identify the code commit and runtime data commit independently. The engine may publish to `runtime-state`; main’s evening database can differ. Inspect each snapshot read-only and label its cutoff. Never write the production database to run research.

Use the configured stock list, currently 60, rather than hardcoding tickers. Check earliest/latest candles, actual source labels, missing/invalid OHLC, trade volume and corporate actions for every stock. Source labels alone are not independent reconciliation. Raw price gaps, splits, rights and dividends must not silently become momentum. Use only sourced adjustment terms known by the decision date. Preserve raw execution prices separately.

Use public source interfaces only within their current permissions. TradingView is useful for permitted visual comparison; its display licence is not an automated engine ingestion licence. Do not use undocumented chart endpoints or bypass restrictions. A publicly accessible PSX page does not by itself authorize bulk harvesting.

## Research and continuing improvement

Use the bundled `scripts/study.py` with `--repo`, `--database` and a new dated `--output` directory. It scans every stored candle for four fixed patterns using at most 40 preceding sessions, produces next-open/five-session-close price scenarios and an interactive chart gallery. It is a diagnostic, not the engine’s 30-session execution contract.

Before inspecting evaluation results, freeze the hypothesis, membership definition, decision cutoff, entry/exit rule, costs, source/adjustment policy and experiment version. Keep all attempted variants. Do not optimize on a test period or reinterpret the already-viewed 2026 period as an untouched holdout. Do not recompute operating-brief measurements merely to rediscover them.

Historical candle context may use the full valid archive to test robustness across regimes. Current signals must still use their declared recent window; older data must not be blended into an existing 42-session decision. The current selected universe is a biased historical cohort until dated membership is reconstructed.

Add a new evidence checkpoint when new completed sessions or corrected sourced bars arrive. Compare it to the prior frozen checkpoint; record improvements, failures, costs and unresolved observations. Keep old artifacts immutable. Change a rule only after evidence supports it, assign a new version, and reserve later untouched sessions for confirmation. Updating this skill’s research ledger is distinct from automatically changing production decisions.

The user authorized daily cloud research on 3 October 2026, with validation before rule changes. The dashboard repository’s `PSX Candle Research` GitHub Actions workflow runs at 03:20 PKT Tuesday–Saturday, after the prior trading day’s evening work. It reads pinned database downloads from engine main/runtime-state, with no engine clone or push. Verify deployment and a successful run before saying it is active. Read its durable `checkpoints/` ledger on the dashboard’s separate `research-evidence` branch (at most one checkpoint per completed session, no daily main pushes) and dated artifacts; do not create a duplicate scheduler or automatically promote rules. Research ideas are frozen separately from live engine calls. A fresh-data failure is unavailable evidence, not a no-op success or a new training example.

For prospective validation, freeze the entry, stop, target, holding horizon and permitted fill model before observing outcomes. Separate intraday and swing. Preserve every intraday refresh, but count a continuing trade setup once. Group correlated symbols/sector events and overlapping sessions when estimating uncertainty. Include unresolved and unfilled cases in coverage reporting; never label them wins or silently drop them to raise a win rate.

## Confidence and abstention

Report confidence only for a precisely defined event, such as positive net return before a frozen exit. Require chronological out-of-sample predictions, enough resolved independent outcomes, realistic costs and fill constraints, calibration diagnostics and uncertainty intervals. A score of 70 is not 70% probability. Target-high touches, chart similarity and a language model’s self-assessment are not trade-profit probabilities.

If those prerequisites are absent, write **not measured** and describe the setup as research/watch evidence. Abstain from new skill-generated trade calls if data, fillability or evidence is inadequate. Do not manufacture a Buy to fill a shortlist. No threshold can make every future trade profitable; even zero historical losses does not establish zero future risk.

Do not create a schedule, place orders, suppress existing production calls or push a strategy change merely because this skill was invoked. Follow the user’s authorized task and existing publication rules. Background execution needs an actual scheduler; this document alone does not run continuously.

## Current evidence

Read [the initial audit](references/audit-2026-10-03.md) for verified limitations and queries. Update dated evidence after new observations; retain earlier findings rather than silently rewriting history.
