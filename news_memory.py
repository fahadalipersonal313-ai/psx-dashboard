"""news_memory.py — remember every news read, so a story can be followed.

A news story is rarely one event. A merger clears one regulator, then another,
then completes or collapses; a gas well is announced, drilled, tied in. Today the
routine writes news_ai_ratings.json and the NEXT run overwrites it, so every
earlier stage is destroyed. The rater then meets the third development in a
sequence with no idea the first two happened, and the reader sees "positive,
causal, 0.5" with no sense of whether that is a story building or a story
already priced in.

This keeps them. Three pieces:

  remember()    — persist every rating produced, immutably and idempotently.
                  Re-running over the same file changes nothing.
  history_for() — the prior reads on a symbol, newest first, for the routine to
                  analyse fresh news AGAINST rather than in isolation.
  grade()       — attach what the price actually did after each read.

On threading: this module does NOT guess which items belong to the same story.
Clustering headlines on shared words invents links that are not there, and a
wrong link is worse than none because it fabricates a narrative. Instead the
symbol's history is handed to the routine, which is an LLM and can judge
relatedness, and it may record its own `thread_key`. Nothing here asserts a
connection the rater did not make.

Grading is descriptive, not a score input. Per the standing rule, news carries
0.0 weight in config.WEIGHTS and this changes nothing about that: it exists so
"causal, high confidence" can eventually be checked against what happened
instead of being taken on faith.
"""

import json
import logging
import os

import config
import database as db

log = logging.getLogger("news_memory")

RATINGS_FILE = "news_ai_ratings.json"
HORIZONS = (1, 3, 5, 10, 20)


def _digest(*parts):
    from decision_engine import digest
    return digest([str(p) for p in parts])


RAW_FILE = "news_raw_24h.json"


def ingest_raw(path=RAW_FILE):
    """Bank every gathered headline permanently, rated or not.

    news.yml fetches the last 24h into news_raw_24h.json every hour, but the
    v3 rewrite stopped full_run from saving any of it: main.py passes an empty
    news list, so the `news` table's last write was 2026-09-05 and the
    dashboard's News tab has been empty ever since while the file was full.

    The raw record is the historical context the rater needs. A rating says
    "positive, causal" about one development; only the headline history says
    whether that development is the third in a run or the first anyone has
    heard. So every item is kept, including the macro ones no symbol claims.

    Idempotent: `news.title` is UNIQUE and this is INSERT OR IGNORE, so a
    headline keeps the fetched_at of when it was FIRST seen. Re-running over
    the same file changes nothing, and a story re-published later does not
    reset its own age.
    """
    if not os.path.exists(path):
        return {"stored": 0, "reason": f"{path} absent"}
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (ValueError, OSError) as exc:
        return {"stored": 0, "reason": f"unreadable: {exc}"}
    stamp = blob.get("fetched_at")
    if not stamp:
        # Same rule as remember(): an undated read cannot be placed in a
        # sequence, and inventing a timestamp would corrupt every later window.
        return {"stored": 0, "reason": "raw file carries no fetched_at"}
    items = blob.get("items") or []
    rows = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        sym = it.get("symbol") or ""
        rows.append({"fetched_at": stamp, "source": it.get("source") or "?",
                     "title": title, "link": it.get("url") or it.get("link") or "",
                     "published": it.get("published") or "",
                     # sentiment stays NULL: nothing here reads the text, and a
                     # fabricated score is worse than an absent one.
                     "sentiment": None,
                     # A LIST: save_news joins it, so a bare string would be
                     # stored one character at a time ("O,G,D,C").
                     "symbols": [] if (not sym or sym.startswith("_")) else [sym]})
    if not rows:
        return {"stored": 0, "reason": "no items in file"}
    with db.conn() as c:
        before = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    db.save_news(rows)
    with db.conn() as c:
        after = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    return {"stored": after - before, "seen": len(rows), "fetched_at": stamp}


def context_for(symbol, headline_days=180, rating_limit=12):
    """Everything remembered about a symbol: prior rated reads AND the raw
    headline history behind them. This is what a fresh item is analysed
    against, so it deliberately reaches much further back than any dashboard
    window."""
    rows = history_for(symbol, rating_limit)
    with db.conn() as c:
        heads = [dict(r) for r in c.execute(
            "SELECT fetched_at, source, title, link FROM news "
            "WHERE symbols LIKE ? AND fetched_at > datetime('now', ?) "
            "ORDER BY fetched_at DESC LIMIT 60",
            (f"%{symbol}%", f"-{int(headline_days)} days"))]
    return {"symbol": symbol, "ratings": rows, "headlines": heads}


def context_text(symbol, headline_days=180):
    """The same context as prompt-ready text. Empty when nothing is known --
    the rater must never be handed an invented backstory."""
    ctx = context_for(symbol, headline_days)
    parts = []
    thread = thread_summary(symbol)
    if thread:
        parts.append(thread)
    if ctx["headlines"]:
        parts.append(f"Headlines already seen for {symbol} "
                     f"(last {headline_days} days, newest first):")
        for h in ctx["headlines"]:
            parts.append(f"  {str(h['fetched_at'])[:10]}  [{h['source']}] {h['title']}")
    return "\n".join(parts)


def remember(path=RATINGS_FILE, as_of=None):
    """Persist every rating in `path`. Idempotent: the id is a digest of the
    symbol, the rating timestamp and the sources, so the same read is never
    stored twice and a re-run is a no-op."""
    if not os.path.exists(path):
        return {"stored": 0, "reason": f"{path} absent"}
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (ValueError, OSError) as exc:
        return {"stored": 0, "reason": f"unreadable: {exc}"}
    stamp = as_of or blob.get("as_of")
    if not stamp:
        # Without a timestamp a read cannot be placed in a sequence, which is
        # the entire point. Refuse rather than invent one.
        return {"stored": 0, "reason": "ratings file carries no as_of"}
    provider = blob.get("provider") or blob.get("model") or "unknown"
    rows = []
    for symbol, r in (blob.get("ratings") or {}).items():
        if not isinstance(r, dict):
            continue
        sources = json.dumps(r.get("sources") or [], sort_keys=True)
        rows.append((
            _digest(symbol, stamp, sources), symbol, stamp, provider,
            r.get("rating"), r.get("causality"), r.get("horizon"),
            float(r["confidence"]) if isinstance(r.get("confidence"), (int, float)) else None,
            (r.get("reason") or "")[:400], sources, r.get("thread_key"),
        ))
    if not rows:
        return {"stored": 0, "reason": "no ratings in file"}
    with db.conn() as c:
        before = c.execute("SELECT COUNT(*) FROM news_memory").fetchone()[0]
        c.executemany(
            "INSERT OR IGNORE INTO news_memory "
            "(id,symbol,as_of,provider,rating,causality,horizon,confidence,"
            " reason,sources,thread_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        after = c.execute("SELECT COUNT(*) FROM news_memory").fetchone()[0]
    return {"stored": after - before, "seen": len(rows), "as_of": stamp}


def history_for(symbol, limit=12):
    """Prior reads on this symbol, NEWEST FIRST. What the routine analyses
    fresh news against."""
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT as_of, rating, causality, horizon, confidence, reason, "
            "       thread_key, outcome_5d, outcome_20d "
            "FROM news_memory WHERE symbol=? ORDER BY as_of DESC LIMIT ?",
            (symbol, limit))]


def remembered_symbols():
    """Symbols with at least one banked read, most recently rated first."""
    with db.conn() as c:
        return [r[0] for r in c.execute(
            "SELECT symbol, MAX(as_of) m FROM news_memory "
            "GROUP BY symbol ORDER BY m DESC")]


def thread_summary(symbol, limit=8):
    """Compact plain-text history for a prompt. Empty string when there is no
    prior read — the routine must not be told a story exists when none does."""
    rows = history_for(symbol, limit)
    if not rows:
        return ""
    out = [f"Prior reads on {symbol} (newest first):"]
    for r in rows:
        conf = f"{r['confidence']:.0%}" if isinstance(r["confidence"], (int, float)) else "?"
        graded = ""
        if r["outcome_5d"] is not None:
            graded = f" -> 5d {r['outcome_5d']:+.2f}%"
            if r["outcome_20d"] is not None:
                graded += f", 20d {r['outcome_20d']:+.2f}%"
        out.append(f"  {r['as_of'][:10]}  {r['rating']}/{r['causality']} "
                   f"conf {conf}{graded}  {r['reason'][:120]}")
    return "\n".join(out)


def grade(horizons=HORIZONS):
    """Attach what the price actually did after each read.

    Excess over the same-day cross-sectional median, the benchmark this repo
    uses everywhere else: a positive call during a market-wide rally is not
    evidence the call was right.
    """
    import pandas as pd
    with db.conn() as c:
        pending = [dict(r) for r in c.execute(
            "SELECT id, symbol, as_of FROM news_memory WHERE outcome_5d IS NULL")]
    if not pending:
        return {"graded": 0}
    panel = {}
    for sym in {p["symbol"] for p in pending}:
        bars = db.get_daily_ohlc(sym, limit=100000)
        if bars:
            panel[sym] = pd.Series({b["date"]: b["close"] for b in bars})
    if not panel:
        return {"graded": 0, "reason": "no bars"}
    px = pd.DataFrame(panel).sort_index()
    # Universe-wide median needs more than the rated names, or "excess" is
    # measured against a handful of correlated stocks.
    wide = {}
    for sym in config.STOCKS:
        bars = db.get_daily_ohlc(sym, limit=100000)
        if bars:
            wide[sym] = pd.Series({b["date"]: b["close"] for b in bars})
    W = pd.DataFrame(wide).sort_index()
    updates = []
    for p in pending:
        sym, day = p["symbol"], str(p["as_of"])[:10]
        if sym not in px.columns:
            continue
        idx = list(px.index)
        after = [d for d in idx if d >= day]
        if not after:
            continue
        start = after[0]
        vals = {}
        for h in horizons:
            pos = idx.index(start)
            if pos + h >= len(idx):
                continue
            end = idx[pos + h]
            try:
                stock = px.at[end, sym] / px.at[start, sym] - 1
                cohort = (W.loc[end] / W.loc[start] - 1).median()
                vals[h] = float((stock - cohort) * 100)
            except (KeyError, ZeroDivisionError, TypeError):
                continue
        if 5 in vals:
            updates.append((vals.get(1), vals.get(3), vals.get(5),
                            vals.get(10), vals.get(20), p["id"]))
    if updates:
        with db.conn() as c:
            c.executemany(
                "UPDATE news_memory SET outcome_1d=?, outcome_3d=?, outcome_5d=?, "
                "outcome_10d=?, outcome_20d=? WHERE id=?", updates)
    return {"graded": len(updates), "pending": len(pending)}


def accuracy():
    """Has 'causal, confident' actually preceded a move? Descriptive only."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT rating, causality, confidence, outcome_5d, outcome_20d "
            "FROM news_memory WHERE outcome_5d IS NOT NULL")]
    if not rows:
        return {"n": 0, "note": "nothing graded yet"}
    import statistics
    def bucket(pred):
        sel = [r for r in rows if pred(r)]
        if not sel:
            return None
        return {"n": len(sel),
                "median_5d": round(statistics.median(r["outcome_5d"] for r in sel), 2),
                "positive_5d": round(
                    sum(r["outcome_5d"] > 0 for r in sel) / len(sel) * 100, 1)}
    return {
        "n": len(rows),
        "all": bucket(lambda r: True),
        "causal": bucket(lambda r: r["causality"] == "causal"),
        "correlated": bucket(lambda r: r["causality"] == "correlated"),
        "positive_causal": bucket(
            lambda r: r["causality"] == "causal" and str(r["rating"]).endswith("positive")),
        "confidence_ge_0.5": bucket(
            lambda r: isinstance(r["confidence"], (int, float)) and r["confidence"] >= 0.5),
        "note": "Descriptive. News carries 0.0 weight in config.WEIGHTS and "
                "these figures are not wired into any score.",
    }
