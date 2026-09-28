"""psx_market_watch.py — live OHLC for the WHOLE market in one request.

dps.psx.com.pk/market-watch returns every listed company's current session row.
Two things that buys us:

  * REAL running High/Low. The engine otherwise reconstructs the day's range from
    15-minute intraday polls, which must UNDERSTATE it whenever an extreme prints
    between polls. This is the exchange's own figure.
  * One request instead of 100. fetch_eod + latest_quote is 2 calls x 50 symbols
    per cycle; DPS load was the leading suspect in the 2026-08-27 blackout, and
    this cuts the volume rather than parallelising it.

Columns are keyed off the header's `data-name` attributes and to_bars() RAISES
when one is missing, rather than mis-mapping High into Low — a wrong High/Low
corrupts every derived ATR and looks perfectly normal on a chart. Same rule that
psx_historical follows.

`python psx_market_watch.py` prints the observed header and a few rows: run that
on a runner before trusting anything here. This sandbox cannot reach DPS.
"""

import re
import sys
import logging

import requests

import config

log = logging.getLogger("market_watch")

URL = config.PSX_DPS_BASE + "/market-watch"
TIMEOUT = 30
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Referer": "https://dps.psx.com.pk/",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

_TAG = re.compile(r"<[^>]+>")
_TH = re.compile(r"<th\b([^>]*)>", re.I)
_NAME = re.compile(r'data-name="([^"]+)"', re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)

# What to_bars() needs. `current` is the live price; DPS labels it variously, so
# CURRENT_ALIASES is tried in order and the first present wins.
REQUIRED = ("symbol", "open", "high", "low", "volume")
CURRENT_ALIASES = ("current", "close", "last", "price", "ldcp")


def _clean(cell):
    txt = _TAG.sub(" ", cell)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&").replace(",", "").strip()
    return re.sub(r"\s+", " ", txt)


def _num(txt):
    try:
        return float(txt)
    except (TypeError, ValueError):
        return None


def parse(html):
    """-> (column_names, [row_dict, ...]) with every column, keyed by data-name.

    Deliberately lossless: the probe needs to see columns to_bars() ignores.
    """
    head, _, body = html.partition("</thead>")
    names = []
    for attrs in _TH.findall(head):
        m = _NAME.search(attrs)
        names.append(m.group(1).lower() if m else "")
    if not names:
        return [], []
    rows = []
    for raw in _ROW.findall(body):
        cells = [_clean(c) for c in _CELL.findall(raw)]
        if len(cells) < len(names):
            continue
        rows.append(dict(zip(names, cells)))
    return names, rows


def to_bars(names, rows, symbols=None):
    """-> {SYMBOL: {open, high, low, current, volume}} for the configured universe.

    Raises ValueError if the table no longer carries what we need, so a site
    change fails loudly instead of silently producing wrong ranges.
    """
    missing = [c for c in REQUIRED if c not in names]
    if missing:
        raise ValueError(f"market-watch is missing columns {missing}; saw {names}")
    cur = next((c for c in CURRENT_ALIASES if c in names), None)
    if not cur:
        raise ValueError(f"no current-price column; tried {CURRENT_ALIASES}, saw {names}")

    wanted = set(symbols or config.STOCKS)
    out = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        if sym not in wanted:
            continue
        bar = {k: _num(r.get(k)) for k in ("open", "high", "low", "volume")}
        bar["current"] = _num(r.get(cur))
        # A zero is not a price: PSX reports 0.00 for a name that did not trade,
        # and it would collapse every range derived from it (same rule as the
        # historical backfill, where 10 such bars had to be deleted).
        if not bar["high"] or not bar["low"] or bar["high"] <= 0 or bar["low"] <= 0:
            continue
        out[sym] = bar
    return out


def fetch(session=None, symbols=None):
    """-> ({SYMBOL: bar}, meta). Never raises on network error: returns ({}, meta)
    so a market-watch failure can never take the price path down with it."""
    s = session or requests
    try:
        r = s.get(URL, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        names, rows = parse(r.text)
        bars = to_bars(names, rows, symbols)
        return bars, {"ok": True, "rows": len(rows), "matched": len(bars), "error": None}
    except Exception as e:
        log.warning("market-watch fetch failed: %s", e)
        return {}, {"ok": False, "rows": 0, "matched": 0, "error": str(e)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    r = requests.get(URL, headers=HEADERS, timeout=TIMEOUT)
    print(f"HTTP {r.status_code} | {len(r.content)} bytes")
    names, rows = parse(r.text)
    print(f"\ncolumns ({len(names)}): {names}")
    print(f"rows: {len(rows)}")
    if rows:
        print(f"\nfirst raw row: {rows[0]}")
    try:
        bars = to_bars(names, rows)
        print(f"\nmatched {len(bars)} of {len(config.STOCKS)} configured symbols")
        for sym in list(config.STOCKS)[:8]:
            print(f"  {sym:8s} {bars.get(sym)}")
    except ValueError as e:
        print(f"\nto_bars FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
