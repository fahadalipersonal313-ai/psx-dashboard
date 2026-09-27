"""orderbook.py — ingest hand-captured L1 order-book CSVs.

You capture snapshots from a broker terminal with the console snippet in
tools/investify_l1.js and drop the CSV into orderbook/. Every engine run
re-scans that folder and ingests anything new.

THIS IS A MEASUREMENT DATASET, NOT A LIVE INPUT, and nothing reads it into a
signal. Three reasons, all of them structural rather than temporary:
  * one symbol at a time, captured by hand in bursts — not 50 symbols always-on
  * L1 goes stale within minutes, while the engine cycles every 15
  * it is UNMEASURED, and on this repo's own history every unmeasured input that
    looked meaningful (OBV divergence, score velocity, the confluence gate) went
    on to measure flat or negative

The point is to accumulate sessions so `imbalance` can be graded through
measure.py. If it earns its place, wire it in then — as a ranker or sizer, never
a veto, because every veto measured here rejected a better subset than it passed.

Consecutive identical states are collapsed. The first live capture produced 37
rows containing only 5 distinct states (7.4x duplication): the broker page
repaints when something changes, not on the sampler's clock, so storing every
sample would inflate any future n by ~7x and quietly break the independence
checks measure.py exists to enforce.
"""

import os
import re
import csv
import glob
import logging
from datetime import datetime, timedelta, timezone

import config

log = logging.getLogger("orderbook")

FOLDER = os.path.join(config.BASE_DIR, "orderbook")
# The snippet writes t as HH:MM:SS with no date. Recover the date from the
# filename's epoch-ms (book.save() names files nrl_book_<epoch>.csv), else the
# file's mtime. A wrong DATE would silently scatter snapshots across sessions.
_EPOCH_IN_NAME = re.compile(r"(\d{13})")
_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# The browser writes the clock time of whoever captured it — PKT for this user —
# while the engine runs in UTC. Stored naive, an 11:15 PKT snapshot reads as 90
# minutes in the FUTURE on a UTC host, and a future timestamp trivially passes a
# "younger than N minutes" test. Normalise to UTC on the way in.
CAPTURE_TZ_OFFSET_HOURS = float(os.environ.get("ORDERBOOK_TZ_OFFSET", "5"))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _file_date(path):
    """-> 'YYYY-MM-DD' for a file, from an ISO date or epoch-ms in its name,
    falling back to its modification time."""
    base = os.path.basename(path)
    m = _ISO_DATE.search(base)
    if m:
        return m.group(1)
    m = _EPOCH_IN_NAME.search(base)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000).strftime("%Y-%m-%d")
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")


def _stamp(raw_t, day):
    """Row clock time + file date -> ISO-8601 UTC timestamp."""
    t = (raw_t or "").strip()
    if not t:
        return None
    try:
        if "T" in t or len(t) > 10:      # already a full timestamp
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=CAPTURE_TZ_OFFSET_HOURS)))
        else:
            dt = datetime.fromisoformat(f"{day}T{t}").replace(
                tzinfo=timezone(timedelta(hours=CAPTURE_TZ_OFFSET_HOURS)))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_csv(path):
    """-> list of snapshot dicts, consecutive duplicates collapsed."""
    day = _file_date(path)
    out, last_state = [], None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            sym = (r.get("symbol") or "").strip().upper()
            ts = _stamp(r.get("t") or r.get("time") or r.get("captured_at"), day)
            if not sym or not ts:
                continue
            bid, ask = _num(r.get("bid_price")), _num(r.get("ask_price"))
            bv, av = _num(r.get("bid_volume")), _num(r.get("ask_volume"))
            if bid is None and ask is None:
                continue
            # What actually changed. Identical consecutive states are the page
            # not having repainted, not new information.
            state = (bid, ask, bv, av, _num(r.get("volume")))
            if state == last_state:
                continue
            last_state = state
            out.append({
                "symbol": sym, "captured_at": ts,
                "bid_price": bid, "ask_price": ask,
                "bid_volume": bv, "ask_volume": av,
                "spread": _num(r.get("spread")),
                "last_price": _num(r.get("last_price")) or _num(r.get("current")),
                "day_volume": _num(r.get("volume")),
                "day_low": _num(r.get("day_low")),
                "day_high": _num(r.get("day_high")),
                # Derived, so a future measurement does not have to re-derive it.
                # None rather than a fabricated 0 when a side is missing/empty.
                "imbalance": (round(bv / av, 4) if bv is not None and av else None),
                "spread_pct": (round((ask - bid) / bid * 100, 4)
                               if bid and ask and bid > 0 else None),
                "source": os.path.basename(path),
            })
    return out


def ingest(db, folder=FOLDER):
    """Scan the folder and store anything new. Returns (files, rows, inserted).

    Re-ingesting is a no-op: the primary key is (symbol, captured_at), so the
    folder can be rescanned on every cycle without duplicating.
    """
    if not os.path.isdir(folder):
        return 0, 0, 0
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    rows = []
    for p in files:
        try:
            rows += parse_csv(p)
        except Exception as e:
            log.warning("order-book CSV unreadable (%s): %s", os.path.basename(p), e)
    inserted = db.save_order_book(rows)
    if inserted:
        log.info("Order book: %d files, %d distinct states, %d new rows stored",
                 len(files), len(rows), inserted)
    return len(files), len(rows), inserted


def main():
    import database as db
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db.init_db()
    files, rows, new = ingest(db)
    print(f"files: {files} | distinct states: {rows} | newly stored: {new}")
    cov = db.order_book_coverage()
    print(f"\ncoverage: {cov['syms']} symbols, {cov['n']} snapshots, "
          f"{cov['days']} sessions, {cov['lo']} .. {cov['hi']}")
    print("\nUNMEASURED and wired into nothing. Grade `imbalance` with "
          "measure.render() before proposing it as a ranker or sizer.")


if __name__ == "__main__":
    main()
