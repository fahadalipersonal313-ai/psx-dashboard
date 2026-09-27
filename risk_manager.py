"""risk_manager.py — Capital protection layer. Runs AFTER scoring and can
veto or downgrade anything. The engine never claims zero loss; it labels
every setup low / medium / high risk and sizes positions so a stopped-out
trade costs at most RISK['max_risk_per_trade_pct'] of capital.
"""

import logging
import config

log = logging.getLogger("risk")


def _effective_min_headroom_rr(regime, regime_pct_above):
    """Regime-aware headroom-RR threshold. In a strong risk-on rally most names
    sit near their recent highs (the leadership default), so a flat 1.5x rule
    flags the entire leadership group as "thin upside". The minimum scales DOWN
    linearly with rally strength, from the strict baseline (neutral / risk-off)
    to a configured floor (powerful rally). Same pattern as the chase-guard
    widening — symmetric, regime-aware risk policy."""
    base = config.RISK["min_headroom_rr"]
    floor = config.RISK.get("min_headroom_rr_riskon_floor", base)
    full_pct = config.RISK.get("headroom_rr_riskon_full_pct", 8.0) or 8.0
    if regime != "risk-on" or floor >= base:
        return base
    strength = 1.0 if regime_pct_above is None else \
        max(0.0, min(1.0, regime_pct_above / full_pct))
    return base - (base - floor) * strength


def _concentration_pct(symbol, price, holdings):
    """This symbol's share of the CURRENT book at live prices, or None when the
    book is unknown/unpriceable. holdings: [{symbol, qty, avg_cost}, ...] —
    positions other than `symbol` are valued at avg_cost (no live quote for them
    in this call), which is the honest approximation available here."""
    if not holdings or not price:
        return None
    total = this = 0.0
    for h in holdings:
        qty = float(h.get("qty") or 0)
        if qty <= 0:
            continue
        if (h.get("symbol") or "").upper() == symbol.upper():
            val = qty * price          # live price for the symbol being assessed
            this += val
        else:
            val = qty * float(h.get("avg_cost") or 0)
        total += val
    if total <= 0 or this <= 0:
        return None
    return round(this / total * 100, 1)


def assess(symbol, technical, sentiment, macro, capital_pkr=1_000_000,
           regime=None, regime_pct_above=None, holdings=None):
    """Returns dict: risk_level, warnings[], position sizing, veto flags.

    regime / regime_pct_above (optional): market context. When supplied, the
    headroom-RR veto relaxes in a confirmed risk-on rally (see
    _effective_min_headroom_rr). Backward-compatible — omit to keep the strict
    threshold."""
    warnings, vetoes = [], []
    price = technical.get("price")
    stop = technical.get("stop_loss")
    rr = technical.get("headroom_rr")   # REAL room-to-resistance:risk (not the ≈2.0 proj.)
    rr_min = _effective_min_headroom_rr(regime, regime_pct_above)

    # ---- hard warnings
    from data_quality import finite
    turnover = technical.get('median_turnover_pkr')
    if not finite(turnover) or turnover < config.MIN_TURNOVER_PKR:
        vetoes.append('illiquid')
        warnings.append('Not enough verified daily trading value for entry')
    if technical.get("avg_volume") is not None and \
       technical["avg_volume"] < config.RISK["min_avg_daily_volume"]:
        vetoes.append("illiquid")
        warnings.append(f"ILLIQUID: 20-day avg volume "
                        f"{technical['avg_volume']:,.0f} below "
                        f"{config.RISK['min_avg_daily_volume']:,} — exits may slip badly")
    if technical.get("atr_pct") and technical["atr_pct"] > config.RISK["max_volatility_pct"]:
        warnings.append(f"HIGH VOLATILITY: daily range ≈{technical['atr_pct']:.1f}% — "
                        "gap risk elevated")
    if technical.get("breakdown"):
        warnings.append("TECHNICAL BREAKDOWN below support")
        vetoes.append("breakdown")
    if config.CONCENTRATION_VETO_ENABLED:
        conc = _concentration_pct(symbol, price, holdings)
        conc_cap = config.RISK.get("max_existing_concentration_pct")
        if conc is not None and conc_cap and conc > conc_cap:
            warnings.append(f"CONCENTRATED: {symbol} is already {conc}% of the "
                            f"book (cap {conc_cap}%) — adding here increases "
                            "single-name risk, the exposure per-trade sizing "
                            "cannot see")
            vetoes.append("concentrated")

    # News- and sentiment-derived flags. In PURE_TECHNICAL mode they are still
    # surfaced as warnings (the dashboard shows them for manual cross-checking)
    # but are NOT vetoes: nothing outside price/volume moves a signal.
    _pure = getattr(config, "PURE_TECHNICAL", False)
    if macro.get("bad_news_flag"):
        warnings.append("NEGATIVE COMPANY NEWS in last 96h: "
                        + "; ".join(t[:70] for t in macro.get("bad_news", [])[:2]))
        if not _pure:
            vetoes.append("bad_news")
    for f in sentiment.get("flags", []):
        warnings.append(f)
        if ("PUMP" in f or "HYPE" in f) and not _pure:
            vetoes.append("manipulation_risk")
    if rr is not None and rr < rr_min:
        _relax = (f" (eased from {config.RISK['min_headroom_rr']} for risk-on rally)"
                  if rr_min < config.RISK["min_headroom_rr"] else "")
        warnings.append(f"Thin upside: room-to-resistance:risk {rr} below "
                        f"{round(rr_min, 2)}{_relax} — price near overhead "
                        "resistance, little room before the next ceiling")
        # Gated at the source, not at the downgrade: any veto also forces
        # risk_level "High", so leaving a disabled veto in the list would keep
        # mislabelling cards the engine no longer blocks.
        if config.POOR_RR_VETO_ENABLED:
            vetoes.append("poor_rr")
    if technical.get("volume_spike") and sentiment.get("score", 50) > 80:
        warnings.append("Volume spike + euphoric sentiment — possible "
                        "manipulation / pump pattern, verify before acting")

    # ---- structural rules (always shown)
    warnings.append("Rule: no leverage, never all-in, max "
                    f"{config.RISK['max_position_pct']}% of capital per stock, "
                    "diversify across sectors")
    warnings.append("Rule: manual confirmation required before any buy order")

    # ---- risk level
    _keys = ["ILLIQUID", "HIGH VOLATILITY", "BREAKDOWN"]
    if not _pure:
        _keys += ["NEGATIVE", "PUMP", "PANIC"]
    hard = sum(1 for w in warnings if any(k in w for k in _keys))
    risk_level = "High" if hard >= 2 or vetoes else \
                 "Medium" if hard == 1 else "Low"

    # ---- position sizing (risk-based)
    from position_sizing import size
    raw = size(price, stop, capital_pkr, avg_volume=technical.get('avg_volume'))
    sizing = None if raw is None else {'capital_assumed_pkr': capital_pkr,
                'max_loss_if_stopped_pkr': raw['risk'], 'suggested_shares': raw['shares'],
                'position_value_pkr': raw['value'], 'risk_per_share': raw['rps']}

    return {"risk_level": risk_level, "warnings": warnings, "vetoes": vetoes,
            "position_sizing": sizing}
