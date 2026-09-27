"""Flag unexplained price discontinuities without guessing action factors. Only verified sourced action terms with explicit price and volume factors may adjust raw views. Historical raw bars remain unchanged."""

import logging

import numpy as np
import pandas as pd

import config

log = logging.getLogger("corporate_actions")

LIMIT_PCT = getattr(config, "CIRCUIT_LIMIT_PCT", 10.0)
DETECT_PCT = LIMIT_PCT + 0.5          # small buffer over the circuit
LIMIT_TOL = 0.3                       # "at the limit" band, for flagging


def detect(panel):
    """-> list of {symbol, ex_date, ratio, factor, kind}.

    `ratio` is close_after / close_before. `factor` is what historical prices
    must be MULTIPLIED by to sit on the post-action scale.
    """
    close = panel["close"]
    r = close.pct_change(fill_method=None) * 100
    out = []
    for sym in close.columns:
        s = r[sym].dropna()
        for dt, pct in s[s.abs() > DETECT_PCT].items():
            ratio = 1 + pct / 100
            out.append({
                "symbol": sym, "ex_date": dt.strftime("%Y-%m-%d"),
                "ratio": round(float(ratio), 6),
                "factor": None,
                # An implied 7/6 or 10/9 is the fingerprint of a 1:6 or 10%
                # bonus; it is recorded for eyeballing, never used to decide.
                "implied": round(1 / float(ratio), 4),
                "kind": "price_cut" if pct < 0 else "unexplained_gap_up",
                "pct": round(float(pct), 3),
            })
    log.info("Detected %d beyond-circuit moves (%d adjustable price cuts)",
             len(out), sum(1 for a in out if a["factor"]))
    return out


def adjust(panel, actions):
    """Back-adjust prices for every detected price cut. Returns a NEW panel.

    Prices before an ex-date are multiplied by the ratio so the series is
    continuous on today's scale; volume is divided by it, because a split
    multiplies the share count and raw historical volume would otherwise look
    artificially small. Only `price_cut` actions are applied — an unexplained
    gap up is left exactly as the exchange reported it.
    """
    if not actions:
        return panel
    close = panel["close"]
    factors = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    volume_factors = factors.copy()
    applied = 0
    for a in actions:
        if not valid_action(a) or a["symbol"] not in close.columns:
            continue
        ex = pd.Timestamp(a["ex_date"])
        # everything strictly BEFORE the ex-date moves onto the new scale
        factors.loc[factors.index < ex, a["symbol"]] *= a["factor"]
        volume_factors.loc[volume_factors.index < ex, a["symbol"]] *= a["volume_factor"]
        applied += 1
    out = {}
    for field in ("open", "high", "low", "close"):
        out[field] = panel[field] * factors
    out["volume"] = panel["volume"] * volume_factors
    log.info("Applied %d price-cut adjustments", applied)
    return out


def at_limit_mask(panel):
    """True where the session closed at the circuit limit. Those bars are often
    unfillable — there is frequently no counterparty — so a signal generated on
    one is not a trade you could have taken."""
    r = panel["close"].pct_change(fill_method=None) * 100
    return (r.abs() >= LIMIT_PCT - LIMIT_TOL) & (r.abs() <= LIMIT_PCT + LIMIT_TOL)


def turnover(panel, window=20):
    """Rolling median daily turnover in PKR — the only honest read on whether a
    name is tradeable at a given size."""
    return (panel["close"] * panel["volume"]).rolling(window, min_periods=10).median()


def main():
    import database as db
    import market_factors as mf
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db.init_db()
    panel = mf.load_panel(db, adjust=False)
    actions = detect(panel)
    stored = db.save_corporate_actions(actions)
    cuts = [a for a in actions if a["factor"]]
    print(f"detected {len(actions)} beyond-circuit moves, {stored} newly stored")
    print(f"  adjustable price cuts : {len(cuts)}")
    print(f"  unexplained gap-ups   : {len(actions) - len(cuts)} (flagged, NOT adjusted)\n")
    print(f"{'symbol':8s}{'ex_date':12s}{'move':>9s}{'implied':>9s}  note")
    for a in sorted(cuts, key=lambda x: x["pct"])[:15]:
        print(f"{a['symbol']:8s}{a['ex_date']:12s}{a['pct']:8.2f}%{a['implied']:9.3f}  "
              f"{'~' + str(round(a['implied'], 2)) + 'x shares'}")
    lim = at_limit_mask(panel)
    print(f"\nbars closing AT the +-{LIMIT_PCT}% circuit: {int(lim.sum().sum()):,} "
          f"of {int(panel['close'].notna().sum().sum()):,}")


if __name__ == "__main__":
    main()


def valid_action(action):
    from data_quality import finite
    try:
        return (action.get("verified") is True or action.get("verified") == 1) and bool(action.get("source")) and action.get("kind") in ("split", "bonus", "rights", "dividend") and finite(action.get("factor"), True) and finite(action.get("volume_factor"), True) and pd.Timestamp(action["known_at"]) <= pd.Timestamp(action["ex_date"])
    except (KeyError, ValueError, TypeError):
        return False


def verified_bars(bars, actions, cutoff):
    from copy import deepcopy
    out = deepcopy(bars)
    for action in actions:
        if not valid_action(action) or action["ex_date"] > cutoff or action["known_at"] > cutoff:
            continue
        for bar in out:
            if bar["date"] < action["ex_date"]:
                for key in ("open", "high", "low", "close"):
                    bar[key] = float(bar[key]) * float(action["factor"])
                bar["volume"] = float(bar["volume"]) * float(action["volume_factor"])
    return out
