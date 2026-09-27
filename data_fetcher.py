"""data_fetcher.py — Fetches prices, volume, and public news.

Rules enforced here:
  * Only public, login-free endpoints (PSX official data portal + public RSS).
  * No protection bypass, no fabricated data.
  * Every value carries a `source` and `as_of` tag.
  * On failure, return the latest stored data with a clear staleness warning.
"""

import logging
import re
from datetime import datetime

import requests
import pandas as pd

import config
import database as db
import ssl_compat

log = logging.getLogger("data_fetcher")

# Ensure HTTPS verification uses the OS trust store before any request runs.
# Fixes CERTIFICATE_VERIFY_FAILED on machines whose required root CA lives in
# the Windows store but not in certifi's bundle (e.g. SSL-inspecting networks).
ssl_compat.enable()

COMPANY_KEYWORDS = {
    "PSO": ["pso", "pakistan state oil"],
    "TREET": ["treet"],
    "FABL": ["fabl", "faysal bank"],
    "AIRLINK": ["airlink", "air link"],
    "MEBL": ["mebl", "meezan bank"],
    "SYS": ["systems limited", "systems ltd"],
    "LUCK": ["lucky cement", "lucky core"],
    "FFC": ["fauji fertilizer"],
    "OGDC": ["ogdc", "oil & gas development", "oil and gas development"],
    "MARI": ["mari energies", "mari petroleum"],
}


def _get(url):
    return requests.get(url, headers=config.REQUEST_HEADERS,
                        timeout=config.REQUEST_TIMEOUT)


# ---------------------------------------------------------------------------
# PRICES
# ---------------------------------------------------------------------------
def fetch_intraday(symbol):
    """PSX DPS intraday timeseries -> DataFrame[ts, price, volume].
    Returns (df, meta). df may be None on failure."""
    url = config.PSX_INTRADAY_URL.format(symbol=symbol)
    try:
        r = _get(url)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            raise ValueError("empty payload")
        # PSX DPS rows are [ts, price, volume, ...]; tolerate extra trailing
        # fields the portal may append by keeping only the first three.
        df = pd.DataFrame([row[:3] for row in data],
                          columns=["ts", "price", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="s")
        df = df.sort_values("ts").reset_index(drop=True)
        meta = {"source": "PSX DPS intraday", "as_of": str(df["ts"].iloc[-1]),
                "live": True, "warning": None}
        last = df.iloc[-1]
        db.save_price(symbol, str(last["ts"]), float(last["price"]),
                      float(df["volume"].sum()), meta["source"])
        # Option B: bank today's REAL high/low from the ticks. PSX EOD has no
        # H/L, so over time this builds genuine daily OHLC history -> true
        # ATR/ADX become possible once enough days accumulate.
        try:
            day = df.copy()
            day["d"] = day["ts"].dt.date
            for d, g in day.groupby("d"):
                db.save_daily_ohlc(symbol, str(d),
                                   float(g["price"].iloc[0]),    # open
                                   float(g["price"].max()),      # high
                                   float(g["price"].min()),      # low
                                   float(g["price"].iloc[-1]),   # close
                                   float(g["volume"].sum()),     # volume
                                   meta["source"])
        except Exception as e:
            log.debug("Daily OHLC capture skipped for %s: %s", symbol, e)
        return df, meta
    except Exception as e:
        log.warning("Intraday fetch failed for %s: %s", symbol, e)
        return None, {"source": "PSX DPS intraday", "as_of": None,
                      "live": False,
                      "warning": f"Live fetch failed ({e}); using latest stored data."}


def _bank_eod_history(symbol, df):
    """Persist the full EOD series we just downloaded. It was fetched and thrown
    away on every one of the ~24 cycles a day, leaving the engine with only the
    ~50 daily_ohlc bars the intraday path had banked.

    Wrapped: banking history must never be able to fail a price fetch.
    """
    try:
        stored, latest = db.eod_history_state(symbol)
        as_of = str(df["date"].iloc[-1].date())
        if stored >= len(df) and latest == as_of:
            return          # already banked today; don't rewrite 1,200 rows
        rows = [(str(r.date.date()), r.open, r.close, r.volume)
                for r in df.itertuples()]
        n = db.save_eod_history(symbol, rows)
        log.info("Banked %d EOD bars for %s through %s", n, symbol, as_of)
    except Exception as e:
        log.warning("EOD history banking failed for %s: %s", symbol, e)


def fetch_eod(symbol):
    """PSX DPS end-of-day history -> DataFrame[date, close, volume]."""
    url = config.PSX_EOD_URL.format(symbol=symbol)
    try:
        r = _get(url)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            raise ValueError("empty payload")
        # PSX DPS EOD rows are [ts, close, volume, open]; older format had only
        # three. Keep `open` when present (used for a gap/body-aware volatility
        # estimate — PSX gives no High/Low). Missing open -> NaN, handled downstream.
        recs = [(row[0], row[1], row[2], row[3] if len(row) > 3 else None)
                for row in data]
        df = pd.DataFrame(recs, columns=["ts", "close", "volume", "open"])
        df["date"] = pd.to_datetime(df["ts"], unit="s")
        df = df.sort_values("date").reset_index(drop=True)
        meta = {"source": "PSX DPS end-of-day", "as_of": str(df["date"].iloc[-1].date()),
                "live": True, "warning": None}
        out = df[["date", "open", "close", "volume"]]
        _bank_eod_history(symbol, out)
        return out, meta
    except Exception as e:
        log.warning("EOD fetch failed for %s: %s", symbol, e)
        # Fall back to the daily bars already banked from earlier intraday polls.
        # These are REAL captured prices, not a reconstruction — the same data the
        # ATR/ADX path already trusts. Without this the engine sat on 48 stored
        # bars and a cached quote and still reported "No data" for every symbol
        # the moment DPS went unreachable (2026-08-27, all 50 tickers blank).
        # latest_quote has always degraded this way; fetch_eod never did.
        bars = db.get_daily_ohlc(symbol, limit=400)
        if len(bars) >= 30:
            df = pd.DataFrame([{"date": pd.to_datetime(b["date"]),
                                "open": b["open"], "close": b["close"],
                                "volume": b["volume"]} for b in bars])
            as_of = bars[-1]["date"]
            log.info("EOD for %s: using %d banked bars through %s",
                     symbol, len(bars), as_of)
            return df[["date", "open", "close", "volume"]], {
                "source": "banked daily OHLC (cached)", "as_of": as_of,
                "live": False,
                "warning": (f"EOD fetch failed ({e}); using {len(bars)} banked "
                            f"daily bars through {as_of}.")}
        return None, {"source": "PSX DPS end-of-day", "as_of": None,
                      "live": False,
                      "warning": f"EOD fetch failed ({e})."}


def latest_quote(symbol):
    """Best-effort latest price/volume with explicit provenance."""
    df, meta = fetch_intraday(symbol)
    if df is not None and len(df):
        last = df.iloc[-1]
        return {"price": float(last["price"]), "volume": float(df["volume"].sum()),
                **meta}
    # Fallback: last stored price
    with db.conn() as c:
        r = c.execute("""SELECT * FROM prices WHERE symbol=?
                         ORDER BY ts DESC LIMIT 1""", (symbol,)).fetchone()
    if r:
        return {"price": r["price"], "volume": r["volume"],
                "source": r["source"] + " (cached)", "as_of": r["ts"],
                "live": False,
                "warning": "Live data unavailable — showing last stored price."}
    return {"price": None, "volume": None, "source": "none", "as_of": None,
            "live": False, "warning": "No price data available for " + symbol}


# ---------------------------------------------------------------------------
# NEWS (public RSS only)
# ---------------------------------------------------------------------------
# Match <item> with or without attributes (feeds emit e.g.
# <item xmlns:default="...">), so attribute-bearing items aren't skipped.
_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.S | re.I)
_TAG_RE = {t: re.compile(rf"<{t}.*?>(.*?)</{t}>", re.S | re.I)
           for t in ("title", "link", "pubDate")}
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def _clean(text):
    if not text:
        return ""
    m = _CDATA_RE.search(text)
    if m:
        text = m.group(1)
    return re.sub(r"<[^>]+>", "", text).strip()


def _published_recent(pub_str, max_age_days=None):
    """True if the article's publish date is within the freshness window.
    Unknown/unparseable dates are kept (we don't over-filter), but dated-old
    items are dropped so stale news can't pollute scoring."""
    if max_age_days is None:
        max_age_days = config.NEWS_MAX_AGE_DAYS
    if not pub_str:
        return True
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        dt = parsedate_to_datetime(pub_str)
        if dt is None:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        return age_days <= max_age_days
    except Exception:
        return True


def _parse_rss_items(text, source, symbols=None, now=None, tag_keywords=False):
    """Parse <item> blocks from RSS text into our news-item dicts.

    Drops headlines older than config.NEWS_MAX_AGE_DAYS by publish date. If
    tag_keywords is True, each item is tagged with any matching symbols via
    COMPANY_KEYWORDS; otherwise the given `symbols` list is used.
    """
    now = now or datetime.now().isoformat()
    out = []
    for raw in _ITEM_RE.findall(text)[:40]:
        m = _TAG_RE["title"].search(raw)
        title = _clean(m.group(1)) if m else ""
        if not title:
            continue
        pub = _clean(_TAG_RE["pubDate"].search(raw).group(1)) \
            if _TAG_RE["pubDate"].search(raw) else ""
        if not _published_recent(pub):
            continue
        link = _clean(_TAG_RE["link"].search(raw).group(1)) \
            if _TAG_RE["link"].search(raw) else ""
        if tag_keywords:
            low = title.lower()
            syms = [s for s, kws in COMPANY_KEYWORDS.items()
                    if any(k in low for k in kws)]
        else:
            syms = list(symbols or [])
        out.append({"fetched_at": now, "source": source, "title": title,
                    "link": link, "published": pub, "symbols": syms})
    return out


def fetch_company_news(symbol):
    """Per-company PUBLIC sentiment input via Google News RSS search.

    Returns news items tagged to `symbol` and stores them, so the sentiment
    module sees real per-stock mentions. Login-free, public, source-tagged.
    """
    from urllib.parse import quote
    query = config.COMPANY_NEWS_QUERY.get(symbol, f"{symbol} PSX Pakistan stock")
    url = config.GOOGLE_NEWS_RSS.format(query=quote(query))
    now = datetime.now().isoformat()
    try:
        r = _get(url)
        r.raise_for_status()
        items = _parse_rss_items(r.text, f"Google News: {query}", [symbol], now)
        if items:
            db.save_news(items)
        log.info("Company news for %s: %d items", symbol, len(items))
        return items
    except Exception as e:
        log.warning("Company news fetch failed for %s: %s", symbol, e)
        return []


def fetch_news():
    """Pull all configured public RSS feeds, tag symbols, store, return items."""
    items, now = [], datetime.now().isoformat()
    for name, url in config.NEWS_FEEDS:
        try:
            r = _get(url)
            r.raise_for_status()
            items += _parse_rss_items(r.text, name, now=now, tag_keywords=True)
        except Exception as e:
            log.warning("News feed %s failed: %s", name, e)
    if items:
        db.save_news(items)
    log.info("Fetched %d recent news items (<= %d days old)",
             len(items), config.NEWS_MAX_AGE_DAYS)
    return items
