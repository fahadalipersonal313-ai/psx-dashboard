"""psx_historical.py — backfill real daily High/Low from PSX's own historical view.

PSX DPS end-of-day returns [ts, close, volume, open] and no High or Low, so ATR,
ADX and a true CMF could only ever be computed from the ~50 bars the intraday
poller had banked. `dps.psx.com.pk/historical` answers a POST with a date and
returns EVERY listed company's OHLCV for that session, so one request per
trading day backfills the whole universe.

Markup confirmed on a runner (hl_probe, 2026-09-01, date=2026-08-28):

    <table id="historicalTable">
      <thead><tr><th data-name="symbol">SYMBOL</th>
                 <th data-name="ldcp">LDCP</th> ... </tr></thead>
    row: ['<strong>786</strong>', '23.27', '23.30', '23.30', '23.00', '23.05',
          '<i class="icon-down-dir"></i> -0.22', '... -0.95%', '47,039']

Verified internally consistent: 23.05 - 23.27 = -0.22, and
LOW 23.00 <= CLOSE 23.05 <= HIGH 23.30. Columns are keyed off the header's
`data-name` attributes rather than fixed positions, so a reordered table fails
loudly instead of silently writing High into Low — a wrong High or Low would
corrupt every ATR derived from it and would not look wrong on a chart.

A non-trading day returns the SAME 200 with an empty tbody (the first probe hit
a Saturday and read as a broken endpoint). Empty simply means no session.

WRITE POLICY: INSERT OR IGNORE, so bars already banked from the intraday feed
are kept. Note the trade-off — the portal's High/Low is the exchange's official
figure, while an intraday-derived one is reconstructed from 15-minute polls and
must UNDERSTATE the true range whenever an extreme printed between polls. For
the ~50 overlapping days the less accurate value therefore wins by default.
Pass --prefer-official to replace those instead.
"""

import sys
import re
import time
import logging
import datetime as dt

import requests

import config
import database as db
import ssl_compat

log = logging.getLogger("psx_historical")

URL = "https://dps.psx.com.pk/historical"
SOURCE = "PSX DPS historical (official)"
TIMEOUT = 30
DEFAULT_PAUSE = 1.0
DEFAULT_YEARS = 5
# Needed: the request is refused without a browser-ish UA and a same-site
# Referer (confirmed by probe, which used exactly these).
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0 Safari/537.36"),
    "Referer": "https://dps.psx.com.pk/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

_TAG_RE = re.compile(r"<[^>]+>")
_TH_RE = re.compile(r"<th\b([^>]*)>", re.I)
_NAME_RE = re.compile(r'data-name="([^"]+)"', re.I)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)

WANTED = ("symbol", "open", "high", "low", "close", "volume")


def _clean(cell):
    """Strip markup and formatting: cells carry <strong> and direction <i> icons,
    and volume is comma-grouped."""
    txt = _TAG_RE.sub(" ", cell)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace(",", "").strip())
    return re.sub(r"\s+", " ", txt)


def _num(txt):
    try:
        return float(txt)
    except (TypeError, ValueError):
        return None


def parse(html):
    """-> list of {symbol, open, high, low, close, volume}. [] for a closed day.

    Raises ValueError if the header no longer carries the columns we need, so a
    site change surfaces as a failure rather than as silently wrong prices.
    """
    head, _, body = html.partition("</thead>")
    names = [(_NAME_RE.search(a).group(1).lower() if _NAME_RE.search(a) else "")
             for a in _TH_RE.findall(head)]
    missing = [w for w in WANTED if w not in names]
    if missing:
        if not names:
            return []          # no table at all (e.g. an error page)
        raise ValueError(f"historical table is missing columns {missing}; "
                         f"saw {names}")
    idx = {w: names.index(w) for w in WANTED}

    out = []
    for raw in _ROW_RE.findall(body):
        cells = [_clean(c) for c in _CELL_RE.findall(raw)]
        if len(cells) < len(names):
            continue
        sym = cells[idx["symbol"]].upper()
        if not sym:
            continue
        sym = sym.split()[0]  # PSX status markers (XD/NC/etc.) are not ticker text.
        row = {"symbol": sym}
        for f in ("open", "high", "low", "close", "volume"):
            row[f] = _num(cells[idx[f]])
        # A bar with no high or low is useless here and is never invented.
        if row["high"] is None or row["low"] is None:
            continue
        # PSX reports O=H=L=0.00 for a session in which a name did not trade,
        # carrying only a stale close (seen on JVDC and BNL, 10 bars in 5 years).
        # A zero is not a price: it would collapse every ATR and range that
        # touches it. Drop the bar rather than store a fabricated-looking one.
        if row["high"] <= 0 or row["low"] <= 0:
            continue
        out.append(row)
    return out


def fetch_day(date_str, session=None):
    """One session's full-market OHLCV. [] means the market was closed."""
    s = session or requests
    r = s.post(URL, data={"date": date_str}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return parse(r.text)


def _trading_dates(years):
    """Calendar dates, newest first, weekends skipped locally. Public holidays
    are not knowable here — those simply come back empty and cost one request."""
    today = dt.date.today()
    start = today - dt.timedelta(days=int(round(years * 365.25)))
    d, out = today, []
    while d >= start:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def backfill(years=DEFAULT_YEARS, pause=DEFAULT_PAUSE, prefer_official=False,
             symbols=None, progress_every=25):
    """Walk back `years` of sessions, banking H/L for the configured universe.

    Paced deliberately: DPS load was the leading suspect in the 2026-08-27
    blackout, so this is one request per second, sequential, single session.
    """
    wanted = set(symbols or config.STOCKS)
    dates = _trading_dates(years)
    sess = requests.Session()
    stats = {"days": 0, "open_days": 0, "closed_days": 0,
             "rows": 0, "written": 0, "errors": 0}
    log.info("Backfilling %d weekday sessions (~%.0f min at %.1fs pacing)",
             len(dates), len(dates) * pause / 60.0, pause)

    for i, date_str in enumerate(dates, 1):
        stats["days"] += 1
        try:
            rows = fetch_day(date_str, sess)
        except Exception as e:
            stats["errors"] += 1
            log.warning("%s: %s", date_str, e)
            time.sleep(pause)
            continue
        if not rows:
            stats["closed_days"] += 1
        else:
            stats["open_days"] += 1
            for row in rows:
                if row["symbol"] not in wanted:
                    continue
                stats["rows"] += 1
                stats["written"] += db.save_hl_bar(
                    row["symbol"], date_str, row["open"], row["high"],
                    row["low"], row["close"], row["volume"], SOURCE,
                    overwrite=prefer_official)
        if i % progress_every == 0:
            log.info("  %d/%d dates — %d sessions, %d bars written",
                     i, len(dates), stats["open_days"], stats["written"])
        time.sleep(pause)
    return stats


def main():
    ssl_compat.enable()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:]
    years = float(args[0]) if args else DEFAULT_YEARS
    pause = float(args[1]) if len(args) > 1 else DEFAULT_PAUSE
    prefer = "--prefer-official" in args
    db.init_db()
    stats = backfill(years=years, pause=pause, prefer_official=prefer)
    print(f"\ndates tried      : {stats['days']}")
    print(f"trading sessions : {stats['open_days']}")
    print(f"closed/holiday   : {stats['closed_days']}")
    print(f"request errors   : {stats['errors']}")
    print(f"universe rows    : {stats['rows']}")
    print(f"bars written     : {stats['written']}")
    print(f"daily_ohlc total : {db.daily_ohlc_count()}")


if __name__ == "__main__":
    main()
