# Daily candle research

Authorized: daily cloud research, with validation before live rule changes.

All implementation is in `fahadalipersonal313-ai/psx-dashboard`. The `PSX Candle Research` GitHub Actions schedule is 03:20 PKT Tuesday–Saturday (22:20 UTC Monday–Friday), after the prior trading day’s evening processing. Manual dispatch supports verification. Scheduled jobs can be delayed or missed; this is not a guarantee of uninterrupted execution.

The runner resolves engine main/runtime-state to immutable commits and downloads only their databases into temporary storage. It records source commits, download times, byte hashes and failures. There is no engine clone, engine push, fresh exchange request, paid model call or order. Dashboard code/configuration supplies the declared research policy and stock list. The configured list was 60 at the audited baseline.

The small durable ledger lives in `checkpoints/` on this dashboard repository’s separate `research-evidence` branch. At most one evidence commit per completed session is allowed. Unchanged inputs produce no push; same-session corrections or method changes remain diagnostic artifacts. Only new session checkpoints enter the durable ledger. Daily research makes no push to main and does not restart Streamlit. Detailed observations and all 60 candle charts are Actions artifacts retained for 90 days; databases and portfolio data are excluded.

A first checkpoint is a retrospective baseline. Subsequent ideas may qualify as prospective only if frozen after the completed session and before the next configured session opens. Execution outcomes are research price scenarios, not trades. Prior pending setups carry forward; revisions, incompatible methods and missing outcomes stay visible for review. Continuing setups count once. Cross-stock and cross-pattern dependence remains.

Freshness uses `session_calendar`, whose holiday list may be incomplete. Missing expected-session data produces an explicit failed run, with no new durable checkpoint. Stock-session continuity uses the union of stored dates and cannot detect a session missing for every stock. Source labels alone do not independently reconcile corporate actions or prices. Actual costs, fillability and adjusted history need further evidence.

Before changing a live rule, require later unseen sessions, enough independent resolved outcomes, realistic execution costs, comparison with the unchanged engine, drawdowns, coverage, calibration and uncertainty. This workflow never promotes a rule itself. Existing news, intraday and swing routines remain in place.

Publication stages only compact checkpoints, uses normal push and plain rebase for benign races, and stops on substantive conflicts. The optional manual installer applies a reviewed checksum-locked source patch once, runs safeguards, removes the patch and publishes code to main from the cloud. It cannot modify workflow files; those are installed through the signed-in GitHub UI.

Verify deployment in this repository’s Actions tab: a successful cloud run and the dated evidence branch are required before reporting the schedule active. The earlier engine-side installer failed at push and introduced no research implementation. Both temporary installer files were removed from engine main (cleanup commits e962940 and bfed5c9).
