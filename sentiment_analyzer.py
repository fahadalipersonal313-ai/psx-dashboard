"""sentiment_analyzer.py — Computes the 30% public sentiment score.

Sources: ONLY public, login-free content already fetched legally (RSS news
headlines stored in the DB, plus any optional public-forum text the user
drops into `public_comments/` as .txt files — e.g. copied from open polls
or open discussion threads they are allowed to use).

Honesty rules:
  * If mention volume is low, the score is pulled toward neutral and the
    confidence flag is lowered — silence is NOT bullishness.
  * Hype / pump-and-dump / panic heuristics raise explicit warnings.
"""

import logging
import os
from datetime import datetime

import config
import database as db
import news_feed

log = logging.getLogger("sentiment")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER = SentimentIntensityAnalyzer()
    # Finance-flavoured lexicon additions
    _VADER.lexicon.update({
        "bullish": 2.5, "bearish": -2.5, "rally": 1.8, "surge": 1.8,
        "plunge": -2.2, "crash": -3.0, "record profit": 3.0, "default": -2.5,
        "upgrade": 2.0, "downgrade": -2.0, "dividend": 1.5, "loss": -1.8,
        "imf": 0.0, "circuit breaker": -1.5, "buyback": 1.8, "halal": 0.5,
    })
except ImportError:
    _VADER = None
    log.warning("vaderSentiment not installed — sentiment limited to keywords.")

BULL_WORDS = {"buy", "bullish", "rally", "breakout", "target", "upgrade",
              "profit", "growth", "dividend", "surge", "accumulate"}
BEAR_WORDS = {"sell", "bearish", "crash", "plunge", "downgrade", "loss",
              "default", "panic", "dump", "decline", "warning"}
HYPE_WORDS = {"moon", "rocket", "guaranteed", "double", "10x", "jackpot",
              "easy money", "can't lose", "sure shot"}

COMMENTS_DIR = os.path.join(config.BASE_DIR, "public_comments")


def _polarity(text):
    if _VADER:
        return _VADER.polarity_scores(text)["compound"]
    low = text.lower()
    b = sum(w in low for w in BULL_WORDS)
    s = sum(w in low for w in BEAR_WORDS)
    return 0.0 if b == s else (0.4 if b > s else -0.4)


def _load_public_comments(symbol):
    """Optional user-supplied public text (one snippet per line)."""
    texts = []
    path = os.path.join(COMMENTS_DIR, f"{symbol}.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            texts = [ln.strip() for ln in f if ln.strip()]
    return texts


def analyze(symbol, news_items):
    """Returns dict: score (0-100), counts, trend, flags, verdict, notes.

    Primary path: the authentic news feed (news_signals.json, LLM-judged with
    source URLs). Falls back to RSS/VADER below when the feed has no fresh
    verdict for this symbol — see _analyze_vader."""
    av = news_feed.get(symbol)
    if av and av.get("score") is not None:
        run_time = datetime.now().isoformat()
        score = round(float(av["score"]), 1)
        conf = (av.get("confidence") or "medium").lower()
        flags = []
        if av.get("materiality") == "material_negative":
            flags.append("MATERIAL NEGATIVE news (authentic source)")
        if av.get("materiality") == "material_positive":
            flags.append("Material positive news (authentic source)")
        verdict = (av.get("summary") or av.get("direction") or "")[:200]
        db.save_sentiment(run_time, symbol, score, 0, 0, 0,
                          len(av.get("headlines") or []), flags)
        return {"symbol": symbol, "score": score,
                "bullish": 0, "bearish": 0, "neutral": 0,
                "mentions": len(av.get("headlines") or []),
                "trend_vs_prev": None, "flags": flags, "verdict": verdict,
                "notes": [f"Authentic news ({conf} confidence): {verdict}"],
                "low_confidence": conf == "low",
                "sources": "Authentic news feed (" +
                           ", ".join((av.get("sources") or [])[:3]) + ")"}
    if config.NEWS_FALLBACK_VADER:
        return _analyze_vader(symbol, news_items)
    # Authentic-or-neutral: no fresh authentic verdict -> neutral, not RSS noise.
    return {"symbol": symbol, "score": 50.0, "bullish": 0, "bearish": 0,
            "neutral": 0, "mentions": 0, "trend_vs_prev": None, "flags": [],
            "verdict": "No authentic news", "low_confidence": True,
            "notes": ["No fresh authentic news verdict — news treated as neutral "
                      "(VADER keyword scoring disabled)."],
            "sources": "Authentic news feed (none this run)"}


def _analyze_vader(symbol, news_items):
    """RSS/VADER fallback (keyword-lexicon scoring) when no authentic verdict."""
    run_time = datetime.now().isoformat()
    texts = [n["title"] for n in news_items
             if symbol in n.get("symbols", []) or symbol in (n.get("symbols") or "")]
    # DB-stored news for this symbol over the last 48h adds depth
    texts += [n["title"] for n in db.recent_news(48, symbol)]
    texts += _load_public_comments(symbol)
    texts = list(dict.fromkeys(texts))  # dedupe

    flags, notes = [], []
    bullish = bearish = neutral = 0
    polarities = []
    for t in texts:
        p = _polarity(t)
        polarities.append(p)
        if p > 0.15: bullish += 1
        elif p < -0.15: bearish += 1
        else: neutral += 1
        if any(h in t.lower() for h in HYPE_WORDS):
            flags.append("HYPE language detected")

    mentions = len(texts)
    if mentions == 0:
        notes.append("No public mentions found in current sources — neutral "
                     "score assigned with LOW confidence. Silence ≠ bullish.")
        score, verdict = 50.0, "No signal (insufficient public data)"
    else:
        avg = sum(polarities) / mentions
        raw = 50 + avg * 50                       # map [-1,1] -> [0,100]
        # Low-volume shrinkage toward neutral
        weight = min(1.0, mentions / 8)
        score = round(50 + (raw - 50) * weight, 1)
        verdict = ("Healthy bullish" if 58 <= score <= 75 and bearish < bullish
                   else "Overhyped" if score > 80
                   else "Improving" if score > 55
                   else "Weak/negative" if score < 42 else "Mixed/neutral")

    # Trend vs previous run
    prev = db.previous_sentiment(symbol)
    trend = None
    if prev is not None:
        trend = round(score - prev, 1)
        if trend <= -15:
            flags.append("PANIC-SELLING risk: sentiment dropped sharply vs last run")
        if trend >= 15 and score > 75:
            flags.append("PUMP-AND-DUMP risk: sudden sentiment spike — verify "
                         "with fundamentals before acting")
    if score > 82:
        flags.append("RETAIL OVER-EXCITEMENT: sentiment unusually euphoric")

    db.save_sentiment(run_time, symbol, score, bullish, bearish, neutral,
                      mentions, flags)
    return {"symbol": symbol, "score": score, "bullish": bullish,
            "bearish": bearish, "neutral": neutral, "mentions": mentions,
            "trend_vs_prev": trend, "flags": list(set(flags)),
            "verdict": verdict, "notes": notes,
            "low_confidence": mentions < 5,
            "sources": "Public RSS headlines + optional user-supplied public text"}
