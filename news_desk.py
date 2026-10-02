"""News on the trading desk: the freshest headlines and the fresh reviews.

Until 2026-10-02 the desk showed only "Claude: No fresh review" badges, so a
stock could carry a live headline and the desk said nothing. This puts news
where decisions are read. It stays display-only: headlines are unscored and
carry zero weight in any signal, and a stale review is never shown as current.

Freshest copy: the engine loop refreshes news_raw_24h.json on runtime-state
every ~15 minutes; main gets news.yml's hourly copy. Whichever has the newer
fetched_at is used.
"""
import html
import json
import re
from datetime import datetime, timezone

import config
import news_feed

RUNTIME_RAW_URL = ("https://raw.githubusercontent.com/fahadalipersonal313-ai/"
                   "psx-engine/runtime-state/news_raw_24h.json")


def _stamp(blob):
    try:
        t = datetime.fromisoformat(str(blob.get("fetched_at")))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def load_freshest(get=None, timeout=3):
    """-> (payload, where). The newest of: the bundled file, main's live copy
    (news.yml, hourly) and runtime-state's (engine loop, every cycle). The
    dashboard deploys from a frozen branch, so the bundled copy goes stale."""
    import remote_data
    local, meta = news_feed.load_raw()
    candidates = [(local if meta.get("status") == "ok" else {}, "bundled copy")]
    for branch, label in (("main", "main (hourly)"), ("runtime-state", "engine loop (15-minute)")):
        if get is None:
            blob = remote_data.fetch_json("news_raw_24h.json", branch, timeout=timeout)
        else:
            try:
                r = get(RUNTIME_RAW_URL.replace("/runtime-state/", f"/{branch}/"), timeout=timeout)
                r.raise_for_status()
                blob = r.json()
            except Exception:
                blob = None
        candidates.append((blob or {}, label))
    best, where = max(candidates, key=lambda c: _stamp(c[0]) or datetime.min.replace(tzinfo=timezone.utc))
    return (best, where) if best else ({}, "none")


def _published(item):
    return str(item.get("published") or "")


# The macro feeds are whole-newspaper wires (Business Recorder carried a
# Ronaldo match report and a Kanye West concert on 2026-10-02). On the desk an
# untagged headline needs a market term; the News tab still lists everything.
MARKET_TERMS = re.compile(
    r"\b(psx|kse|kmi|stock|shares?|equit|index|market|rupee|dollar|forex|exchange rate|"
    r"sbp|state bank|policy rate|interest|inflation|cpi|spi|gdp|imf|budget|tax|revenue|"
    r"export|import|remittance|reserve|debt|bond|t-bill|sukuk|oil|crude|petrol|lng|gas|"
    r"power|electricity|tariff|circular debt|gold|cement|fertili[sz]er|bank|profit|earnings|"
    r"dividend|ipo|merger|acquisition|textile|auto|refiner|economy|economic|finance|fiscal|"
    r"invest|trade|price)", re.I)


def market_headlines(raw, limit=8, stocks=None):
    """Newest credible headlines, each tagged with the tracked stocks it names.
    Stock-tagged headlines first; untagged ones only when market-related."""
    stocks = stocks or config.STOCKS
    credible = [p.lower() for p in getattr(config, "NEWS_DISPLAY_PUBLISHERS", [])]
    out, seen = [], set()
    for it in sorted(raw.get("items") or [], key=_published, reverse=True):
        title = news_feed._clean_title(it)
        pub = news_feed._publisher(it)
        if not title or title.lower() in seen:
            continue
        if credible and not any(c in pub.lower() for c in credible):
            continue
        seen.add(title.lower())
        tags = [s for s in stocks
                if config.headline_matches_company(s, it.get("title"), it.get("summary"))]
        if not tags and not MARKET_TERMS.search(title):
            continue
        out.append({"title": title, "url": it.get("url") or it.get("link") or "",
                    "publisher": pub, "published": it.get("published"), "stocks": tags})
    out.sort(key=lambda h: (not h["stocks"],))   # stable: newest-first within each group
    return out[:limit]


def _link(h):
    esc = html.escape
    url = h.get("url") or ""
    title = esc(h["title"])
    if url.startswith("https://"):
        title = f'<a href="{esc(url, quote=True)}" target="_blank" rel="noopener">{title}</a>'
    return title


CSS = '''<style>
.news-desk{background:#0f1b2b;border:1px solid #2b4058;border-radius:14px;padding:14px 18px;margin:6px 0 14px;font-size:13px;color:#eef5ff}
.news-desk h4{margin:0 0 8px;font-size:15px}.news-row{padding:6px 0;border-top:1px solid #1f3046;line-height:1.45}
.news-row:first-of-type{border-top:none}.news-meta{color:#a9bfd4;font-size:11px}
.news-tag{background:#1c2a40;border-radius:5px;padding:1px 6px;font-size:11px;margin-left:6px;color:#bfe3ff}
.news-rate{border-radius:5px;padding:1px 7px;font-size:11px;font-weight:700;margin-right:6px}
.news-rate.pos{background:#183c35;color:#8aefd0}.news-rate.neg{background:#432026;color:#ffb3bd}.news-rate.neu{background:#2a3446;color:#d5deea}
.news-desk a{color:#8fd3ff}
</style>'''

TONE = {"positive": "pos", "highly_positive": "pos", "negative": "neg", "highly_negative": "neg"}


def reviewed_rows(reviews):
    """Fresh reviewed stories, one per (reviewer, stock). Stale reviewers
    contribute nothing: load() already dropped their ratings."""
    from news_review_panel import LABELS
    rows = []
    for label, ratings, meta in reviews:
        for sym, r in sorted(ratings.items()):
            src = next((u for u in r.get("sources", []) if str(u).startswith("https://")), "")
            rows.append({"stock": sym, "reviewer": label, "rating": r["rating"],
                         "label": LABELS.get(r["rating"], r["rating"]),
                         "reason": r.get("reason", ""), "source": src})
    return rows


def desk_html(raw, where, reviews, limit=8):
    esc = html.escape
    parts = [CSS, '<section class="news-desk"><h4>Today\'s news</h4>']
    fetched = _stamp(raw)
    age = ""
    if fetched:
        mins = (datetime.now(timezone.utc) - fetched).total_seconds() / 60
        age = f"{mins:.0f} min ago" if mins < 90 else f"{mins/60:.1f} h ago"
    stale = [label for label, ratings, meta in reviews if meta.get("status") != "ok"]
    rows = reviewed_rows(reviews)
    if rows:
        for r in rows:
            src = (f' · <a href="{esc(r["source"], quote=True)}" target="_blank" rel="noopener">source</a>'
                   if r["source"] else "")
            parts.append(f'<div class="news-row"><span class="news-rate {TONE.get(r["rating"], "neu")}">'
                         f'{esc(r["label"])}</span><b>{esc(r["stock"])}</b> {esc(r["reason"])}'
                         f'<div class="news-meta">Reviewed by {esc(r["reviewer"])}{src}</div></div>')
    else:
        parts.append('<div class="news-row news-meta">No fresh stock review right now. '
                     'Headlines below are unrated.</div>')
    if stale:
        parts.append(f'<div class="news-meta">Not current: {esc(", ".join(stale))} review '
                     f'(out of date, so not shown).</div>')
    heads = market_headlines(raw, limit=limit)
    if heads:
        parts.append(f'<div class="news-meta" style="margin-top:10px">Latest headlines · unrated · '
                     f'from the {esc(where)} fetch {esc(age)}</div>')
        for h in heads:
            tags = "".join(f'<span class="news-tag">{esc(s)}</span>' for s in h["stocks"])
            parts.append(f'<div class="news-row">{_link(h)}{tags}'
                         f'<div class="news-meta">{esc(h["publisher"])} · {esc(str(h["published"] or "")[:16])}</div></div>')
    else:
        parts.append('<div class="news-row news-meta">No headlines fetched in the last 24 hours.</div>')
    parts.append('<div class="news-meta" style="margin-top:8px">News never changes a signal here. '
                 'Full evidence and older headlines are in the News tab.</div></section>')
    return "".join(parts)


def stock_headlines(symbol, raw, limit=2):
    return news_feed.raw_headlines(symbol, limit=limit, raw=raw) if raw else []
