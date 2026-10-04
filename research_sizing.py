"""Pure, unlevered long-position sizing for explicitly assumed scenarios.

This helper is separate from the engine's frozen execution contract and never
places orders or changes a portfolio. Fees and slippage are user assumptions,
not a claim about any broker's charges or executable prices. A modeled stop
loss is NOT a guaranteed maximum loss: gaps, queues and liquidity can make an
actual exit worse. The caller must supply a verified prior-session median daily
volume; ``None`` withholds a quantity rather than silently removing that limit.
"""

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import math
from numbers import Real


_HUNDRED = Decimal(100)
_BPS = Decimal(10000)
_ZERO = Decimal(0)


def _number(name, value):
    """Accept finite real numbers, never booleans or numeric-looking strings."""
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{name} must be a finite number, not a boolean or string")
    try:
        # Reject values which cannot also be represented in the reporting API.
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        if isinstance(value, Decimal):
            result = value
        else:
            result = Decimal(str(value))
    except (InvalidOperation, OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _float(value):
    """Fail closed when a derived reporting amount would overflow/underflow."""
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise ValueError("scenario amounts exceed supported numeric precision")
    return result


def size_scenario(entry, stop, target, capital, cash, loss_budget,
                  fee_bps_per_side, entry_slippage_bps, exit_slippage_bps,
                  fixed_round_trip_cost, position_cap_pct, median_daily_volume,
                  participation_pct, lot_size=1):
    """Return a cost-aware quantity and its modeled trade amounts.

    All costs and risk controls must be supplied explicitly. ``*_pct`` inputs
    are percentages (1 means 1%); ``*_bps`` are basis points (100 means 1%).
    Prices obey 0 < stop < entry < target. Capital is positive; cash and the
    monetary loss budget are each in [0, capital]. Costs and volume cannot be
    negative; fee/slippage rates are below 10,000 bps; percentages are in
    [0, 100]. Lot size must be a positive integer-valued number. Invalid or
    unrepresentable inputs/derived values raise ValueError consistently.

    Entry debit = entry * (1 + entry slip) * (1 + fee). Stop/target proceeds
    = exit price * (1 - exit slip) * (1 - fee). Fees apply to the slipped
    execution price. The entire fixed round-trip cost is reserved at entry,
    added to modeled stop loss and subtracted from target gain. Conservatively,
    the capital position cap also includes that same cash reserve.

    Quantity is the largest lot multiple satisfying cash, modeled loss budget,
    capital position cap and verified-volume participation. Decimal arithmetic
    avoids binary floating-point floor errors at exact quantity boundaries.
    Each share_limits value is independently rounded down to a lot multiple;
    binding_constraints names all limits that tie after that rounding.

    A valid but unaffordable scenario returns status='zero_shares' and zero
    trade totals (no trade means no fixed costs). Missing volume returns
    status='withheld', shares=None, and None trade totals. Net reward/risk is
    None without a trade. Target gain and reward/risk may be negative when
    assumed costs exhaust the target's gross upside; this is a calculation,
    not a recommendation. Output money values are unrounded reporting floats.
    """
    raw = {
        "entry": entry, "stop": stop, "target": target, "capital": capital,
        "cash": cash, "loss_budget": loss_budget,
        "fee_bps_per_side": fee_bps_per_side,
        "entry_slippage_bps": entry_slippage_bps,
        "exit_slippage_bps": exit_slippage_bps,
        "fixed_round_trip_cost": fixed_round_trip_cost,
        "position_cap_pct": position_cap_pct,
        "participation_pct": participation_pct, "lot_size": lot_size,
    }
    values = {key: _number(key, value) for key, value in raw.items()}
    volume = None if median_daily_volume is None else _number(
        "median_daily_volume", median_daily_volume)
    if not 0 < values["stop"] < values["entry"] < values["target"]:
        raise ValueError("prices must satisfy 0 < stop < entry < target")
    if values["capital"] <= 0:
        raise ValueError("capital must be positive")
    for name in ("cash", "loss_budget"):
        if not 0 <= values[name] <= values["capital"]:
            raise ValueError(f"{name} must be between zero and capital")
    for name in ("fee_bps_per_side", "entry_slippage_bps", "exit_slippage_bps"):
        if not 0 <= values[name] < _BPS:
            raise ValueError(f"{name} must be between zero and less than 10000 bps")
    for name in ("position_cap_pct", "participation_pct"):
        if not 0 <= values[name] <= _HUNDRED:
            raise ValueError(f"{name} must be between zero and 100 percent")
    if values["fixed_round_trip_cost"] < 0:
        raise ValueError("fixed_round_trip_cost cannot be negative")
    if volume is not None and volume < 0:
        raise ValueError("median_daily_volume cannot be negative")
    lot = values["lot_size"]
    if lot < 1 or lot != lot.to_integral_value():
        raise ValueError("lot_size must be a positive integer")

    # Preserve enough digits for price/slip/fee multiplication and subtraction,
    # including unusually large/small finite inputs, without global state.
    numbers = list(values.values()) + ([] if volume is None else [volume])
    precision = max(80, max(len(n.as_tuple().digits) for n in numbers) * 4 + 32,
                    max(n.adjusted() for n in numbers)
                    - min(n.as_tuple().exponent for n in numbers) + 80)
    with localcontext() as ctx:
        ctx.prec = precision
        fee = values["fee_bps_per_side"] / _BPS
        entry_slip = values["entry_slippage_bps"] / _BPS
        exit_slip = values["exit_slippage_bps"] / _BPS
        fixed = values["fixed_round_trip_cost"]
        entry_price = values["entry"] * (1 + entry_slip)
        stop_price = values["stop"] * (1 - exit_slip)
        target_price = values["target"] * (1 - exit_slip)
        entry_fee, stop_fee, target_fee = (p * fee for p in
                                          (entry_price, stop_price, target_price))
        debit = entry_price + entry_fee
        stop_proceeds = stop_price - stop_fee
        target_proceeds = target_price - target_fee
        unit_loss = debit - stop_proceeds
        unit_gain = target_proceeds - debit
        position_cap = values["capital"] * values["position_cap_pct"] / _HUNDRED
        volume_cap = (None if volume is None else
                      volume * values["participation_pct"] / _HUNDRED)
        if debit <= 0 or unit_loss <= 0:
            raise ValueError("scenario must have positive entry debit and modeled stop loss")

        unit = {
            "entry_execution_price": entry_price, "entry_fee": entry_fee,
            "entry_slippage": entry_price - values["entry"],
            "entry_debit": debit,
            "stop_execution_price": stop_price, "stop_fee": stop_fee,
            "stop_slippage": values["stop"] - stop_price,
            "stop_proceeds": stop_proceeds,
            "target_execution_price": target_price, "target_fee": target_fee,
            "target_slippage": values["target"] - target_price,
            "target_proceeds": target_proceeds,
            "modeled_stop_loss": unit_loss, "target_net_gain": unit_gain,
        }

        # Use exact comparisons of full trade totals after flooring. Division
        # rounding must never allow one extra lot beyond an input constraint.
        def bounded_lots(budget, per_unit):
            available = max(_ZERO, budget - fixed)
            count = int((available / (per_unit * lot)).to_integral_value(rounding=ROUND_FLOOR))
            if count and count * lot * per_unit + fixed > budget:
                count -= 1
            return count * int(lot)

        limits = {
            "cash": bounded_lots(values["cash"], debit),
            "loss_budget": bounded_lots(values["loss_budget"], unit_loss),
            "position_cap": bounded_lots(position_cap, debit),
            "volume_participation": (None if volume_cap is None else
                int((volume_cap / lot).to_integral_value(rounding=ROUND_FLOOR)) * int(lot)),
        }
        result = {
            "status": "withheld" if volume is None else "zero_shares",
            "shares": None if volume is None else 0,
            "cash_required": None if volume is None else 0.0,
            "modeled_stop_loss": None if volume is None else 0.0,
            "target_net_gain": None if volume is None else 0.0,
            "net_reward_risk": None,
            "per_unit_costs": {key: _float(value) for key, value in unit.items()},
            "binding_constraints": ["unverified_volume"] if volume is None else [],
            "share_limits": limits,
            "assumptions": {key: _float(value) for key, value in values.items()},
            "warnings": ["Modeled stop loss is not a guaranteed maximum loss.",
                         "Fees and slippage are explicit scenario assumptions, not verified broker charges."],
        }
        result["assumptions"]["median_daily_volume"] = None if volume is None else _float(volume)
        result["assumptions"]["lot_size"] = int(lot)
        if volume is None:
            result["warnings"].append("Verified median daily volume is required before sizing a quantity.")
            return result

        shares = min(limits.values())
        result["shares"] = shares
        result["binding_constraints"] = [key for key, limit in limits.items() if limit == shares]
        if not shares:
            return result
        required = shares * debit + fixed
        modeled_loss = shares * unit_loss + fixed
        target_gain = shares * unit_gain - fixed
        # Fail closed rather than silently returning an over-sized position.
        if (shares % int(lot) or required > values["cash"]
                or required > position_cap or modeled_loss > values["loss_budget"]
                or shares > volume_cap or modeled_loss <= 0):
            raise ValueError("rounded scenario quantity violates a sizing constraint")
        result.update(status="sized", cash_required=_float(required),
                      modeled_stop_loss=_float(modeled_loss),
                      target_net_gain=_float(target_gain),
                      net_reward_risk=_float(target_gain / modeled_loss))
        if target_gain <= 0:
            result["warnings"].append("Assumed costs leave no positive net gain at the target.")
        return result
