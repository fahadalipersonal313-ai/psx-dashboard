"""portfolio_risk.py — Tier 2 #9: risk at the BOOK level, not just per trade.

risk_manager sizes each position so a single stopped-out trade costs at most
RISK['max_risk_per_trade_pct'] of capital. But the real account-killers are
CORRELATED: ten "safe" 1.5% trades that all gap down on the same bad day cost
15% together, and a book that is 80% cement isn't diversified at all.

This module takes the current Buy / Strong Buy candidates, sizes each one with
the same rule risk_manager uses, then admits them greedily (best score first)
until a cap binds:
  * total portfolio HEAT  — sum of max-loss-if-stopped across open positions
  * SECTOR exposure       — capital deployed into any one sector
  * max OPEN positions    — a practical concurrency cap

Anything that would breach a cap is moved to `deferred` WITH the reason, never
silently dropped. Honest by design: a candidate with no usable price/stop can't
be sized, so it is listed separately in `unsizable`.
"""

import logging
import config

log = logging.getLogger("portfolio_risk")


def _size(price, stop, capital):
    from position_sizing import size
    return size(price, stop, capital)


def assess(candidates, capital=1_000_000, holdings=None, cash=None, pending=None):
    """candidates: list of dicts with keys symbol, score, signal, price, stop,
    and (optional) sector. Returns admitted / deferred / unsizable lists plus a
    `book` summary of heat, deployment and sector exposure vs the caps."""
    from data_quality import finite
    from position_sizing import size as shared_size
    if not finite(capital, True):
        raise ValueError('Positive marked account equity required')
    cash = capital if cash is None else cash
    if not finite(cash) or cash < 0:
        raise ValueError('Valid available cash required')
    holdings, pending = holdings or [], pending or []
    P = config.PORTFOLIO_RISK
    max_heat = capital * P["max_portfolio_heat_pct"] / 100
    max_sector_val = capital * P["max_sector_exposure_pct"] / 100
    EPS = 1e-6

    ranked = sorted(candidates, key=lambda c: (c.get("score") or 0), reverse=True)
    admitted, deferred, unsizable = [], [], []
    heat_used, value_used, sector_val = 0.0, 0.0, {}

    names = set()
    for h in holdings + pending:
        sym = h.get('symbol')
        price, stop, qty = h.get('price'), h.get('stop'), h.get('qty')
        if not sym or not all(finite(v, True) for v in (price, stop, qty)) or stop >= price:
            raise ValueError('Existing/pending position lacks valid quantity, current mark or actual stop')
        sec = h.get('sector') or config.SECTORS.get(sym, 'Unknown')
        val = qty * price
        names.add(sym)
        value_used += val
        heat_used += qty * (price - stop)
        sector_val[sec] = sector_val.get(sec, 0) + val
    cash -= sum(h['qty'] * h['price'] * (1 + config.EXECUTION['fee_bps_per_side'] / 10000) for h in pending)
    for c in ranked:
        if c.get('symbol') in names:
            deferred.append({**c, 'reason': 'Existing, pending or duplicate symbol; additions require separate account review'})
            continue
        sec = c.get("sector") or config.SECTORS.get(c.get("symbol"), "Unknown")
        size = shared_size(c.get("price"), c.get("stop"), capital, max(0, cash), avg_volume=c.get("avg_volume"))
        if not size or size["shares"] <= 0:
            unsizable.append({**c, "sector": sec,
                              "reason": "no usable price/stop to size a position"})
            continue
        reasons = []
        if len(names) >= P["max_open_positions"]:
            reasons.append(f"max {P['max_open_positions']} open positions reached")
        if heat_used + size["risk"] > max_heat + EPS:
            reasons.append(f"would breach {P['max_portfolio_heat_pct']:.0f}% total "
                           f"portfolio heat")
        if sector_val.get(sec, 0.0) + size["value"] > max_sector_val + EPS:
            reasons.append(f"would breach {P['max_sector_exposure_pct']:.0f}% cap "
                           f"on {sec}")
        entry = {"symbol": c.get("symbol"), "score": c.get("score"),
                 "signal": c.get("signal"), "sector": sec,
                 "price": c.get("price"), "stop": c.get("stop"),
                 "shares": size["shares"], "value": round(size["value"], 0),
                 "risk": round(size["risk"], 0),
                 "heat_pct": round(size["risk"] / capital * 100, 2),
                 "weight_pct": round(size["value"] / capital * 100, 2)}
        if reasons:
            entry["reason"] = "; ".join(reasons)
            deferred.append(entry)
        else:
            admitted.append(entry)
            names.add(c.get("symbol"))
            cash -= size["cash_required"]
            heat_used += size["risk"]
            value_used += size["value"]
            sector_val[sec] = sector_val.get(sec, 0.0) + size["value"]

    sectors = {s: {"value": round(v, 0), "pct": round(v / capital * 100, 2)}
               for s, v in sorted(sector_val.items(), key=lambda kv: -kv[1])}
    book = {
        "capital": capital,
        "heat_pkr": round(heat_used, 0),
        "heat_pct": round(heat_used / capital * 100, 2),
        "max_heat_pct": P["max_portfolio_heat_pct"],
        "heat_room_pct": round(P["max_portfolio_heat_pct"] - heat_used / capital * 100, 2),
        "deployed_pkr": round(value_used, 0),
        "deployed_pct": round(value_used / capital * 100, 2),
        "cash_pct": round(cash / capital * 100, 2),
        "open_positions": len(names),
        "max_open_positions": P["max_open_positions"],
        "sector_exposure": sectors,
        "max_sector_pct": P["max_sector_exposure_pct"],
        "deferred": len(deferred),
    }
    return {"admitted": admitted, "deferred": deferred,
            "unsizable": unsizable, "book": book}


def summary_line(result):
    """One-line book summary for logs / reports / email."""
    b = result["book"]
    top_sec = next(iter(b["sector_exposure"].items()), None)
    sec_txt = (f", heaviest sector {top_sec[0]} {top_sec[1]['pct']:.0f}%"
               if top_sec else "")
    return (f"{b['open_positions']} positions, heat {b['heat_pct']:.1f}% of "
            f"{b['max_heat_pct']:.0f}% cap, deployed {b['deployed_pct']:.0f}% "
            f"({b['cash_pct']:.0f}% cash){sec_txt}; {b['deferred']} deferred.")
