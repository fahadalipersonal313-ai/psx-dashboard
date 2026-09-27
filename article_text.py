"""article_text.py — Fetch and extract the BODY TEXT of news articles.

Why this exists
---------------
Every rating the engine makes about news is judged from a HEADLINE. `summary` in
news_raw_24h.json is the RSS <description> cut at 400 chars — a lede stub, not the
article — and Mettis items carry no summary at all. Deciding "causal vs correlated"
from one line of text is guesswork; the mechanism that makes a story causal is
usually in the third paragraph, not the headline.

96% of the URLs in the raw file are direct publisher links (Business Recorder,
Dawn, ProPakistani, Mettis), so the bodies are reachable. Google News links are
opaque redirects and mostly are not — that is expected, and shows up as None.

What is and is NOT done here
  - Only pages the publisher serves to any anonymous visitor. No login, no paywall
    bypass, no internal APIs. Same standing rule as mettis_scraper.
  - Paced and capped, so a run is a polite trickle rather than a burst.
  - Output goes to news_bodies.json, which is GITIGNORED on purpose. Adding bodies
    to news_raw_24h.json would commit ~50 full articles on every hourly CI run —
    hundreds of KB of churn per hour, straight back into the tracked-size problem
    that took the DB to 54 MB.

Accuracy rules, since these feed a score
  - An article that cannot be fetched or parsed is recorded as None, NEVER as an
    empty string or a guess. A caller must be able to tell "no body available"
    from "the body said nothing".
  - No summarising or rewriting happens here. This module returns the publisher's
    own prose, cleaned of markup, and nothing else.
"""

import json
import logging
import os
import re
import sys
import time

import requests

import config

log = logging.getLogger("article_text")

BODIES_PATH = os.path.join(config.BASE_DIR, "news_bodies.json")
RAW_PATH = os.path.join(config.BASE_DIR, "news_raw_24h.json")

UA = {"User-Agent": "Mozilla/5.0 (psx-engine news-routine; +github)",
      "Accept": "text/html,application/xhtml+xml"}
TIMEOUT = 20
PAUSE_SECONDS = 0.4          # matches mettis_scraper's politeness pacing
MAX_ARTICLES = 80
MAX_BODY_CHARS = 4000        # ~1k tokens per article; enough for the mechanism
MIN_BODY_CHARS = 200         # shorter than this is nav furniture, not an article

# Blocks that are never article prose. Dropped before paragraphs are collected.
_JUNK_RE = re.compile(
    r"<(script|style|noscript|figure|figcaption|aside|nav|header|footer)\b.*?</\1>",
    re.S | re.I)
_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
_ENTITIES = (("&#x27;", "'"), ("&#39;", "'"), ("&#x2019;", "’"),
             ("&amp;", "&"), ("&quot;", '"'), ("&nbsp;", " "),
             ("&#x2013;", "–"), ("&lt;", "<"), ("&gt;", ">"))
# Boilerplate lines publishers repeat on every article.
_BOILER = re.compile(
    r"^(copyright|all rights reserved|follow us|read more|also read|share this"
    r"|subscribe|advertisement|published in dawn)", re.I)


def _clean(text):
    """Strip tags, decode the entities these publishers actually emit, collapse
    whitespace. Extends mettis_scraper._clean rather than duplicating its bugs."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    for ent, ch in _ENTITIES:
        text = text.replace(ent, ch)
    text = re.sub(r"&#x?[0-9a-fA-F]+;", " ", text)   # any entity left over
    return re.sub(r"\s+", " ", text).strip()


def extract(html):
    """Article prose from a page, or None if nothing article-like is present.

    Paragraph-based rather than a readability heuristic: these five publishers all
    wrap body copy in <p>, and a dependency-free extractor cannot fail to install
    on a runner. None (not "") when the page yields too little to be an article.
    """
    if not html:
        return None
    body = _JUNK_RE.sub(" ", html)
    paras = []
    for raw in _P_RE.findall(body):
        p = _clean(raw)
        # One-clause fragments are captions, bylines and share prompts.
        if len(p) < 40 or _BOILER.match(p):
            continue
        paras.append(p)
    text = " ".join(paras).strip()
    if len(text) < MIN_BODY_CHARS:
        return None
    return text[:MAX_BODY_CHARS]


def fetch_body(url):
    """Body text for one article URL, or None. Never raises."""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log.warning("fetch failed %s: %s", url, e)
        return None
    return extract(r.text)


def fetch_bodies(items, cap=MAX_ARTICLES, pause=PAUSE_SECONDS):
    """url -> body text or None, for each item. Ordered newest-first by caller."""
    out, seen = {}, set()
    for it in items:
        url = (it or {}).get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        if len(out) >= cap:
            break
        out[url] = fetch_body(url)
        time.sleep(pause)
    return out


def run(raw_path=RAW_PATH, output_path=BODIES_PATH):
    """Read the raw news file, fetch every article body, write news_bodies.json."""
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)
    items = raw.get("items") or []
    bodies = fetch_bodies(items)

    by_source = {}
    for it in items:
        src = it.get("source") or "?"
        got, tot = by_source.get(src, (0, 0))
        if it.get("url") in bodies:
            tot += 1
            if bodies[it["url"]]:
                got += 1
        by_source[src] = (got, tot)

    ok = sum(1 for v in bodies.values() if v)
    payload = {"fetched_at": raw.get("fetched_at"),
               "count": len(bodies), "with_body": ok,
               "chars": sum(len(v) for v in bodies.values() if v),
               "bodies": bodies}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info("bodies: %d/%d articles have text (%s)", ok, len(bodies),
             ", ".join(f"{s} {g}/{t}" for s, (g, t) in sorted(by_source.items())))
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = run()
    print(f"{p['with_body']}/{p['count']} articles with body text, "
          f"{p['chars']:,} chars -> {BODIES_PATH}")
    sys.exit(0 if p["with_body"] else 1)
