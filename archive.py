"""archive.py — move settled decision history to a durable file, verifiably.

The tracked database is committed on every 15-minute cycle, so its size is a
per-push cost. `decisions` + `decision_snapshots` are the unbounded part: 0.67 MB
per 60-symbol session, roughly 14 MB a month, against GitHub's 100 MB hard limit.
Compression bought headroom; it did not stop the growth.

These rows are immutable audit records, so they are ARCHIVED, never discarded.
The order is deliberate and is the whole point of the module:

    export  ->  verify  ->  purge

`purge` refuses to delete anything the archive cannot reproduce byte-for-byte.
A restore is not a hope, it is checked before the live rows go away, which is the
"durable destination and a verified restore" the operating constraints ask for.

Snapshots are shared: several decisions can point at one snapshot_hash. A
snapshot is only archived and purged when NO retained decision still references
it, otherwise a live row would be left pointing at nothing.

The archive is a plain SQLite file. It restores with `python main.py restore
<path>` and needs no code from this repo to read -- a format that outlives the
tool is part of being durable.
"""

import os
import sqlite3

import database as db
from decision_engine import digest

TABLES = ("decisions", "decision_snapshots")


def _rows(c, sql, args=()):
    return [tuple(r) for r in c.execute(sql, args)]


def _table_rows(c, table):
    """Rows of `table`, or [] when the archive predates it. An older archive
    must keep verifying, not start failing because the format grew."""
    try:
        return _rows(c, f"SELECT * FROM {table}")
    except sqlite3.OperationalError:
        return []


def manifest(decisions, snapshots, runs=()):
    """Content digest of everything the archive claims to hold.

    `runs` defaults to empty so an archive written before runs were archivable
    re-derives exactly the digest it stored.
    """
    runs = list(runs)
    body = [sorted(str(r) for r in decisions), sorted(str(r) for r in snapshots)]
    if runs:
        body.append(sorted(str(r) for r in runs))
    return {"decisions": len(decisions), "snapshots": len(snapshots),
            "runs": len(runs), "digest": digest(body)}


def export(cutoff_session, out_path):
    """Write every decision strictly older than `cutoff_session` to `out_path`.

    Nothing is deleted here. Returns the manifest the verify step re-derives.
    """
    with db.conn() as c:
        decisions = _rows(c, "SELECT * FROM decisions WHERE session < ?", (cutoff_session,))
        keep_hashes = {r[0] for r in _rows(
            c, "SELECT DISTINCT snapshot_hash FROM decisions WHERE session >= ?",
            (cutoff_session,)) if r[0]}
        moving = {r[4] for r in decisions if r[4]}
        # Snapshots referenced by NO decision at all. They accumulate when a
        # decision set is regenerated under a new contract hash: the old rows
        # go, their snapshots stay, and nothing afterwards ever names them. They
        # are still immutable audit records, so they are archived with the rest
        # rather than dropped.
        unref = {r[0] for r in _rows(c, "SELECT hash FROM decision_snapshots")} - {
            r[0] for r in _rows(
                c, "SELECT DISTINCT snapshot_hash FROM decisions "
                   "WHERE snapshot_hash IS NOT NULL")}
        orphans = sorted((moving | unref) - keep_hashes)
        snapshots = []
        for i in range(0, len(orphans), 400):
            chunk = orphans[i:i + 400]
            snapshots += _rows(c, "SELECT * FROM decision_snapshots WHERE hash IN (%s)"
                               % ",".join("?" * len(chunk)), chunk)
    if os.path.exists(out_path):
        os.remove(out_path)
    out = sqlite3.connect(out_path)
    out.executescript("""
        CREATE TABLE decisions (symbol TEXT, session TEXT, version TEXT,
          config_hash TEXT, snapshot_hash TEXT, state BLOB, payload BLOB);
        CREATE TABLE decision_snapshots (hash TEXT PRIMARY KEY, payload BLOB);
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT);""")
    out.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", decisions)
    out.executemany("INSERT INTO decision_snapshots VALUES (?,?)", snapshots)
    man = manifest(decisions, snapshots)
    out.executemany("INSERT INTO archive_meta VALUES (?,?)",
                    [("cutoff_session", cutoff_session), ("digest", man["digest"]),
                     ("decisions", str(man["decisions"])), ("snapshots", str(man["snapshots"]))])
    out.commit(); out.close()
    man["path"] = out_path
    man["bytes"] = os.path.getsize(out_path)
    return man


def verify(path, expected=None):
    """Re-derive the archive's digest from its own contents.

    Deliberately recomputed rather than trusting the stored value: a truncated
    or half-written file would still carry a plausible-looking meta row.
    """
    a = sqlite3.connect(path)
    try:
        decisions = _rows(a, "SELECT * FROM decisions")
        snapshots = _rows(a, "SELECT * FROM decision_snapshots")
        runs = _table_rows(a, "runs")
        stored = dict(_rows(a, "SELECT key, value FROM archive_meta"))
    finally:
        a.close()
    got = manifest(decisions, snapshots, runs)
    ok = got["digest"] == stored.get("digest")
    if expected:
        ok = ok and got["digest"] == expected["digest"]
    return {"ok": bool(ok), "recomputed": got["digest"], "stored": stored.get("digest"),
            "decisions": got["decisions"], "snapshots": got["snapshots"],
            "runs": got["runs"]}


def restore(path):
    """Put archived rows back. Idempotent -- restoring twice changes nothing."""
    a = sqlite3.connect(path)
    decisions = _rows(a, "SELECT * FROM decisions")
    snapshots = _rows(a, "SELECT * FROM decision_snapshots")
    a.close()
    with db.conn() as c:
        c.executemany("INSERT OR IGNORE INTO decision_snapshots VALUES (?,?)", snapshots)
        c.executemany("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?,?)", decisions)
    return {"decisions": len(decisions), "snapshots": len(snapshots)}


def purge(path, cutoff_session):
    """Delete the archived rows from the live database -- only if the archive
    verifies AND actually contains this cutoff. Refuses otherwise."""
    check = verify(path)
    a = sqlite3.connect(path)
    stored_cutoff = dict(_rows(a, "SELECT key, value FROM archive_meta")).get("cutoff_session")
    a.close()
    if not check["ok"]:
        return {"purged": 0, "refused": "archive failed verification", **check}
    if stored_cutoff != cutoff_session:
        return {"purged": 0,
                "refused": f"archive cutoff {stored_cutoff!r} != requested {cutoff_session!r}"}
    with db.conn() as c:
        keep = {r[0] for r in _rows(
            c, "SELECT DISTINCT snapshot_hash FROM decisions WHERE session >= ?",
            (cutoff_session,)) if r[0]}
        moving = {r[0] for r in _rows(
            c, "SELECT DISTINCT snapshot_hash FROM decisions WHERE session < ?",
            (cutoff_session,)) if r[0]}
        unref = {r[0] for r in _rows(c, "SELECT hash FROM decision_snapshots")} - {
            r[0] for r in _rows(
                c, "SELECT DISTINCT snapshot_hash FROM decisions "
                   "WHERE snapshot_hash IS NOT NULL")}
        # Only ever the snapshots this archive actually holds: a snapshot the
        # export did not capture must never be deleted here.
        archive_connection = sqlite3.connect(path)
        try:
            held = {r[0] for r in _rows(archive_connection,
                                      "SELECT hash FROM decision_snapshots")}
        finally:
            archive_connection.close()
        orphans = sorted(((moving | unref) - keep) & held)
        n = c.execute("DELETE FROM decisions WHERE session < ?", (cutoff_session,)).rowcount
        s = 0
        for i in range(0, len(orphans), 400):
            chunk = orphans[i:i + 400]
            s += c.execute("DELETE FROM decision_snapshots WHERE hash IN (%s)"
                           % ",".join("?" * len(chunk)), chunk).rowcount
    return {"purged": n, "snapshots_purged": s, "verified": True}


# ---------------------------------------------------------------------------
# Selection-based archiving.
#
# The original export moves decisions OLDER THAN a session. That is the wrong
# instrument for the two things actually filling the database:
#
#   retired contracts - v3 and v4 decisions are unreachable by the running
#       engine already. Every live reader filters on version AND config_hash
#       (database.py, upward_candidates.py), so a superseded version can never
#       be returned. They are pure audit records, and archiving them costs the
#       engine nothing. NOT time-based: a v5 decision from the same day stays.
#
#   old runs - one row per symbol per cycle. prune() already day-dedupes them
#       past runs_full_days; what remains is history, and history belongs in a
#       file rather than in every push.
#
# Both keep the export -> verify -> purge order, and purge re-derives the
# selection itself rather than trusting the archive's own description of it.
# ---------------------------------------------------------------------------

def _selection(c, kind, value):
    """The rows a selection names, re-derived from the LIVE database."""
    if kind == "retired_decisions":
        # value is the version that must survive.
        decisions = _rows(c, "SELECT * FROM decisions WHERE version <> ?", (value,))
        keep = {r[0] for r in _rows(
            c, "SELECT DISTINCT snapshot_hash FROM decisions WHERE version = ?",
            (value,)) if r[0]}
        moving = {r[4] for r in decisions if r[4]}
        return {"decisions": decisions, "snapshots": sorted(moving - keep), "runs": []}
    if kind == "runs_before":
        return {"decisions": [], "snapshots": [],
                "runs": _rows(c, "SELECT * FROM runs WHERE run_time < ?", (value,))}
    raise ValueError(f"unknown selection {kind!r}")


def export_selection(kind, value, out_path):
    """Write the rows a selection names to `out_path`. Deletes nothing."""
    with db.conn() as c:
        sel = _selection(c, kind, value)
        snapshots = []
        orphans = sel["snapshots"]
        for i in range(0, len(orphans), 400):
            chunk = orphans[i:i + 400]
            snapshots += _rows(c, "SELECT * FROM decision_snapshots WHERE hash IN (%s)"
                               % ",".join("?" * len(chunk)), chunk)
        run_cols = [r[1] for r in _rows(c, "PRAGMA table_info(runs)")]
    decisions, runs = sel["decisions"], sel["runs"]
    if os.path.exists(out_path):
        os.remove(out_path)
    out = sqlite3.connect(out_path)
    out.executescript("""
        CREATE TABLE decisions (symbol TEXT, session TEXT, version TEXT,
          config_hash TEXT, snapshot_hash TEXT, state BLOB, payload BLOB);
        CREATE TABLE decision_snapshots (hash TEXT PRIMARY KEY, payload BLOB);
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT);""")
    # The runs schema is wide and has changed before, so it is copied from the
    # live table rather than restated here, which would rot.
    out.execute("CREATE TABLE runs (%s)" % ",".join(f'"{c_}"' for c_ in run_cols))
    out.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", decisions)
    out.executemany("INSERT INTO decision_snapshots VALUES (?,?)", snapshots)
    if runs:
        out.executemany("INSERT INTO runs VALUES (%s)" % ",".join("?" * len(run_cols)),
                        runs)
    man = manifest(decisions, snapshots, runs)
    out.executemany("INSERT INTO archive_meta VALUES (?,?)",
                    [("selection_kind", kind), ("selection_value", str(value)),
                     ("digest", man["digest"]), ("decisions", str(man["decisions"])),
                     ("snapshots", str(man["snapshots"])), ("runs", str(man["runs"]))])
    out.commit(); out.close()
    man["path"] = out_path
    man["bytes"] = os.path.getsize(out_path)
    return man


def purge_selection(path, kind, value):
    """Delete a selection's rows -- only if the archive verifies AND holds this
    exact selection. Deletes only rows the archive actually contains."""
    check = verify(path)
    a = sqlite3.connect(path)
    meta = dict(_rows(a, "SELECT key, value FROM archive_meta"))
    held_runs = {r[0] for r in _table_rows(a, "runs")}
    held_dec = {(r[0], r[1], r[2], r[3]) for r in _rows(a, "SELECT * FROM decisions")}
    held_snap = {r[0] for r in _rows(a, "SELECT * FROM decision_snapshots")}
    a.close()
    if not check["ok"]:
        return {"purged": 0, "refused": "archive failed verification", **check}
    if (meta.get("selection_kind"), meta.get("selection_value")) != (kind, str(value)):
        return {"purged": 0,
                "refused": f"archive holds {meta.get('selection_kind')!r}/"
                           f"{meta.get('selection_value')!r}, not {kind!r}/{value!r}"}
    n = s = r = 0
    with db.conn() as c:
        sel = _selection(c, kind, value)
        for row in sel["decisions"]:
            if (row[0], row[1], row[2], row[3]) in held_dec:
                n += c.execute("DELETE FROM decisions WHERE symbol=? AND session=? "
                               "AND version=? AND config_hash=?", row[:4]).rowcount
        orphans = [h for h in sel["snapshots"] if h in held_snap]
        for i in range(0, len(orphans), 400):
            chunk = orphans[i:i + 400]
            s += c.execute("DELETE FROM decision_snapshots WHERE hash IN (%s)"
                           % ",".join("?" * len(chunk)), chunk).rowcount
        run_ids = [row[0] for row in sel["runs"] if row[0] in held_runs]
        for i in range(0, len(run_ids), 400):
            chunk = run_ids[i:i + 400]
            r += c.execute("DELETE FROM runs WHERE id IN (%s)"
                           % ",".join("?" * len(chunk)), chunk).rowcount
    return {"purged": n, "snapshots_purged": s, "runs_purged": r, "verified": True}


def restore_selection(path):
    """Put an archived selection back. Idempotent."""
    a = sqlite3.connect(path)
    decisions = _rows(a, "SELECT * FROM decisions")
    snapshots = _rows(a, "SELECT * FROM decision_snapshots")
    runs = _table_rows(a, "runs")
    run_cols = [r[1] for r in _rows(a, "PRAGMA table_info(runs)")]
    a.close()
    with db.conn() as c:
        c.executemany("INSERT OR IGNORE INTO decision_snapshots VALUES (?,?)", snapshots)
        c.executemany("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?,?,?)", decisions)
        if runs:
            c.executemany("INSERT OR IGNORE INTO runs (%s) VALUES (%s)"
                          % (",".join(f'"{x}"' for x in run_cols),
                             ",".join("?" * len(run_cols))), runs)
    return {"decisions": len(decisions), "snapshots": len(snapshots), "runs": len(runs)}
