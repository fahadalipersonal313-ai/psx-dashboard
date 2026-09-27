"""macro_news_analyzer.py — Computes the 40% macro / industry / fundamentals
/ news score and builds the written explanation per stock.

Inputs:
  * Macro anchors from config (policy rate, CPI, USD/PKR, reserves) — manually
    maintained with as-of dates; stale or empty anchors lower confidence.
  * Public news headlines (macro, sector, company) scored by keyword/polarity.
  * A per-sector driver map so the explanation is sector-aware.

The module NEVER invents fundamentals. Where audited financials are not
wired in, it says so and relies on news + macro context, flagging the gap.
"""

import logging
from datetime import datetime

import config
import database as db
import news_feed
from sentiment_analyzer import _polarity  # reuse polarity engine

log = logging.getLogger("macro")

MACRO_KEYWORDS = {
    "rates": ["policy rate", "interest rate", "monetary policy", "sbp", "mpc"],
    "inflation": ["inflation", "cpi"],
    "fx": ["rupee", "usd", "exchange rate", "pkr"],
    "reserves": ["reserves", "foreign exchange reserves"],
    "imf": ["imf", "bailout", "programme review", "tranche"],
    "policy": ["budget", "tax", "subsidy", "tariff", "psdp", "circular debt"],
}

SECTOR_DRIVERS = {
    "Oil Marketing": ["oil price", "petroleum levy", "circular debt", "fuel demand", "margins"],
    "Islamic Banking": ["policy rate", "deposit growth", "advances", "adr", "islamic banking"],
    "Technology/IT Exports": ["it exports", "remote work", "dollar revenue", "rupee depreciation"],
    "Technology/Telecom Devices": ["smartphone", "import duty", "assembly", "lc restrictions"],
    "Cement/Conglomerate": ["cement dispatches", "construction", "coal price", "psdp"],
    "Fertilizer": ["urea", "gas price", "offtake", "subsidy", "agriculture"],
    "Oil & Gas Exploration": ["oil price", "gas price", "wellhead", "exploration", "circular debt"],
    "Diversified/Consumer": ["consumer demand", "raw material", "margins"],
}


def _anchor_status():
    notes, stale = [], 0
    for k, v in config.MACRO_ANCHORS.items():
        if v["value"] is None:
            notes.append(f"Macro anchor '{k}' not set — update config.MACRO_ANCHORS "
                         f"from {v['source']}.")
            stale += 1
        elif v["as_of"]:
            try:
                age = (datetime.now() - datetime.fromisoformat(v["as_of"])).days
                if age > config.MACRO_STALE_DAYS:
                    notes.append(f"Macro anchor '{k}' is {age} days old — refresh it.")
                    stale += 1
            except Exception:
                pass
    return notes, stale


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _anchor_score():
    """Convert the macro anchors into a 0-100 'macro environment' score where
    higher = more equity-supportive. Level-based (a snapshot of the economy),
    blended into the macro component so the macro WEIGHT rests on real economic
    conditions rather than headline polarity alone.

    Three drivers with a clear 'lower/higher is better' reading:
      * policy rate  — lower rates ease equity discount rates & leverage cost.
      * CPI YoY      — lower inflation = less tightening pressure, real returns.
      * FX reserves  — higher reserves = lower external-default / import risk.
    USD/PKR is kept as an informational anchor only: a single level without a
    trend is not scoreable; rupee momentum is a later add (we already store EOD).

    Returns (score or None if no usable anchors, note string, parts list).
    """
    a = config.MACRO_ANCHORS
    subs, parts = [], []

    rate = a.get("policy_rate_pct", {}).get("value")
    if rate is not None:                       # 7%→90 ... 22%→20
        s = _clamp(90 - (rate - 7) * 70 / 15, 20, 90)
        subs.append(s); parts.append(f"policy rate {rate}%")

    cpi = a.get("cpi_yoy_pct", {}).get("value")
    if cpi is not None:                        # 7%→85 ... 25%→20
        s = _clamp(85 - (cpi - 7) * 65 / 18, 20, 85)
        subs.append(s); parts.append(f"CPI {cpi}%")

    res = a.get("fx_reserves_usd_bn", {}).get("value")
    if res is not None:                        # $5bn→25 ... $20bn→85
        s = _clamp(25 + (res - 5) * 60 / 15, 25, 85)
        subs.append(s); parts.append(f"reserves ${res}bn")

    if not subs:
        return None, "", []
    return round(sum(subs) / len(subs), 1), \
        "Macro anchors: " + ", ".join(parts), parts


def analyze(symbol, news_items):
    """Returns dict: score (0-100), explanation, components, notes."""
    sector = config.SECTORS.get(symbol, "Unknown")
    notes, components = [], {}

    anchor_notes, stale_count = _anchor_status()
    notes += anchor_notes

    all_titles = [n["title"] for n in news_items] + \
                 [n["title"] for n in db.recent_news(72)]
    all_titles = list(dict.fromkeys(all_titles))

    # ---- 1) Macro environment score (0-100): anchor fundamentals + headlines.
    # The anchors (rates/CPI/reserves) set the structural backdrop; macro news
    # nudges it for live developments. When anchors are set they lead (0.6),
    # news adjusts (0.4); with no anchors we fall back to news-only as before.
    # Themes in play (for the written explanation). VADER-scored only when the
    # legacy fallback is enabled; otherwise we don't keyword-score noisy RSS.
    macro_hits, macro_pol = [], []
    for t in all_titles:
        low = t.lower()
        for theme, kws in MACRO_KEYWORDS.items():
            if any(k in low for k in kws):
                macro_hits.append((theme, t))
                if config.NEWS_FALLBACK_VADER:
                    macro_pol.append(_polarity(t))
                break
    anchor_macro, anchor_note, _ = _anchor_score()
    news_macro = 50 + (sum(macro_pol) / len(macro_pol)) * 40 if macro_pol else 50
    if anchor_macro is not None and config.NEWS_FALLBACK_VADER:
        macro_score = round(0.6 * anchor_macro + 0.4 * news_macro, 1)
        notes.append(anchor_note + f" → backdrop {anchor_macro}/100.")
    elif anchor_macro is not None:
        macro_score = float(anchor_macro)          # anchors only — no RSS/VADER
        notes.append(anchor_note + f" → backdrop {anchor_macro}/100.")
    elif config.NEWS_FALLBACK_VADER:
        macro_score = news_macro
    else:
        macro_score = 50.0
    components["macro_environment"] = round(macro_score, 1)

    # ---- 2) Sector score — neutral unless the legacy VADER fallback is on.
    if config.NEWS_FALLBACK_VADER:
        drivers = SECTOR_DRIVERS.get(sector, [])
        sector_pol = [_polarity(t) for t in all_titles
                      if any(d in t.lower() for d in drivers)]
        sector_score = 50 + (sum(sector_pol) / len(sector_pol)) * 40 if sector_pol else 50
    else:
        sector_score = 50.0
    components["sector"] = round(sector_score, 1)

    # ---- 3) Company news — authentic verdict if present, else NEUTRAL (no VADER).
    comp_titles = [n["title"] for n in db.recent_news(96, symbol)]
    av = news_feed.get(symbol)
    # Causality-tagged rating from the Claude news routine takes precedence:
    # it is session-windowed and damped by causality x confidence, so noise
    # cannot move the score. Falls back to the legacy news_signals.json verdict,
    # then to neutral. None (no rating for this symbol) is NOT zero — it means
    # "no news", which is neutral, never bearish.
    _ns = news_feed.news_score(symbol)
    # Sector news reaches every peer, so it is resolved per-symbol via the
    # symbol's sector. Kept separate from the company score end to end: the
    # anti-mis-attribution guarantee is that a sector call never masquerades
    # as a company-specific one.
    _sns = news_feed.sector_news_score(symbol)
    if _ns is not None:
        comp_score = _ns
    elif av and av.get("score") is not None:
        comp_score = float(av["score"])
    elif config.NEWS_FALLBACK_VADER:
        comp_pol = [_polarity(t) for t in comp_titles]
        comp_score = 50 + (sum(comp_pol) / len(comp_pol)) * 45 if comp_pol else 50
    else:
        comp_score = 50.0
        notes.append(f"No authentic news for {symbol} — company news neutral.")
    components["company_news"] = round(comp_score, 1)

    # ---- 4) Fundamentals placeholder honesty
    notes.append("Audited fundamentals (revenue/profit growth, margins, debt, "
                 "cash flow, dividends) are not auto-ingested in this version. "
                 "Review the latest quarterly report on PSX before any Buy. "
                 "Score relies on macro + sector + news components.")

    # Weighted blend: macro 35%, sector 30%, company news 35%
    score = round(0.35 * macro_score + 0.30 * sector_score + 0.35 * comp_score, 1)

    # ---- "Material" bad-news test (was: ANY headline < -0.4, which let a single
    # noisy Google-News result hard-convert a high-scoring Buy into Avoid).
    # Now a veto requires EITHER a strongly negative headline (< -0.6, i.e. real
    # distress words like default/crash/loss) OR at least two negatives that
    # clearly outweigh the positive flow. A lone mildly-negative headline amid
    # neutral/positive coverage no longer hides a strong technical setup.
    # Veto fires on the authentic feed's materiality call (an LLM read the
    # article). With VADER disabled, a missing verdict means NO veto — we never
    # block a setup on keyword noise. (`av` is fetched above for company news.)
    if av and av.get("materiality") in ("material_negative", "material_positive", "normal"):
        material_bad = av.get("materiality") == "material_negative"
        bad_news = ([av.get("summary")] if material_bad and av.get("summary")
                    else (av.get("headlines") or [])[:2] if material_bad else [])
    elif config.NEWS_FALLBACK_VADER:
        neg = [t for t in comp_titles if _polarity(t) < -0.4]
        strong_neg = [t for t in comp_titles if _polarity(t) < -0.6]
        pos = [t for t in comp_titles if _polarity(t) > 0.4]
        material_bad = bool(strong_neg) or (len(neg) >= 2 and len(neg) > len(pos))
        bad_news = strong_neg or neg
    else:
        material_bad, bad_news = False, []
    explanation = _explain(symbol, sector, components, macro_hits[:5],
                           comp_titles[:5], bad_news if material_bad else [])

    return {"symbol": symbol, "score": score, "components": components,
            # Causality-damped news scores (None when there is no rating).
            # scoring_engine applies them as bounded modifiers, not a blend.
            "news_score": _ns,
            "sector_news_score": _sns,
            "explanation": explanation, "notes": notes,
            "bad_news_flag": material_bad, "bad_news": bad_news[:3],
            # With anchors set the macro backdrop is grounded, so absent news
            # alone no longer marks the section low-confidence.
            "low_confidence": stale_count >= 3 or
                              (anchor_macro is None and not macro_pol and not comp_titles),
            "sources": "Public RSS feeds (Business Recorder, Dawn, Profit, "
                       "Mettis) + config macro anchors"}


def _explain(symbol, sector, comp, macro_hits, comp_titles, bad_news):
    lines = [f"{symbol} ({sector}): macro environment component "
             f"{comp['macro_environment']}/100, sector component "
             f"{comp['sector']}/100, company news component "
             f"{comp['company_news']}/100."]
    if macro_hits:
        themes = ", ".join(sorted({t for t, _ in macro_hits}))
        lines.append(f"Macro themes in play: {themes}.")
    if comp_titles:
        lines.append("Recent company headlines: " +
                     " | ".join(t[:90] for t in comp_titles[:3]))
    if bad_news:
        lines.append("⚠ Negative company news detected — treated as a risk "
                     "override candidate.")
    if not macro_hits and not comp_titles:
        lines.append("Limited news flow this run; assessment is neutral by "
                     "construction, not by conviction.")
    return " ".join(lines)
