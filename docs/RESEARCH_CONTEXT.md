# Combined research contract v1

The original versioned technical strategy remains separate. New research is experimental decision support, not a probability, order service, calibrated prediction, or demonstrated edge.

## Runtime and deployment

- Live user-facing app: `https://psx-engine.streamlit.app`, repository `fahadalipersonal313-ai/psx-dashboard`, branch `main`, `dashboard.py`.
- The separate `psx-engine` repository owns source on `main`, data on `runtime-state`, and model context on `research-state`.
- Approved research universe: PRL, MEBL, SYS, PSO, GAL, EFERT, ATRL, NRL, ASL, FCL, FCEPL, CNERGY, THCCL, FABL, OGDC. Existing 60-stock technical strategy is unchanged.
- One authorized model-context writer updates only `research-state:research_context.json`, using its current blob SHA and refusing a race rather than overwriting another writer.
- Validate before writing: `python research_contract.py research_context.json`. Read back and revalidate after publication.
- Source updates must not write SQLite. Engine/recovery/evening writers share `psx-engine` concurrency and publish data only to `runtime-state`, through the conflict-refusing publication helper.
- The existing engine loop targets 15-minute start-to-start snapshots. Model research targets 30-minute scheduled reviews. Neither GitHub nor assistant scheduling guarantees exact latency; the UI fails closed at read time.

## JSON shape

Top-level: `schema_version: 1`, `generated_at`, `as_of`, `expires_at` (aware ISO timestamps), `universe` (exact 15 symbols), `sources`, `market_context`, `stocks`.

Source: `id`, `title`, `url` (public HTTP/S, no credentials), `kind`, `published_at` (aware ISO or null if genuinely unknown), `verified_at` (aware ISO). All references resolve to source IDs; unknown publication time must never be presented as fresh publication. A fetched date does not revise an old event's date.

Evidence object: `status` (`available`, `unavailable`, or `no_material_news`), `summary`, `bias` (`supportive`, `mixed`, `adverse`, `unknown`), `source_ids`. Available evidence requires a source verified within one hour of the declared research as-of. Fundamentals instead reference sources verified around their preserved monthly reviewed_at (24-hour allowance), so a fresh news review does not force a new financial-report review. A no-material-news conclusion is permitted only for news, requires a checked coverage source, and cannot carry supportive bias. Missing evidence is not neutral evidence.

Market context: evidence objects plus `category` (`macro`, `geopolitical`, `sector`). Global vetoes apply only to macro/geopolitical observations; stock sector context is scoped to each stock.

Stock: `symbol`, `thesis`, `countercase`, evidence objects `news`, `sector`, `fundamentals`, `public_sentiment`, and `horizons` (`intraday`, `swing`, `investment`). Horizon: `stance` (`watch`, `supportive`, `cautious`, `avoid`, `unavailable`), `rationale`, `source_ids`.

Fundamentals additionally require `report_period`, `reviewed_at`, `next_review_at`, `event_review_required` (boolean), `event_triggers` (list). Available fundamentals require a report period and next review within 31 days. Refresh at least monthly and after sourced results, distributions, corporate actions, financing changes, material guidance, material regulations or similar issuer/sector events. Explain event impact and label inference. Do not treat an old ratio retrieval date as a current audited financial period.

Optional `fundamentals.events` contains sourced `{kind, date, known_at, source_ids}`; kind is earnings/agm/dividend/corporate_action, date is YYYY-MM-DD, known_at is an aware ISO timestamp. Known earnings within five calendar days and corporate actions within one day block new combined entries. Unknown event dates are not invented.

A review can remain displayed after expiry, but cannot enable an entry. Context expires within at most 72 hours; intraday review must additionally be at most 60 minutes old. The weekend baseline must be reviewed again before Monday intraday use. Public sentiment needs independent attributable public evidence and sample/duplication caveats; news headlines alone are not an independent social signal.

## Market observations and gates

Public PSX company pages are a delayed REG snapshot fallback while legacy tick endpoints return 404. The source quote-update timestamp is preserved as `source_as_of`, separate from `fetched_at`. DPS declares a nominal five-minute delay, not a guaranteed maximum lag. FUT/CSF/ODL rows are excluded. Regular-session cumulative volume must never be treated as per-tick volume.

Snapshots start warming up, require distinct increasing source timestamps, and compare only the same trading segment. Friday lunch is a boundary. No tick tape or intraday OHLC is synthesized. The snapshot watch uses an explicitly experimental 10,000-share cumulative-volume increment, not fabricated PKR turnover or a same-time volume baseline. Reference swing levels require latest completed-session official history, a versioned input hash, a current source/action/session audit, correct level ordering, and acceptable research risk. Known unresolved actions and market-wide session coverage holes withhold new research levels. An independent dated Aug-Dec2026 exchange calendar cross-checks the market-wide banked manifest and catches jointly missing stock/index sessions. It uses the revised Aug26 EidMilad closure, not the obsolete provisional Aug25 entry. Dates outside the covered feature-window range fail closed; later exceptional closure notices must still update the manifest.

During trading hours, the quote must be current-session, at most 20 minutes old, not future-dated, within the validated entry zone, above the stop and below target, with gross target-1 reward/risk at least 2 using the observed quote. Costs, gap/circuit limits, spread and order availability remain unverified; these are plans for review, not executable guarantees. Long-term numeric valuation targets are withheld because no separately validated valuation model exists.

## Prospective records

`python research_runtime.py --quotes-only` refreshes quotes/history. `python research_runtime.py` writes `research_status.json` and immutable `research_decisions/YYYY-MM-DD/HHMMSS-id.json` checkpoints, with full input evidence and SHA256 digests, exact statuses and frozen levels. The original technical backtest cohort is not pooled with combined research.

The initial implementation records observations/candidates only. It does not assume a position was entered. Outcomes stay pending execution evidence, costs are unspecified, and same-bar stop/target conflicts are unresolved without sequencing evidence. No success rate is calculated. A separately verified forward evaluator and explicit cost/execution assumptions are required before performance claims.

No credentials are created, account access expanded, app audience changed, or orders placed. Preserve data-source licensing restrictions and do not assume publicly readable quotes authorize wider redistribution.

## Calendar and artifact maintenance

Review the 2027 exchange calendar by 2026-11-01. The current manifest ends 2026-12-31, and uncovered feature windows fail closed rather than inventing January session coverage. `research_status.json` surfaces this maintenance date. Reconcile new official exceptional-closure notices promptly. Keep each model artifact under 256 KB by removing unreferenced old news sources; retain sources needed by still-current financial reviews. Do not duplicate headlines by alternate URLs or count syndicated copies as independent evidence.


## Five-minute observation collection (forward data only)

The same engine worker now samples the 15 approved company REG pages at the start of each 15-minute analysis cycle and at its +5/+10-minute slots. It publishes the first observations before slower analysis/news work. Delayed/overrun slots are skipped, never backfilled or stamped as if sampled on time. Regular exchange hours, Friday recess, noticed holidays, and the local pause flag are checked before batches. The legacy tick feed is not substituted with invented candles.

`intraday_samples/YYYY-MM-DD/HHMMSS-id.json` contains immutable capture records with actual source_as_of, fetched_at, collection time, price, cumulative session volume, original running daily OHLC context, previous observation ID, and quality flags. Every document says `ohlcv_status: unavailable` and `source_delayed: true`. The running daily high/low are not five-minute extremes. Interval volume is differenced only across increasing source times within the same regular trading segment; duplicate/conflicting/regressing times, resets, overnight/lunch boundaries and source gaps withhold it. Irregular intervals retain their actual seconds. A known counter reset anywhere inside the research watch window invalidates that watch.

The latest convenience view is `intraday_collection_status.json`; it shows configured cadence, actual capture/source times, observed scheduled five-minute windows, missed windows (60-second grace), and per-symbol quality. Counts are through the last capture, not claims of candle completeness. `intraday_collection_state.json` is a mutable index. A local write-ahead transaction recovers interrupted capture/state/status writes exactly once before further requests/publication. Invalid state fails closed instead of silently resetting. Immutable capture files are never rewritten.

Requests use at most two workers and 12-second per-request timeouts inside the 180-second collection budget. Failed symbols receive exponential backoff; HTTP 401/403/404/429 receives one hour. No credentials, paid feeds, or access bypass are introduced. The existing `runtime-state` single-writer publisher remains responsible for all small data commits. No second scheduler/database writer is created.

Paper-model status remains collection-only: no orders, assumed fills, complete 1/5-minute OHLCV, or implemented numeric intraday entry/stop/target model. Sufficient representative session coverage, a frozen prospective strategy, explicit costs/spread/fill assumptions, and independently resolved outcomes are prerequisites. Ambiguous intervals must remain unresolved. Existing candle studies use completed daily bars and are separate.

## Source-verification identity and known events

Keep a source ID tied to one immutable verification record. When a dynamic company page is rechecked for fresh news, create a new source ID and change only relevant news/sector/macro references. Preserve the old verification record used by a cached monthly financial review; do not replace its verified_at in place. Financial-source verification must remain within 24 hours of its preserved reviewed_at, while current-context sources must be verified within one hour of as_of. Prune only unreferenced old sources to keep the artifact below 256 KB.

Record every known verified date in `fundamentals.events`; missing/empty arrays do not establish complete event-calendar coverage. Earnings, AGMs, dividends and actual effective corporate actions remain distinct. Never infer an ex-dividend or price-adjustment date from a book/register closure. Source publication known only to a date uses `published_at: null`, `published_date: YYYY-MM-DD`, `publication_precision: date`; do not invent midnight timestamps. Existing reviews/as-of times must not be refreshed merely to make old facts look current.

## Action board, prospective scorecard and scenarios (2026-10-04)

The Research tab now leads with **What can I do now?** and separates Ready for
review, Watching and Blocked. This is a display of the existing guarded combined
research; it does not alter v8 decisions, eligibility, ranking weights or source
freshness gates. Conditional reference levels, maximum holding sessions and a
recheck deadline are shown only when their evidence is valid. The deadline is
bounded by the current quote, reviewed context and regular trading segment.
Ready for review is not an executable order or a suitability recommendation.

`research_paper.py` records immutable, causally sequenced `paper_events/*.json`
and rebuilds `research_paper_summary.json`. Only new current candidates are
enrolled after installation; old journals are not retrospectively converted to
trades. A bound technical snapshot can create at most one opportunity, and only
one unresolved active opportunity per symbol is observed at a time. Each
candidate freezes its plan, version, source hashes, entry rule, 15-minute-or-less
expiry and 30-session observation deadline. Source update and fetch times must
both be strictly after candidate recording for the first observed entry
condition; the original decision quote cannot activate its own call. Known
collector-quality failures are propagated into the quote and block activation.
Causal event sequence and predecessor hashes make exact retries and recovery
from an interrupted summary write idempotent.

Recording time is the engine's local checkpoint timestamp, **not** a verified
Git publication or dashboard-visibility time. Point samples support observed
conditions only: neither a fill nor the order of possible stop/target touches
between samples is known. Subsequent level touches are unresolved, rather than
wins/losses. Expired, cancelled, research-invalidated and unresolved candidates
remain counted. Verified fills, resolved-trade count, win rate, net gain/loss and
drawdown are deliberately unavailable/zero as appropriate. A future execution
model requires a new version, frozen costs and sufficient independent evidence;
it must not relabel these old observation cohorts as executed trades.

`research_sizing.py` is a separate session-local scenario calculator. Users
explicitly supply capital, available cash, modeled loss budget, fee/slippage,
fixed costs, lot size, position cap and daily-volume participation. Decimal
arithmetic rounds quantity down to satisfy every constraint. Adverse entry/exit
slippage and fees are applied on both sides; fixed round-trip costs reserve cash
and consume risk budget. Quantity requires current validated median volume that
matches the plan and completed-session cutoff. Unverified/older liquidity
withholds sizing. Modeled stop loss is not a guaranteed maximum: gaps, circuit
limits and unavailable liquidity can be worse. Zero assumptions require explicit
acknowledgment. Tax, spread and execution assumptions must be included by the
user; the tool does not claim verified broker charges. No inputs enter Git,
research artifacts, a broker, or the actual portfolio. A separate hypothetical
basket shows sector concentration from user-entered proposed values.

`research_comparisons.json` is rebuilt with the successful completed-session
snapshot by the existing single writer. It measures 5/20-session raw stock price
changes, reported KSE100 index-level changes and their percentage-point
difference. These are not like-for-like total returns or alpha. Exact independent
session sequences, source quality, corporate-action and discontinuity gates
apply. The selected-15 peer mean excludes the stock, requires two or more other
same-sector members and all peer coverage, and discloses its membership. It is
not an official sector index. Presently only the four refiners have enough
selected peers. The 20-session close-times-volume statistic is a turnover proxy,
not actual traded value or instant order capacity.

`research_activity.json` keeps the latest 200 before/after research-condition
changes, deduplicated by the single writer; first load establishes a baseline.
Original inputs remain in immutable research decisions. In-dashboard health
alerts also highlight expired reviews, stale/missing collection and checkpoints,
missed polls, financial reviews and listed near-term events. These alerts do not
subscribe the user to external notifications or claim that every changed review
is a new material announcement. All new runtime files are staged by the existing
writer and retain hard publication-conflict failures.

The paper ledger's future 30-session window requires extending the calendar
**before 19 November 2026**, earlier than the technical lookback alone required.
Maintenance warning now begins 1 November. If a complete holding window cannot
be verified, the summary explicitly reports an enrollment blocker; no candidate
is silently added with a guessed deadline.

The frontend JSON reader bounds intermediary caching by its refresh window and
rejects in-process cache timestamps ahead of the current clock. The Research
refresh control forces a new read without changing any source/review timestamp.
Each component retains its own provenance and fail-closed freshness checks;
standalone files are not represented as one atomic market observation.

The top technical snapshot and engine-status header use that same JSON reader,
with no separate engine-status cache surviving a manual refresh. A local
technical-snapshot fallback is explicitly labeled if the live read fails.

## Annotated daily charts and rolling two-month planner (2026-10-04)

These frontend views reuse the existing banked database and public research
artifacts. They do not add a source collector, schedule, trade rule, order or
private holdings journal. The existing candle-research study remains a separate
section with its own frozen historical assumptions.

`research_charts.py` opens the existing database read-only and validates a
42-session completed-daily window against the independent exchange calendar.
Only the two exact official full-OHLC source identities are accepted. Missing,
duplicated, invalid or unexpected sessions stay explicit; no candles are filled
in. Daily share-volume must be finite, nonnegative and integer-valued. Raw OHLC
and volume remain unadjusted, with banked corporate actions and source/price-basis
warnings. No unadjusted moving average is presented as the strategy's adjusted
indicator, and delayed point samples are never turned into intraday candles.

Current entry-zone, stop and target annotations require independently rederived
technical and combined-research eligibility, matching stock/session/price basis,
complete source/action evidence and consistent level geometry. Numeric reference
segments start at the current evaluation time to the right of completed candles,
not across the historical window. Their short visual display area is not a
holding deadline or predicted target date. Ineligible overlays remain withheld
while valid historical bars can still be viewed. Listed company events, source
publication dates, known-at times and verification times remain separate;
annotations do not claim that an event caused a price move or that a scheduled
event actually occurred.

`research_planner.py` presents a rolling window from today's Pakistan date through
two calendar months, using month-end day clamping. That research window does not
extend or replace the existing maximum 30-session swing holding rule, original
paper candidate expiry, or immutable decision timestamps. Only current eligible
numeric levels are shown. Thesis, countercase, constructive conditions and
invalidation come from the existing reviewed evidence and guards. Upcoming
issuer dates come only from verified listed events inside the window. Suggested
weekly review dates and the existing financial-review due date are explicitly
research maintenance, not issuer catalysts or newly scheduled reminders.
Uncovered future exchange-calendar ranges are warned about, not invented.

The source-linked checklist distinguishes rule/configuration identity, completed
session, bound data/action audit, stored technical signal classification, numeric
plan validation, research currency, macro/company/event risk, quote freshness
and combined entry eligibility. A current financial review is separate from an
adverse financial-risk veto. Unavailable independent public sentiment remains a
coverage limitation; it is not a positive input or an added mandatory swing gate.
Action-board row selection opens the selected stock's chart, planner and checks.

`research_brief.py` derives a morning/pre-session view only from existing saved
artifacts. A weekend or prior-day research cutoff is labeled cached prior review,
even if its JSON was regenerated today. Current-session pre-open review and
updates after the open have separate labels. Any qualifying overnight publication
must have a genuine publication timestamp between the previous actual regular
close and the target session's first open; date-only sources stay separate.
Zero listed publications does not prove no news occurred. Dashboard-condition
changes use engine recording times rather than invented publication times, and
activity-checkpoint coverage is disclosed. Missing source categories, financial
reviews and independent public sentiment stay visible. No chat messages or new
schedules are produced by this display.

Future-generated/as-of contexts and future-known source/event metadata are
withheld from these narratives and annotations as well as numeric plans. Past
expired evidence may remain only with explicit dated/stale labeling; it never
becomes a new current review merely because the page was loaded again.

Technical/context evidence buttons identify their targets as latest branch
artifacts, which can advance after viewing. Their check details retain the
observed run/research times and technical snapshot/configuration hashes; the
latest-branch URL is not represented as an immutable copy of those inputs.

### Primary Trading desk decision contract

`research_signals.evaluate` is the single primary decision adapter, version
`combined-signal-v1`. The Trading desk headline, 15 cards, combined watchlist,
stock detail and Research reuse one evaluation time and the same bound inputs
per app rerun. Open pages target a rerun every minute while connected; browser
suspension, network errors and rendering can delay it. Manual refresh clears the
bounded JSON caches. No recorded Ready status is trusted for current activation.

Only `Ready for review` carries a current numerical swing plan. Watching,
blocked, expired, closed-session and missing-source decisions withhold entry,
stop and target levels on primary cards/tables. The original technical label,
version, configuration, source hash and session remain separate attribution.
Technical Watch/Exit/Avoid never become affirmative research entries. Current
financial review currency is required alongside the existing company, sector,
macro, geopolitical and event-risk guards. Unsupported intraday numeric plans
remain unavailable; a withdrawn entry review does not imply a sell or exit fill.

Duplicate instrument rows, future or contradictory artifact clocks, unknown
quote provenance and fatal collection quality flags fail closed. Quote age must
be strictly below 20 minutes and in-session research review age strictly below
60 minutes, also before the context's own expiry and session boundary. These
are maximum review tolerances, not claims of tick-live data or timely delivery.

The single runtime writer stores `research_signals.json` with checkpoint and
source hashes and adds `signal_version` / `combined_signals` to NEW immutable
`research_decisions` entries. Original technical snapshots and older journals
are never changed or reclassified. The prospective paper ledger retains its
observed-condition semantics and never assumes an executable fill. The UI
compares recorded attribution against current checks to surface withdrawn
entry reviews, without rewriting the recorded checkpoint.
