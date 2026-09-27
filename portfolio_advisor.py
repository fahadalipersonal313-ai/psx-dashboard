"""portfolio_advisor.py — Turns the engine's per-stock signals into a strategy
for YOUR actual book: existing holdings + ready cash → P/L and a per-position
action (hold / add / average down / trim / exit), plus how to deploy spare cash
into new Buys.

Inputs are plain dicts so this stays pure/testable and the dashboard just renders
it. Sizing reuses the risk rules in config.RISK (per-trade risk %, position cap).

Averaging-down policy = CONSERVATIVE (Fahad, 2026-06-15): only suggest averaging
down when the engine STILL rates the stock Buy/Strong Buy — which already implies
it is not extended and not breaking down (those downgrade the signal), the thesis
is intact, and the position is under the per-stock cap. Never average a falling
knife.
"""

import json
import logging

import config

log = logging.getLogger("portfolio")

_DEPLOY_ACTIONS = ("AVERAGE DOWN", "ADD", "NEW POSITION")
_SIG_RANK = {"Strong Buy": 0, "Buy": 1}


def load_portfolio(path=None):
    """Read portfolio.json → {'cash_pkr': float, 'holdings': [..]}. Tolerant of a
    missing/blank file (returns an empty book)."""
    path = path or config.PORTFOLIO_PATH
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"cash_pkr": 0.0, "holdings": []}
    holds = []
    for h in d.get("holdings", []):
        try:
            # avg_cost may be null: you can hold shares without having recorded
            # what you paid. Keep the position (it still drives sizing, the
            # concentration cap and exposure) and let P&L stay unknown rather
            # than dropping the holding or inventing a cost basis.
            avg = h.get("avg_cost")
            holds.append({"symbol": str(h["symbol"]).upper(),
                          "qty": float(h["qty"]),
                          "stop": h.get("stop"), "avg_cost": None if avg is None else float(avg)})
        except (KeyError, TypeError, ValueError):
            continue
    return {"cash_pkr": float(d.get("cash_pkr") or 0), "holdings": holds, "pending": d.get("pending", [])}


def _size(price, stop, cash_left, equity, current_value):
    from position_sizing import size
    result = size(price, stop, equity, cash_left, current_value)
    return (result['shares'], result['value']) if result else (0, 0.0)


def advise(portfolio, latest):
    """portfolio: {'cash_pkr', 'holdings':[{symbol,qty,avg_cost}]}.
    latest: {SYMBOL: latest-run dict} (needs price, signal, stop_loss, final_score,
            buy_zone_low/high, risk_level).
    Returns {'holdings':[...], 'deploy':[...], 'totals':{...}}."""
    cash = float(portfolio.get("cash_pkr") or 0)
    holds = portfolio.get("holdings", [])
    held_syms = {h["symbol"] for h in holds}

    # --- value the book first (need total equity for position-cap %)
    rows, market_value, cost_value = [], 0.0, 0.0
    for h in holds:
        sym = h["symbol"]
        r = latest.get(sym)
        price = (r or {}).get("price")
        qty, avg = h["qty"], h["avg_cost"]
        cost = qty * avg if avg is not None else None
        cost_value += cost or 0.0
        if price:
            mv = qty * price
            market_value += mv
        rows.append({"symbol": sym, "qty": qty, "avg_cost": avg, "price": price,
                     "cost": cost, "run": r})
    equity = market_value + cash

    # --- per-holding action
    holdings_out, deploy_ideas = [], []
    for row in rows:
        sym, r, price = row["symbol"], row["run"], row["price"]
        qty, avg, cost = row["qty"], row["avg_cost"], row["cost"]
        mv = qty * price if price else None
        pl = (mv - cost) if (mv is not None and cost is not None) else None
        pl_pct = (pl / cost * 100) if (pl is not None and cost) else None
        pos_pct = (mv / equity * 100) if (mv and equity) else 0.0
        signal = (r or {}).get("signal")
        stop = next((h.get("stop") for h in holds if h["symbol"] == sym), None)
        under_cap = pos_pct < config.RISK["max_position_pct"]

        if (r or {}).get("strategy_version"):
            action, detail = "REVIEW", "Versioned opportunity only; use account admission and the actual position stop."
        elif r is None:
            action, detail = "NO COVERAGE", "Not in the engine universe — tracked for P/L only."
        elif signal == "Exit":
            action, detail = "EXIT", "Engine flags Exit — close the position."
        elif price and stop and price <= stop:
            action, detail = "EXIT (stop hit)", f"Price ≤ stop {stop:.2f} — exit per plan."
        elif signal == "Avoid":
            action, detail = "TRIM / EXIT", "Thesis broken (Avoid) — reduce; do not add."
        elif signal in ("Buy", "Strong Buy"):
            if pl_pct is not None and pl_pct < 0:          # underwater
                if under_cap and cash > 0:
                    action = "AVERAGE DOWN"
                    detail = (f"Still {signal} (thesis intact, not extended) & under "
                              f"{config.RISK['max_position_pct']:.0f}% cap — averaging "
                              "lowers your cost.")
                else:
                    action = "HOLD"
                    detail = ("Underwater but at/over the position cap — hold, don't add.")
            else:                                          # in profit
                if under_cap and cash > 0:
                    action = "ADD"
                    detail = (f"Winner, still {signal} — room under cap to add "
                              "(prefer the buy-zone on a pullback).")
                else:
                    action = "HOLD (let it run)"
                    detail = "Winner but at the position cap — ride it, don't add."
        elif signal == "Watch":
            action = "HOLD"
            bz = _zone_txt(r)
            detail = f"Engine on Watch — hold." + (f" {bz}" if bz else "")
        else:
            action, detail = "HOLD", f"Engine: {signal or 'No data'} — hold."

        holdings_out.append({
            "symbol": sym, "qty": qty, "avg_cost": avg, "price": price,
            "value": mv, "pl": pl, "pl_pct": pl_pct, "pos_pct": pos_pct,
            "signal": signal, "action": action, "detail": detail})

        if action in ("AVERAGE DOWN", "ADD"):
            deploy_ideas.append({"symbol": sym, "signal": signal, "price": price,
                                 "stop": stop, "score": (r or {}).get("final_score") or 0,
                                 "kind": action, "current_value": mv or 0})

    # --- new-position candidates: Buy/Strong Buy not already held
    for sym, r in latest.items():
        if sym in held_syms:
            continue
        if not r.get("strategy_version") and r.get("signal") in ("Buy", "Strong Buy") and r.get("price"):
            deploy_ideas.append({"symbol": sym, "signal": r["signal"],
                                 "price": r["price"], "stop": r.get("stop_loss"),
                                 "score": r.get("final_score") or 0,
                                 "kind": "NEW POSITION", "current_value": 0})

    # --- allocate cash sequentially, best conviction first (Strong Buy, then score)
    deploy_ideas.sort(key=lambda d: (_SIG_RANK.get(d["signal"], 9), -d["score"]))
    cash_left, deploy_out = cash, []
    for d in deploy_ideas:
        shares, value = _size(d["price"], d["stop"], cash_left, equity, d["current_value"])
        if shares <= 0:
            continue
        cash_left -= value
        d2 = dict(d); d2["shares"] = shares; d2["value"] = value
        deploy_out.append(d2)

    # Book P&L is only meaningful when EVERY holding has a cost basis. With one
    # missing, market_value covers all positions while cost_value covers a
    # subset, which reads as a huge phantom gain (+494% on a real book).
    _full_cost = bool(cost_value) and all(h["avg_cost"] is not None for h in holds)
    totals = {
        "cash": cash, "invested_cost": round(cost_value, 0),
        "market_value": round(market_value, 0), "equity": round(equity, 0),
        "cost_basis_complete": _full_cost,
        "pl": round(market_value - cost_value, 0) if _full_cost else None,
        "pl_pct": round((market_value - cost_value) / cost_value * 100, 1) if _full_cost else None,
        "deployed_pct": round(market_value / equity * 100, 1) if equity else 0.0,
        "cash_after_plan": round(cash_left, 0), "n_holdings": len(holds)}
    return {"holdings": holdings_out, "deploy": deploy_out, "totals": totals}


def _zone_txt(r):
    lo, hi = (r or {}).get("buy_zone_low"), (r or {}).get("buy_zone_high")
    return f"Buy-zone {lo:.2f}–{hi:.2f}." if lo and hi else ""
