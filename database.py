"""database.py — SQLite storage. Every run, signal, and outcome is recorded
so the engine can learn from history. Nothing is ever fabricated; missing
outcomes stay NULL until real prices arrive."""

import os
import json
import zlib as _zlib
import uuid
from contextvars import ContextVar
from data_quality import bar_error, source_priority
import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

import config

log = logging.getLogger("database")

RELIABILITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cohort_candidates (
 id TEXT PRIMARY KEY, symbol TEXT, session TEXT, version TEXT, config_hash TEXT,
 emitted TEXT, pre_veto TEXT, payload TEXT NOT NULL,
 UNIQUE(symbol,session,version,config_hash));
CREATE TABLE IF NOT EXISTS cohort_outcomes (
 candidate_id TEXT PRIMARY KEY, status TEXT, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quarantined_bars (
 id INTEGER PRIMARY KEY, symbol TEXT, date TEXT, payload TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS run_batches (
 id TEXT PRIMARY KEY, started_at TEXT, status TEXT, expected INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS decision_snapshots (
 hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
 symbol TEXT, session TEXT, version TEXT, config_hash TEXT, snapshot_hash TEXT,
 state TEXT, payload TEXT, PRIMARY KEY(symbol,session,version,config_hash));
CREATE TABLE IF NOT EXISTS opportunities (
 id TEXT PRIMARY KEY, symbol TEXT, session TEXT, version TEXT, config_hash TEXT,
 snapshot_hash TEXT, payload TEXT NOT NULL,
 UNIQUE(symbol,session,version,config_hash));
CREATE TABLE IF NOT EXISTS opportunity_outcomes (
 opportunity_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS opportunity_outcome_events (
 hash TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, payload TEXT NOT NULL);
"""


@contextmanager
def analysis_batch(expected):
    batch_id = uuid.uuid4().hex
    with conn() as c:
        c.execute("INSERT INTO run_batches VALUES (?,?,?,?,NULL)",
                  (batch_id, datetime.now().isoformat(), 'running', expected))
    try:
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            token = _transaction.set(c)
            try:
                yield batch_id
                rows = c.execute('SELECT symbol,signal FROM runs WHERE batch_id=?', (batch_id,)).fetchall()
                if len(rows) != expected or len({r['symbol'] for r in rows}) != expected or all(r['signal'] == 'No data' for r in rows):
                    raise ValueError('Incomplete or entirely unavailable analysis batch')
                c.execute("UPDATE run_batches SET status='complete' WHERE id=?", (batch_id,))
            finally:
                _transaction.reset(token)
    except Exception as exc:
        with conn() as c:
            c.execute("UPDATE run_batches SET status='failed',error=? WHERE id=?", (str(exc), batch_id))
        raise


def pack_payload(text):
    """Store decision/snapshot JSON zlib-compressed.

    These two tables were 59.4 MB of a 104.7 MB database across 1,492 rows,
    because each decision embeds a full copy of the snapshot that
    decision_snapshots already holds under the snapshot_hash the same row
    carries, and the ~16.5 KB config is written into all 1,492 rows although
    only THREE distinct configs exist.

    Compression is used rather than dropping the duplicated members because
    snapshot_hash and config_hash are digests OF that content: it round-trips
    byte-identically, so every hash still verifies. Measured on a copy of the
    live database: 104.7 MB -> 60.9 MB, 0 mismatches across 722 decisions.
    """
    return _zlib.compress(text.encode(), 6)


def unpack_payload(value):
    """Read a payload written either compressed or as legacy plain text."""
    if isinstance(value, (bytes, bytearray)):
        try:
            return _zlib.decompress(value).decode()
        except _zlib.error:
            return bytes(value).decode()
    return value


def previous_decision(symbol, session, version, config_hash):
    with conn() as c:
        row = c.execute('SELECT state FROM decisions WHERE symbol=? AND session<? AND version=? AND config_hash=? ORDER BY session DESC LIMIT 1',
                        (symbol, session, version, config_hash)).fetchone()
    return json.loads(unpack_payload(row['state'])) if row else None


def save_decision(decision):
    from decision_engine import canonical, digest, state
    from swing_evaluation import opportunity
    with conn() as c:
        snapshot_hash = decision.get('snapshot_hash')
        if snapshot_hash:
            c.execute('INSERT OR IGNORE INTO decision_snapshots VALUES (?,?)',
                      (snapshot_hash, pack_payload(canonical(decision['snapshot']))))
        c.execute('INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?,?)',
                  (decision['symbol'], decision['decision_session'], decision['strategy_version'],
                   decision['config_hash'], snapshot_hash,
                   pack_payload(canonical(state(decision))), pack_payload(canonical(decision))))
        _record_cohort_candidate(c, decision, snapshot_hash)
        if decision['signal']['signal'] not in ('Buy', 'Strong Buy'):
            return None
        active = c.execute("""SELECT o.id FROM opportunities o LEFT JOIN opportunity_outcomes x ON x.opportunity_id=o.id
          WHERE o.symbol=? AND (x.status IS NULL OR x.status IN ('pending','unavailable')) LIMIT 1""", (decision['symbol'],)).fetchone()
        if active:
            return active['id']
        item = opportunity(decision)
        oid = digest([decision['symbol'], decision['decision_session'], decision['strategy_version'], decision['config_hash']])
        c.execute('INSERT OR IGNORE INTO opportunities VALUES (?,?,?,?,?,?,?)',
                  (oid, decision['symbol'], decision['decision_session'], decision['strategy_version'], decision['config_hash'], snapshot_hash, canonical(item)))
        return oid



def _record_cohort_candidate(c, decision, snapshot_hash):
    """Bank every plausible candidate, whatever the vetoes then did to it.

    `opportunities` is created only for an EMITTED Buy, so with the regime gate
    holding every candidate at Watch the engine has produced 782 decisions, zero
    Buys and zero opportunities -- it cannot accumulate evidence about itself,
    and the gates cannot be measured because the rejected side is never graded.

    This is the rejected side. It records the candidate, what tier the score
    reached BEFORE vetoes, what was actually emitted, and which downgrades fired,
    so the standing question -- is the subset a gate rejects worse than the
    subset it passes? -- becomes answerable from real forward bars instead of a
    backtest.

    Measurement only. Nothing reads these rows into a score or a signal, per the
    rule that new findings are surfaced, not wired in.
    """
    from decision_engine import canonical, digest
    from swing_evaluation import opportunity
    from data_quality import valid_levels
    try:
        tech = decision.get('technical') or {}
        sig = decision.get('signal') or {}
        score = (decision.get('scoring') or {}).get('final_score')
        floor = config.SIGNAL_THRESHOLDS.get('watch')
        if score is None or floor is None or score < floor:
            return None
        if not valid_levels(tech.get('price'), tech.get('stop_loss'),
                            tech.get('target1'), tech.get('target2')):
            return None
        item = opportunity(decision)
        item['emitted_signal'] = sig.get('signal')
        item['final_score'] = score
        item['raw_qualified'] = bool(sig.get('raw_qualified'))
        # The reasons carry the downgrade text, which is how a specific gate is
        # attributed later without re-deriving it.
        item['reasons'] = list(sig.get('reasons') or [])
        item['relative_strength'] = tech.get('relative_strength')
        item['cmf'] = tech.get('cmf')
        pre = 'Strong Buy' if sig.get('raw_qualified') else (
            'Buy' if score >= config.SIGNAL_THRESHOLDS.get('buy', 75) else 'Watch')
        cid = digest(['cohort', decision['symbol'], decision['decision_session'],
                      decision['strategy_version'], decision['config_hash']])
        c.execute('INSERT OR IGNORE INTO cohort_candidates VALUES (?,?,?,?,?,?,?,?)',
                  (cid, decision['symbol'], decision['decision_session'],
                   decision['strategy_version'], decision['config_hash'],
                   sig.get('signal'), pre, canonical(item)))
        return cid
    except Exception as exc:                     # never cost a decision
        log.warning('cohort candidate not recorded for %s: %s',
                    decision.get('symbol'), exc)
        return None


def cohort_summary():
    """Keep different rules separate in every cohort summary."""
    with conn() as c:
        return [dict(r) for r in c.execute('''SELECT k.version,k.config_hash,x.status,COUNT(*) AS n
            FROM cohort_outcomes x JOIN cohort_candidates k ON k.id=x.candidate_id
            GROUP BY k.version,k.config_hash,x.status''')]


def open_cohort_candidates():
    with conn() as c:
        return [dict(r) for r in c.execute("""SELECT k.* FROM cohort_candidates k
             LEFT JOIN cohort_outcomes x ON x.candidate_id=k.id
             WHERE x.status IS NULL OR x.status IN ('pending','unavailable')""")]


def save_cohort_outcome(cid, outcome):
    with conn() as c:
        c.execute("INSERT INTO cohort_outcomes VALUES (?,?,?) ON CONFLICT(candidate_id) "
                  "DO UPDATE SET status=excluded.status,payload=excluded.payload "
                  "WHERE cohort_outcomes.status IN ('pending','unavailable')",
                  (cid, outcome['status'], json.dumps(outcome, allow_nan=False)))


def open_opportunities():
    with conn() as c:
        return [dict(r) for r in c.execute("""SELECT o.* FROM opportunities o LEFT JOIN opportunity_outcomes x ON x.opportunity_id=o.id
             WHERE x.status IS NULL OR x.status IN ('pending','unavailable')""")]


def save_opportunity_outcome(oid, outcome):
    from decision_engine import canonical, digest
    with conn() as c:
        c.execute('INSERT OR IGNORE INTO opportunity_outcome_events VALUES (?,?,?)',
                  (digest([oid,outcome]), oid, canonical(outcome)))
        c.execute('INSERT INTO opportunity_outcomes VALUES (?,?,?) ON CONFLICT(opportunity_id) DO UPDATE SET status=excluded.status,payload=excluded.payload WHERE opportunity_outcomes.status IN (\'pending\',\'unavailable\')',
                  (oid, outcome['status'], json.dumps(outcome, allow_nan=False)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL, volume REAL,
    technical_score REAL, sentiment_score REAL, macro_news_score REAL,
    final_score REAL, signal TEXT, confidence REAL,
    stop_loss REAL, target1 REAL, target2 REAL,
    support REAL, resistance REAL, risk_level TEXT,
    shariah_status TEXT, data_quality TEXT,
    main_reason TEXT, main_risk TEXT,
    price_next_run REAL, price_1d REAL, price_3d REAL, price_7d REAL,
    outcome TEXT,             -- 'worked' / 'failed' / NULL (pending)
    tech_flags TEXT,          -- JSON: which sub-indicators were bullish (learning loop)
    conviction_streak INTEGER, -- deprecated: was misleading (15-min run counts
                                -- mistaken for independent confirmations), no
                                -- longer written; old rows kept as-is
    confluence INTEGER,       -- 0-4: how many independent signal dimensions agree
    buy_zone_low REAL,        -- pullback buy-zone (band around the reference EMA)
    buy_zone_high REAL,
    accumulation_candidate INTEGER, -- 1 = OBV/CMF/volume signature of quiet buying
    accumulation_reasons TEXT,      -- JSON list of which signals fired
    cmf REAL,                       -- Chaikin Money Flow (needs real daily H/L)
    obv_divergence_bullish INTEGER  -- 1 = price flat/down while OBV rising
);
CREATE INDEX IF NOT EXISTS idx_runs_symbol_time ON runs(symbol, run_time);

CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, ts TEXT, price REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, source TEXT, title TEXT UNIQUE, link TEXT,
    published TEXT, sentiment REAL, symbols TEXT
);

-- Every news rating the Claude routine has ever produced, kept immutably so a
-- story can be followed across months. news_ai_ratings.json is OVERWRITTEN each
-- run; without this the previous stage of a running story is destroyed and the
-- rater meets development three with no knowledge of one and two.
-- thread_key is written by the rater when IT judges two reads to be the same
-- story; nothing infers a link from text. outcome_* are excess returns over the
-- same-day cross-sectional median, descriptive only -- news carries 0.0 weight.
CREATE TABLE IF NOT EXISTS news_memory (
    id TEXT PRIMARY KEY,
    symbol TEXT, as_of TEXT, provider TEXT,
    rating TEXT, causality TEXT, horizon TEXT, confidence REAL,
    reason TEXT, sources TEXT, thread_key TEXT,
    outcome_1d REAL, outcome_3d REAL, outcome_5d REAL,
    outcome_10d REAL, outcome_20d REAL,
    UNIQUE (symbol, as_of, sources)
);
CREATE INDEX IF NOT EXISTS idx_news_memory_symbol ON news_memory(symbol, as_of);

CREATE TABLE IF NOT EXISTS sentiment_history (
    run_time TEXT, symbol TEXT, score REAL, bullish INTEGER,
    bearish INTEGER, neutral INTEGER, mentions INTEGER, flags TEXT
);

CREATE TABLE IF NOT EXISTS indicator_accuracy (
    indicator TEXT, symbol TEXT, hits INTEGER DEFAULT 0,
    misses INTEGER DEFAULT 0, PRIMARY KEY (indicator, symbol)
);

-- Real daily OHLC banked from the intraday feed (PSX EOD has no High/Low).
-- Accumulates over time so true ATR/ADX/candles become possible later.
CREATE TABLE IF NOT EXISTS daily_ohlc (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
    close REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, date)
);

-- L1 order-book snapshots captured by hand from a broker terminal and dropped
-- into orderbook/ as CSV. A MEASUREMENT DATASET, not a live input: it is one
-- symbol at a time, in bursts, and goes stale within minutes, so nothing reads
-- it into a signal. It accumulates so imbalance can be graded with measure.py
-- before anyone proposes wiring it in.
CREATE TABLE IF NOT EXISTS order_book (
    symbol TEXT, captured_at TEXT,
    bid_price REAL, ask_price REAL, bid_volume REAL, ask_volume REAL,
    spread REAL, last_price REAL, day_volume REAL, day_low REAL, day_high REAL,
    imbalance REAL, spread_pct REAL, source TEXT,
    PRIMARY KEY (symbol, captured_at)
);

-- Every large move in the banked history, split into market / sector /
-- stock-specific (see market_factors.py) with the setup that preceded it and
-- the forward EXCESS return that followed. Built from price data, so it is a
-- far larger and wholly independent sample than the engine's graded signals.
-- cause_label stays NULL until real research fills it: a cause is never
-- inferred from price alone.
CREATE TABLE IF NOT EXISTS events (
    symbol TEXT, date TEXT, sector TEXT,
    ret_pct REAL, sigma_move REAL, direction TEXT,
    market_pct REAL, sector_pct REAL, idio_pct REAL,
    cause_class TEXT, has_sector_peers INTEGER,
    gap_pct REAL, close_position REAL, range_atr REAL, vol_ratio REAL,
    trend_50_pct REAL, mom_20_pct REAL, rs_60_pct REAL, rsi_14 REAL,
    range_52w_pos REAL,
    fwd_1d_excess REAL, fwd_3d_excess REAL, fwd_5d_excess REAL,
    fwd_10d_excess REAL, fwd_20d_excess REAL,
    at_limit INTEGER, turnover_pkr REAL, tradeable INTEGER,
    cause_label TEXT, cause_source TEXT,
    PRIMARY KEY (symbol, date)
);

-- Splits, bonuses and rights detected as beyond-circuit price cuts. PSX serves
-- UNADJUSTED prices, so a 1:6 bonus arrives as a -14.3% "loss". Raw bars are
-- never rewritten: the adjustment is applied on read, so the exchange's record
-- stays intact and a mistaken detection is reversible.
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol TEXT, ex_date TEXT, ratio REAL, factor REAL, implied REAL,
    kind TEXT, pct REAL,
    PRIMARY KEY (symbol, ex_date)
);

-- Full PSX DPS end-of-day history (~1,200 bars/symbol). fetch_eod already
-- downloaded this every cycle and discarded it; persisting it gives years of
-- indicator history instead of the ~50 days daily_ohlc had accrued.
-- DELIBERATELY a separate table: DPS EOD carries NO High/Low, and writing
-- null-H/L rows into daily_ohlc would clobber the real intraday-derived highs
-- and lows that the ATR/ADX/momentum paths read. Never merge the two.
CREATE TABLE IF NOT EXISTS daily_eod (
    symbol TEXT, date TEXT, open REAL, close REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, date)
);
"""


_transaction = ContextVar("transaction", default=None)


@contextmanager
def conn():
    active = _transaction.get()
    if active is not None:
        yield active
        return
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        # Lightweight migrations: add newer columns to `runs` if they're missing
        # (CREATE TABLE IF NOT EXISTS won't add columns to a pre-existing table).
        existing = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        for col, decl in (("account_admission", "TEXT"), ("account_reason", "TEXT"),
                          ("strategy_version", "TEXT"), ("decision_session", "TEXT"),
                          ("config_hash", "TEXT"), ("snapshot_hash", "TEXT"),
                          ("raw_qualified", "INTEGER"), ("batch_id", "TEXT"),
                          ("relative_strength", "REAL"), ("market_regime", "TEXT"),
                          ("tech_flags", "TEXT"), ("conviction_streak", "INTEGER"),
                          ("confluence", "INTEGER"),
                          ("buy_zone_low", "REAL"), ("buy_zone_high", "REAL"),
                          ("accumulation_candidate", "INTEGER"),
                          ("accumulation_reasons", "TEXT"),
                          ("cmf", "REAL"), ("obv_divergence_bullish", "INTEGER"),
                          ("early_watch", "INTEGER"), ("early_reason", "TEXT"),
                          ("outcome_7d", "TEXT"),
                          # Continuous confluence axes (JSON) + their mean.
                          # Stored for measurement only — nothing reads them
                          # into a signal until each axis earns it.
                          ("confluence_axes", "TEXT"),
                          ("confluence_composite", "REAL")):
            if col not in existing:
                c.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")
        c.execute("""CREATE TABLE IF NOT EXISTS focus_brief (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       run_time TEXT, symbol TEXT, action TEXT, payload TEXT)""")
    with conn() as c:
        c.executescript(RELIABILITY_SCHEMA)
        existing = {r[1] for r in c.execute("PRAGMA table_info(corporate_actions)")}
        for col, decl in (("verified", "INTEGER DEFAULT 0"), ("source", "TEXT"), ("known_at", "TEXT"), ("volume_factor", "REAL")):
            if col not in existing:
                c.execute(f"ALTER TABLE corporate_actions ADD COLUMN {col} {decl}")
    log.info("Database initialised at %s", config.DB_PATH)


def save_focus_brief(brief: dict):
    import json as _json
    with conn() as c:
        c.execute("INSERT INTO focus_brief (run_time, symbol, action, payload) "
                  "VALUES (?,?,?,?)",
                  (brief.get("run_time"), brief.get("symbol"), brief.get("action"),
                   _json.dumps(brief, default=str)))


def last_focus_brief(symbol=None):
    import json as _json
    q = "SELECT * FROM focus_brief"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol.upper())
    q += " ORDER BY id DESC LIMIT 1"
    try:
        with conn() as c:
            r = c.execute(q, args).fetchone()
    except sqlite3.OperationalError:
        # Table absent: a DB committed before this feature existed, on a host
        # where init_db could not create it. No brief is not an error.
        return None
    if not r:
        return None
    try:
        return _json.loads(r["payload"])
    except (TypeError, ValueError):
        return None


def save_run(row: dict) -> int:
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    with conn() as c:
        cur = c.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})",
                        list(row.values()))
        return cur.lastrowid


def save_price(symbol, ts, price, volume, source):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?)",
                  (symbol, ts, price, volume, source))


def save_daily_ohlc(symbol, date, o, h, l, c, volume, source):
    return save_hl_bar(symbol, date, o, h, l, c, volume, source, overwrite=True)


def eod_history_state(symbol):
    """(rows, latest_date) already stored for a symbol — the cheap guard that
    keeps a 24-cycle day from rewriting 1,200 identical rows 24 times."""
    with conn() as c:
        r = c.execute("SELECT COUNT(*) n, MAX(date) d FROM daily_eod WHERE symbol=?",
                      (symbol,)).fetchone()
    return r["n"], r["d"]


def save_eod_history(symbol, rows, source="PSX DPS end-of-day"):
    """Bank the full EOD series. rows: iterable of (date, open, close, volume).

    REPLACE is safe here (unlike daily_ohlc) because this table holds no
    High/Low to lose — a later fetch only ever refines the same four fields.
    """
    payload = [(symbol, d, o, c_, v, source) for d, o, c_, v in rows]
    if not payload:
        return 0
    with conn() as cx:
        cx.executemany("""INSERT OR REPLACE INTO daily_eod
            (symbol, date, open, close, volume, source)
            VALUES (?,?,?,?,?,?)""", payload)
    return len(payload)


def get_eod_history(symbol, limit=1500):
    """Banked EOD bars, oldest-first. No High/Low by construction — callers
    needing true ranges must use get_daily_ohlc instead."""
    with conn() as c:
        rows = c.execute(
            """SELECT date, open, close, volume FROM daily_eod
               WHERE symbol=? ORDER BY date DESC LIMIT ?""",
            (symbol, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def save_hl_bar(symbol, date, o, h, l, c, volume, source, overwrite=False):
    bar = dict(date=date, open=o, high=h, low=l, close=c, volume=volume, source=source)
    error = bar_error(bar)
    with conn() as cx:
        if error:
            cx.execute("INSERT INTO quarantined_bars(symbol,date,payload,reason) VALUES (?,?,?,?)",
                       (symbol, str(date), json.dumps(bar, default=str), error))
            return 0
        old = cx.execute("SELECT source FROM daily_ohlc WHERE symbol=? AND date=?", (symbol,date)).fetchone()
        if old:
            old_priority, new_priority = source_priority(old['source']), source_priority(source)
            if new_priority < old_priority or (new_priority == old_priority and not overwrite):
                return 0
        cx.execute("INSERT OR REPLACE INTO daily_ohlc VALUES (?,?,?,?,?,?,?,?)",
                   (symbol,date,float(o),float(h),float(l),float(c),float(volume),source))
        return 1


def save_order_book(rows):
    """Insert L1 snapshots. IGNORE on (symbol, captured_at) so re-ingesting the
    same CSV is a no-op — the folder is re-scanned every run."""
    if not rows:
        return 0
    cols = ("symbol", "captured_at", "bid_price", "ask_price", "bid_volume",
            "ask_volume", "spread", "last_price", "day_volume", "day_low",
            "day_high", "imbalance", "spread_pct", "source")
    with conn() as c:
        before = c.execute("SELECT COUNT(*) n FROM order_book").fetchone()["n"]
        c.executemany(
            f"INSERT OR IGNORE INTO order_book ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [tuple(r.get(k) for k in cols) for r in rows])
        after = c.execute("SELECT COUNT(*) n FROM order_book").fetchone()["n"]
    return after - before


def order_book_latest(symbol, max_age_minutes=30):
    """Most recent snapshot for a symbol, or None when there is nothing fresh.

    L1 goes stale in minutes — an 11:15 imbalance says nothing at 14:00 — so a
    freshness gate is mandatory rather than optional.
    """
    with conn() as c:
        r = c.execute("""SELECT * FROM order_book WHERE symbol=?
                         ORDER BY captured_at DESC LIMIT 1""", (symbol,)).fetchone()
    if not r:
        return None
    try:
        ts = datetime.fromisoformat(r["captured_at"])
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        age = (now - ts).total_seconds() / 60
    except Exception:
        return None
    # A NEGATIVE age means the snapshot is stamped in the future — a timezone or
    # clock mismatch, not fresh data. Treating it as fresh is how a bad timestamp
    # silently bypasses a staleness gate (see the as_of bypass in CLAUDE.md), so
    # reject it rather than trust it.
    if age < 0:
        log.warning("order_book snapshot for %s is future-dated (%.0f min) — "
                    "treating as unusable", symbol, -age)
        return None
    return dict(r, age_minutes=round(age, 1)) if age <= max_age_minutes else None


def order_book_coverage():
    """(symbols, snapshots, sessions, first, last) — how much evidence exists yet."""
    with conn() as c:
        r = c.execute("""SELECT COUNT(DISTINCT symbol) syms, COUNT(*) n,
                                COUNT(DISTINCT substr(captured_at,1,10)) days,
                                MIN(captured_at) lo, MAX(captured_at) hi
                         FROM order_book""").fetchone()
    return dict(r)


EVENT_COLS = ("symbol", "date", "sector", "ret_pct", "sigma_move", "direction",
              "market_pct", "sector_pct", "idio_pct", "cause_class",
              "has_sector_peers", "gap_pct", "close_position", "range_atr",
              "vol_ratio", "trend_50_pct", "mom_20_pct", "rs_60_pct", "rsi_14",
              "range_52w_pos", "fwd_1d_excess", "fwd_3d_excess", "fwd_5d_excess",
              "fwd_10d_excess", "fwd_20d_excess", "at_limit", "turnover_pkr",
              "tradeable", "cause_label", "cause_source")


def save_events(events):
    """Store detected events. REPLACE so a rebuild refreshes forward returns as
    later prices arrive, but cause_label is preserved: researched labels must
    survive a rebuild, or the expensive half of the work is lost every time."""
    if not events:
        return 0
    with conn() as c:
        before = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        labels = {(r["symbol"], r["date"]): (r["cause_label"], r["cause_source"])
                  for r in c.execute("SELECT symbol, date, cause_label, cause_source "
                                     "FROM events WHERE cause_label IS NOT NULL")}
        rows = []
        for e in events:
            keep = labels.get((e["symbol"], e["date"]))
            if keep:
                e = dict(e, cause_label=keep[0], cause_source=keep[1])
            rows.append(tuple(e.get(k) for k in EVENT_COLS))
        c.executemany(
            f"INSERT OR REPLACE INTO events ({','.join(EVENT_COLS)}) "
            f"VALUES ({','.join('?' * len(EVENT_COLS))})", rows)
        after = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
    return after - before


def get_events(symbol=None, cause_class=None, direction=None, since=None, limit=5000):
    q, args = "SELECT * FROM events WHERE 1=1", []
    for col, val in (("symbol", symbol), ("cause_class", cause_class),
                     ("direction", direction)):
        if val:
            q += f" AND {col}=?"; args.append(val)
    if since:
        q += " AND date>=?"; args.append(since)
    q += " ORDER BY date DESC LIMIT ?"; args.append(limit)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def unlabelled_events(limit=40, min_abs_ret=0.0):
    """Biggest idiosyncratic moves with no researched cause yet — the queue for
    news research, ordered so the ones that actually matter come first."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM events
               WHERE cause_label IS NULL AND cause_class='idiosyncratic'
                 AND ABS(ret_pct) >= ?
               ORDER BY ABS(idio_pct) DESC LIMIT ?""", (min_abs_ret, limit))]


def save_corporate_actions(actions):
    if not actions:
        return 0
    cols = ("symbol", "ex_date", "ratio", "factor", "implied", "kind", "pct", "verified", "source", "known_at", "volume_factor")
    with conn() as c:
        before = c.execute("SELECT COUNT(*) n FROM corporate_actions").fetchone()["n"]
        c.executemany(
            f"INSERT OR IGNORE INTO corporate_actions ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [tuple(a.get(k) for k in cols) for a in actions])
        after = c.execute("SELECT COUNT(*) n FROM corporate_actions").fetchone()["n"]
    return after - before


def get_corporate_actions(symbol=None):
    q, args = "SELECT * FROM corporate_actions", []
    if symbol:
        q += " WHERE symbol=?"; args.append(symbol)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY ex_date", args)]


def daily_ohlc_count(symbol=None):
    q = "SELECT COUNT(*) n FROM daily_ohlc"
    args = []
    if symbol:
        q += " WHERE symbol=?"; args.append(symbol)
    with conn() as c:
        return c.execute(q, args).fetchone()["n"]


def get_daily_ohlc(symbol, limit=90):
    """Real banked daily OHLC bars for a symbol, oldest-first (list of dicts:
    date, open, high, low, close, volume). Used for true ATR/ADX once enough
    bars accumulate; empty/short -> caller falls back to the EOD-close proxies."""
    with conn() as c:
        rows = c.execute(
            """SELECT date, open, high, low, close, volume, source FROM daily_ohlc
               WHERE symbol=? ORDER BY date DESC LIMIT ?""",
            (symbol, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def momentum_bursts(symbols, min_pct=5.0):
    """Today's biggest 1-day price movers (e.g. upper/lower circuit hits),
    purely informational — NOT a trading signal and NOT fed back into
    scoring/signal_generator. The score-based pipeline deliberately ignores
    single-day spikes (see CLAUDE.md); this surfaces them anyway so a circuit
    move is never silently invisible on the dashboard. Read-only: uses the
    latest stored run price + locally banked daily_ohlc, no live fetch.
    Returns a list sorted by |pct_move| descending."""
    out = []
    for sym in symbols:
        r = last_run(sym)
        if not r or not r.get("price"):
            continue
        bars = get_daily_ohlc(sym, limit=22)
        if not bars:
            continue
        today = datetime.now().strftime("%Y-%m-%d")
        closed_bars = [b for b in bars if b["date"] != today]
        if not closed_bars:
            continue
        prev_close = closed_bars[-1]["close"]
        if not prev_close:
            continue
        pct_move = (r["price"] / prev_close - 1) * 100
        if abs(pct_move) < min_pct:
            continue
        vols = [b["volume"] for b in closed_bars[-20:] if b.get("volume")]
        avg_vol = sum(vols) / len(vols) if vols else None
        out.append({"symbol": sym, "price": r["price"], "prev_close": prev_close,
                    "pct_move": round(pct_move, 1), "today_vol": r.get("volume"),
                    "avg_vol": avg_vol, "signal": r.get("signal"),
                    "final_score": r.get("final_score")})
    return sorted(out, key=lambda x: abs(x["pct_move"]), reverse=True)


def save_news(items):
    with conn() as c:
        for it in items:
            c.execute("""INSERT OR IGNORE INTO news
                (fetched_at, source, title, link, published, sentiment, symbols)
                VALUES (?,?,?,?,?,?,?)""",
                (it["fetched_at"], it["source"], it["title"], it["link"],
                 it.get("published"), it.get("sentiment"),
                 ",".join(it.get("symbols", []))))


def recent_news(hours=48, symbol=None):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    q = "SELECT * FROM news WHERE fetched_at > ?"
    args = [cutoff]
    if symbol:
        q += " AND symbols LIKE ?"
        args.append(f"%{symbol}%")
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY fetched_at DESC", args)]


def save_sentiment(run_time, symbol, score, bullish, bearish, neutral,
                   mentions, flags):
    with conn() as c:
        c.execute("INSERT INTO sentiment_history VALUES (?,?,?,?,?,?,?,?)",
                  (run_time, symbol, score, bullish, bearish, neutral,
                   mentions, ",".join(flags)))


def previous_sentiment(symbol):
    with conn() as c:
        r = c.execute("""SELECT score FROM sentiment_history WHERE symbol=?
                         ORDER BY run_time DESC LIMIT 1""", (symbol,)).fetchone()
        return r["score"] if r else None


def last_run(symbol):
    with conn() as c:
        r = c.execute("""SELECT * FROM runs WHERE symbol=?
                         ORDER BY run_time DESC LIMIT 1""", (symbol,)).fetchone()
        return dict(r) if r else None


def run_history(symbol=None, limit=500):
    q = "SELECT * FROM runs"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    q += " ORDER BY run_time DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def pending_outcomes():
    """Runs whose forward prices are not yet fully recorded."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM runs WHERE price_7d IS NULL
               ORDER BY run_time ASC""")]


def update_outcome(run_id, field, value):
    assert field in ("price_next_run", "price_1d", "price_3d", "price_7d",
                     "outcome", "outcome_7d")
    with conn() as c:
        c.execute(f"UPDATE runs SET {field}=? WHERE id=?", (value, run_id))


def bump_indicator(indicator, symbol, hit: bool):
    with conn() as c:
        c.execute("""INSERT INTO indicator_accuracy (indicator, symbol, hits, misses)
                     VALUES (?,?,?,?)
                     ON CONFLICT(indicator, symbol) DO UPDATE SET
                     hits = hits + ?, misses = misses + ?""",
                  (indicator, symbol, int(hit), int(not hit),
                   int(hit), int(not hit)))


def indicator_stats(symbol=None):
    q = "SELECT * FROM indicator_accuracy"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def signal_accuracy(symbol=None):
    """Win rate per signal type from completed runs."""
    q = """SELECT signal, outcome, COUNT(*) n FROM runs
           WHERE strategy_version IS NULL AND outcome IS NOT NULL"""
    args = []
    if symbol:
        q += " AND symbol=?"
        args.append(symbol)
    q += " GROUP BY signal, outcome"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def signal_accuracy_summary(symbol=None, min_reliable_n=20):
    """Per-signal win rate with sample size + reliability flag.
    Returns one row per signal type with:
      * n_total / n_worked / n_failed
      * win_rate_pct
      * n_confidence: 'high' (n>=min_reliable_n), 'medium' (n>=min_reliable_n/2),
                      'low' (anything less) — small samples are noise, not edge.
    This is the honest cousin of signal_accuracy(): same data, but explicitly
    flagged when N is too small to trust. Use this for any UI that displays the
    win rate to a human about to risk money."""
    rows = signal_accuracy(symbol=symbol)
    agg = {}
    for r in rows:
        s = r["signal"] or "?"
        a = agg.setdefault(s, {"signal": s, "n_total": 0, "n_worked": 0,
                                "n_failed": 0})
        a["n_total"] += r["n"]
        if r["outcome"] == "worked":
            a["n_worked"] += r["n"]
        elif r["outcome"] == "failed":
            a["n_failed"] += r["n"]
    out = []
    for a in agg.values():
        graded = a["n_worked"] + a["n_failed"]
        a["win_rate_pct"] = (round(a["n_worked"] / graded * 100, 1)
                              if graded else None)
        if a["n_total"] >= min_reliable_n:
            a["n_confidence"] = "high"
        elif a["n_total"] >= max(5, min_reliable_n // 2):
            a["n_confidence"] = "medium"
        else:
            a["n_confidence"] = "low"
        out.append(a)
    return sorted(out, key=lambda r: -r["n_total"])


def gradeable_runs():
    """Every run that has enough forward data to grade (price + 3-day price),
    oldest-first so cohort_forward_move sees peers already filled."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM runs
               WHERE strategy_version IS NULL AND price IS NOT NULL AND price_3d IS NOT NULL AND price > 0
               ORDER BY run_time ASC""")]


def reset_indicator_accuracy():
    """Wipe the learned hit/miss tallies so they can be rebuilt from scratch
    (used by backtester.regrade_all after a grading-rule change)."""
    with conn() as c:
        c.execute("DELETE FROM indicator_accuracy")


def cohort_forward_move(date_str, exclude_symbol=None, days=3):
    """Median forward % change over `days` (3 or 7) across every stock scored on
    `date_str` (the calendar date of run_time) that already has that forward
    price filled. This is the engine's own universe acting as the 'market'
    benchmark — used to grade RELATIVELY (did it underperform the market?)
    instead of on absolute move, which is meaningless in a trending tape.
    Returns None when too few peers have completed to form a benchmark."""
    col = "price_7d" if days == 7 else "price_3d"
    with conn() as c:
        rows = c.execute(
            f"""SELECT symbol, price, {col} AS fwd FROM runs
                WHERE date(run_time)=? AND price IS NOT NULL
                  AND {col} IS NOT NULL AND price > 0""",
            (date_str,)).fetchall()
    moves = [(r["fwd"] / r["price"] - 1) * 100 for r in rows
             if exclude_symbol is None or r["symbol"] != exclude_symbol]
    if len(moves) < 5:          # need a real cross-section, not 1-2 names
        return None
    moves.sort()
    mid = len(moves) // 2
    return (moves[mid] if len(moves) % 2 else (moves[mid - 1] + moves[mid]) / 2)


def _runs_has_accum_column():
    """Whether the runs table has the accumulation_candidate column. Older or
    read-only deployments (where the ALTER TABLE migration could not run) may
    not — the accumulation views then degrade to empty instead of crashing."""
    with conn() as c:
        return "accumulation_candidate" in {
            r[1] for r in c.execute("PRAGMA table_info(runs)")}


def accumulation_streak(symbol, lookback=10):
    """How many of the most recent runs (consecutively, from now backward)
    were flagged accumulation_candidate=1. 0 if the latest run wasn't flagged."""
    if not _runs_has_accum_column():
        return 0
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT accumulation_candidate FROM runs WHERE symbol=?
               ORDER BY run_time DESC LIMIT ?""", (symbol, lookback))]
    streak = 0
    for r in rows:
        if r["accumulation_candidate"]:
            streak += 1
        else:
            break
    return streak


def accumulating_now(lookback=10, min_streak=1):
    """Symbols currently flagged as accumulation candidates, each with how many
    consecutive recent runs (sessions) the flag has held — the 'last few
    sessions' view. Only counts symbols flagged in their MOST RECENT run."""
    out = []
    if not _runs_has_accum_column():
        return out
    with conn() as c:
        latest_ids = [r["symbol"] for r in c.execute(
            """SELECT symbol FROM runs r WHERE r.id =
               (SELECT MAX(id) FROM runs WHERE symbol = r.symbol)
               AND r.accumulation_candidate = 1""")]
    for sym in latest_ids:
        streak = accumulation_streak(sym, lookback)
        if streak >= min_streak:
            row = last_run(sym)
            out.append({"symbol": sym, "streak": streak,
                        "reasons": row.get("accumulation_reasons"),
                        "price": row.get("price"), "signal": row.get("signal"),
                        "final_score": row.get("final_score"),
                        "cmf": row.get("cmf")})
    return sorted(out, key=lambda x: x.get("final_score") or 0, reverse=True)




def prune(runs_full_days=7, news_days=None):
    """Shrink the tracked DB by dropping rows nothing reads. Returns a summary.

    The whole database is committed every 15-minute cycle, so its size is a
    per-push cost, and it had reached 54 MB — past GitHub's 50 MB warning and
    heading for the 100 MB hard limit. Three tables carried almost all of it,
    and in every case the surplus was rows no code path ever queries:

      prices  - data_fetcher.latest_quote reads only the LAST row per symbol,
                so 41,888 rows existed to serve 50.
      news    - was pruned to a 48h-ish window; NO LONGER, see news_days.
      runs    - 18.7x duplicated: 15-minute polling stores ~19 rows per symbol
                per day, while every analysis in this repo day-dedupes before
                trusting a number (see CLAUDE.md, "a win rate alone is not
                evidence"). Full fidelity is kept for the last `runs_full_days`
                because backtester grades up to 7 days forward and needs every
                row; older days keep the last row per symbol per day.

    Consequence worth knowing: signal_accuracy_summary's n values drop ~19x to
    their day-deduped truth. That is the honest count, not a loss.
    """
    with conn() as c:
        before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("runs", "news", "prices")}
        c.execute("""DELETE FROM prices WHERE ts < (SELECT MAX(ts) FROM prices)
                     AND rowid NOT IN (SELECT MAX(rowid) FROM prices GROUP BY symbol)""")
        # news_days=None KEEPS EVERY HEADLINE. The raw record is the
        # historical context a rating is judged against -- whether a
        # development is the third in a running story or the first anyone has
        # heard -- so deleting it destroys the only thing that can answer that.
        # It is also the cheapest table here: ~500 bytes a headline, ~74 a day.
        # Pass an integer to re-enable the window if size ever demands it.
        if news_days is not None:
            c.execute("""DELETE FROM news WHERE fetched_at <
                         datetime((SELECT MAX(fetched_at) FROM news), ?)""",
                      (f"-{news_days} days",))
        c.execute("""DELETE FROM runs WHERE run_time <
                     (SELECT datetime(MAX(run_time), ?) FROM runs)
                     AND id NOT IN (SELECT MAX(id) FROM runs
                                    GROUP BY symbol, substr(run_time, 1, 10))""",
                  (f"-{runs_full_days} days",))
        after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("runs", "news", "prices")}
    # VACUUM cannot run inside a transaction, so it needs its own connection.
    v = sqlite3.connect(config.DB_PATH)
    v.execute("VACUUM")
    v.close()
    mb = os.path.getsize(config.DB_PATH) / 1048576
    log.info("prune: %s -> %s, file now %.1f MB",
             {k: f"{v:,}" for k, v in before.items()},
             {k: f"{v:,}" for k, v in after.items()}, mb)
    return {"before": before, "after": after, "size_mb": round(mb, 1)}
