"""news_feed.py — Reads the authentic news feed produced by the daily Claude
news routine (news_signals.json) and exposes per-symbol verdicts to the engine.

The routine (see news_routine.md) reads real articles from an allowlist of
authentic PSX/financial sources, judges each stock's news, and writes a verdict
WITH source URLs. This module only READS that file — it never fetches news
itself, so the 15-min engine loop stays fast and offline-safe.

Freshness contract: if the file is missing, malformed, or older than
config.NEWS_SIGNALS_MAX_AGE_HOURS, get() returns None for every symbol and the
caller falls back to RSS/VADER scoring. Authentic-but-absent is never faked.

Per-symbol verdict schema (values the engine relies on):
    score        float 0-100  (50 = neutral; >50 positive, <50 negative)
    direction    "positive" | "negative" | "neutral"
    materiality  "normal" | "material_negative" | "material_positive"
    confidence   "high" | "medium" | "low"
    summary      str   one-line plain-English reason
    headlines    list[str]
    sources      list[str]  (URLs — authenticity is traceable, not asserted)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import config
import news_window

log = logging.getLogger("news_feed")

_CACHE = {"mtime": None, "data": None}


def _parse_as_of(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fresh_as_of(raw, now=None):
    """Return (datetime, age_hours) or (None, reason); timestamps fail closed."""
    as_of = _parse_as_of(raw.get("as_of"))
    if as_of is None:
        return None, "malformed"
    current = now or datetime.now(timezone.utc)
    if as_of > current + timedelta(minutes=5):
        return None, "future"
    age_h = (current - as_of).total_seconds() / 3600
    if age_h > config.NEWS_SIGNALS_MAX_AGE_HOURS:
        return None, "stale"
    return as_of, age_h


def _valid_scoring_rating(value):
    """Strict score-bearing contract: never synthesize causality/confidence."""
    if not isinstance(value, dict) or value.get("rating") not in _RATING_BASE:
        return False
    confidence = value.get("confidence")
    return (value.get("causality") in _CAUSALITY_MULT
            and not isinstance(confidence, bool)
            and isinstance(confidence, (int, float))
            and 0.0 <= confidence <= 1.0
            and isinstance(value.get("sources"), list)
            and bool(value.get("sources")))


def _rating_in_session(value, now=None):
    """A rating can score only when at least one cited article is in-session."""
    anchor = news_window.session_anchor(now)
    published = list(value.get("source_published") or [])
    if not published:
        raw, _ = load_raw()
        wanted = set(value.get("sources") or [])
        published = [i.get("published") for i in raw.get("items", [])
                     if i.get("url") in wanted]
    stamps = [_parse_as_of(p) for p in published]
    return any(ts is not None and ts >= anchor for ts in stamps)


def load_signals():
    """Return (signals_dict, meta) where signals_dict maps SYMBOL -> verdict.
    Returns ({}, meta) when the file is missing, malformed, or stale."""
    path = config.NEWS_SIGNALS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}, {"status": "absent"}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("news_signals.json unreadable (%s) — using RSS/VADER fallback", e)
        return {}, {"status": "malformed"}

    as_of, age = _fresh_as_of(raw)
    if as_of is None:
        log.warning("news_signals.json rejected: %s as_of", age)
        return {}, {"status": age}

    signals = {k.upper(): v for k, v in (raw.get("signals") or {}).items()}
    return signals, {"status": "ok", "as_of": raw.get("as_of"),
                     "count": len(signals)}


def get(symbol):
    """Per-symbol authentic verdict dict, or None if unavailable/stale."""
    signals, _ = load_signals()
    return signals.get(symbol.upper())


def status_line():
    """One-line health string for reports/logs."""
    _, meta = load_signals()
    if meta["status"] == "ok":
        return f"Authentic news feed: {meta['count']} symbols, as of {meta['as_of']}."
    return f"Authentic news feed unavailable ({meta['status']}) — RSS/VADER fallback."


def sector_session_headlines(symbol, limit=6, now=None):
    """Sector headlines published since the current session anchor.

    Same session rule as session_headlines, applied to sector_headlines. A
    policy story is only allowed to move this session's score once; after the
    next close it is stale like anything else.
    """
    anchor = news_window.session_anchor(now)
    out = []
    for h in sector_headlines(symbol, limit=50):
        ts = _published_at(h)
        if ts is None or ts < anchor:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# RAW headline window (UNSCORED). Reads news_raw_24h.json — the auto-fetched
# last-24h headlines that news.yml collects on a schedule (no manual routine,
# no LLM judgment). Used purely to SHOW real, source-linked headlines per
# symbol so the user can cross-verify by eye. Never feeds the score.
# --------------------------------------------------------------------------
_RAW_CACHE = {"mtime": None, "data": None}


def _publisher(item):
    """Best-effort clean publisher name. Google News titles arrive as
    'Headline - Business Recorder'; prefer the explicit macro `source`, else
    the suffix after the last ' - '."""
    src = (item.get("source") or "").strip()
    if src and src != "google_news_rss":
        return src
    title = item.get("title") or ""
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip()
    return "source"


def _clean_title(item):
    title = item.get("title") or ""
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title


def load_raw():
    """Return (payload, meta). Empty when the raw file is missing/malformed."""
    path = getattr(config, "NEWS_RAW_PATH", None)
    if not path:
        import os
        path = os.path.join(config.BASE_DIR, "news_raw_24h.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, {"status": "absent"}
    fetched = _parse_as_of(raw.get("fetched_at"))
    age_h = None
    if fetched is not None:
        age_h = round((datetime.now(timezone.utc) - fetched).total_seconds() / 3600, 1)
    return raw, {"status": "ok", "fetched_at": raw.get("fetched_at"),
                 "age_hours": age_h, "count": raw.get("count", 0)}


def raw_headlines(symbol, limit=5):
    """List of {title, url, publisher, published} for this symbol's last-24h
    headlines (deduped by cleaned title). Empty list if none / file absent.
    UNSCORED — for manual cross-verification only."""
    raw, meta = load_raw()
    if meta["status"] != "ok":
        return []
    credible = [p.lower() for p in getattr(config, "NEWS_DISPLAY_PUBLISHERS", [])]
    sym = symbol.upper()
    out, seen = [], set()
    for it in raw.get("items", []):
        # Attribution is by ANCHOR MATCH on the item's own text, not by any
        # pre-filed symbol. The per-symbol Google queries that used to set that
        # field were removed 2026-08-30 for mis-attributing stories; matching a
        # company's own name in the desk's text is the guarantee that replaces
        # it. Every item is now considered for every symbol.
        if not config.headline_matches_company(sym, it.get("title"), it.get("summary")):
            continue
        t = _clean_title(it)
        key = t.lower()
        if not t or key in seen:
            continue
        pub = _publisher(it)
        # Display filter: only credible desks (the fetch-time host allowlist is
        # bypassed by Google News redirect links). Skip if no allowlist set.
        if credible and not any(c in pub.lower() for c in credible):
            continue
        seen.add(key)
        out.append({"title": t, "url": it.get("url", ""),
                    "publisher": pub, "published": it.get("published")})
        if len(out) >= limit:
            break
    return out


def _published_at(item):
    """Parse an item's publish time to an aware datetime, or None.

    """
    raw = item.get("published")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_headlines(symbol, limit=6, now=None):
    """Company headlines published since the current session anchor.

    Same authenticity guarantees as raw_headlines (company-anchor gate +
    credible-desk filter); the only added constraint is TIME. A headline older
    than news_window.session_anchor() is stale for this session and excluded,
    so yesterday's story cannot keep influencing today (news policy rule 1),
    while news breaking after the close reaches the next open (rule 3).

    An item with an unparseable/absent publish time is EXCLUDED — with no
    timestamp we cannot prove it belongs to this session, and silently
    treating it as fresh is exactly the kind of guess this engine avoids.
    """
    anchor = news_window.session_anchor(now)
    out = []
    for h in raw_headlines(symbol, limit=50):
        ts = _published_at(h)
        if ts is None or ts < anchor:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


# Rating -> 0-100 score. 50 is neutral; distance from 50 is the conviction.
_RATING_BASE = {"highly_positive": 90.0, "positive": 70.0, "neutral": 50.0,
                "negative": 30.0, "highly_negative": 10.0}
# Causality is the whole point of the analysis (news policy rule 5): only news
# with a traceable mechanism to the company's cash flows should move a score.
# Correlated news is damped hard; noise is pinned to neutral so it CANNOT move
# the score at all, however confident the model was about it.
_CAUSALITY_MULT = {"causal": 1.0, "correlated": 0.35, "noise": 0.0}


def news_score(symbol, now=None):
    """0-100 news score for the scoring engine, or None when there is nothing
    to say (absent/stale ratings file, or no rating for this symbol).

    None matters: it means the caller must treat company news as NEUTRAL rather
    than invent a number. A symbol with no news is not a symbol with bad news.

    The score only departs from 50 in proportion to causality x confidence, so
    a merely-correlated headline nudges and pure noise does nothing. This is
    the guard that the 2026-07 news weighting lacked, where any headline could
    swing a score run-to-run.
    """
    rating = glm_rating(symbol)
    if not _valid_scoring_rating(rating) or not _rating_in_session(rating, now):
        return None
    base = _RATING_BASE.get(rating.get("rating"))
    if base is None:
        return None
    # Causality and confidence were validated above; incomplete legacy records
    # fail closed rather than acquiring synthesized defaults.
    mult = _CAUSALITY_MULT[rating["causality"]]
    conf = float(rating["confidence"])
    return round(50.0 + (base - 50.0) * mult * conf, 1)


def sector_news_score(symbol, now=None):
    """0-100 sector news score for this symbol's sector, or None.

    Read from the "sectors" block of the ratings file, keyed by SECTOR name —
    kept separate from per-company ratings so a sector-wide call is never
    mistaken for a company-specific one. Damped by causality x confidence the
    same way company news is, so a merely-correlated sector story barely moves
    and sector noise does nothing.
    """
    sector = config.SECTORS.get(symbol.upper())
    if not sector:
        return None
    v = _sector_ratings().get(sector)
    if not _valid_scoring_rating(v) or not _rating_in_session(v, now):
        return None
    base = _RATING_BASE.get(v.get("rating"))
    if base is None:
        return None
    mult = _CAUSALITY_MULT[v["causality"]]
    conf = float(v["confidence"])
    return round(50.0 + (base - 50.0) * mult * conf, 1)


def _raw_sectors_cache(name):
    """The `sectors` block of a ratings file, honouring the staleness gate."""
    path = os.path.join(config.BASE_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    as_of, _ = _fresh_as_of(raw)
    if as_of is None:
        return {}
    return {str(k): v for k, v in (raw.get("sectors") or {}).items()}


def sector_headlines(symbol, limit=5):
    """Last-24h headlines matching this symbol's SECTOR, from anywhere in the
    raw feed. Company anchors require a distinctive company name, so policy and
    industry stories — often the real price driver — reach no symbol at all.
    Returned separately from raw_headlines() and labelled sector news, so the
    anti-mis-attribution guarantee for company headlines is untouched.
    UNSCORED, like everything in the news window."""
    raw, meta = load_raw()
    if meta["status"] != "ok":
        return []
    sector = config.SECTORS.get(symbol.upper())
    if not (getattr(config, "SECTOR_NEWS_ANCHORS", {}).get(sector) or []):
        return []
    credible = [p.lower() for p in getattr(config, "NEWS_DISPLAY_PUBLISHERS", [])]
    peers = {s for s, sec in config.SECTORS.items() if sec == sector}
    out, seen = [], set()
    for it in raw.get("items", []):
        t = _clean_title(it)
        key = t.lower()
        if not t or key in seen:
            continue
        if not config.headline_matches_sector(sector, t, it.get("summary")):
            continue
        pub = _publisher(it)
        if credible and not any(c in pub.lower() for c in credible):
            continue
        seen.add(key)
        out.append({"title": t, "url": it.get("url", ""), "publisher": pub,
                    "published": it.get("published"), "sector": sector,
                    # Which peer the fetcher happened to file it under — useful
                    # context, not an attribution claim about this symbol.
                    "filed_under": it.get("symbol") if it.get("symbol") in peers else None})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# GLM ratings (news_glm_ratings.json) — a SECOND OPINION from GLM-4.5-flash
# on the last-24h headlines. Zero score weight; shown next to the engine's
# signal so the user can see whether the LLM agrees. Missing/stale file →
# returns None for every symbol.
# --------------------------------------------------------------------------
# CLAUDE ROUTINE ONLY (2026-09-10, user instruction). The GLM fallback was
# dropped rather than left as a silent second source: news_glm_ratings.json was
# last written 2026-08-21 and carries the OLD rating shape with no causality,
# horizon or confidence, so falling through to it would have shown a bare
# rating with none of the qualifiers that make it interpretable -- and the
# reader could not tell which model produced what.
#
# A stale or absent file now reports exactly that, instead of quietly serving
# three-week-old ratings from a different rater.
_RATING_FILES = ("news_ai_ratings.json",)


def load_glm_ratings():
    primary = None
    for name in _RATING_FILES:
        ratings, meta = _load_rating_file(name)
        if meta["status"] == "ok":
            return ratings, meta
        # Report the PRIMARY's status when nothing is usable: "stale" on the
        # Claude file is the actionable fact, not "absent" on the GLM fallback.
        primary = primary or meta
    return {}, primary


def _load_rating_file(name):
    path = os.path.join(config.BASE_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, {"status": "absent"}
    as_of, age = _fresh_as_of(raw)
    if as_of is None:
        return {}, {"status": age}
    age_h = round(age, 1)
    if not raw.get("provider") or not raw.get("model"):
        return {}, {"status": "malformed"}
    ratings = {k.upper(): v for k, v in (raw.get("ratings") or {}).items()}
    return ratings, {"status": "ok", "as_of": raw.get("as_of"),
                     "age_hours": age_h, "count": len(ratings),
                     "model": raw.get("model"),
                     "provider": raw.get("provider")}


def _sector_ratings():
    for name in _RATING_FILES:
        sectors = _raw_sectors_cache(name)
        if sectors:
            return sectors
    return {}


def glm_rating(symbol):
    """Per-symbol AI rating dict {rating, reason} or None."""
    ratings, _ = load_glm_ratings()
    return ratings.get(symbol.upper())


def sector_rating(symbol):
    """The AI rating for this symbol's SECTOR, or None.

    Same source sector_news_score reads, exposed so the UI can show WHY a score
    moved when the symbol itself has no company news: a sector call adjusts every
    constituent, and a card that showed nothing would present an unexplained
    move. Callers must label it as sector news — it is not a claim about this
    company specifically.
    """
    sector = config.SECTORS.get(symbol.upper())
    if not sector:
        return None
    v = _sector_ratings().get(sector)
    return v if isinstance(v, dict) else None


def glm_status_line():
    _, meta = load_glm_ratings()
    if meta["status"] != "ok":
        return f"AI news rating unavailable ({meta['status']})."
    age = meta.get("age_hours")
    age_s = f"{age:.1f}h old" if age is not None else "age unknown"
    return (f"AI news rating ({meta.get('model') or 'unknown'}): "
            f"{meta['count']} symbols, {age_s} — second opinion, unweighted.")


def raw_status_line():
    _, meta = load_raw()
    if meta["status"] != "ok":
        return "Raw news window unavailable (not fetched yet)."
    age = meta.get("age_hours")
    age_s = f"{age:.1f}h old" if age is not None else "age unknown"
    return f"Raw news window: {meta['count']} headlines, {age_s} (unscored)."
