"""One conservative sizing rule for reports and account admission."""
import config
from data_quality import finite


def size(price, stop, equity, cash=None, current_value=0, avg_volume=None):
    cash = equity if cash is None else cash
    if not all(finite(v) for v in (price, stop, equity, cash, current_value)):
        return None
    if not 0 < stop < price or equity <= 0 or cash < 0 or current_value < 0:
        return None
    fee = config.EXECUTION['fee_bps_per_side'] / 10000
    rps = price - stop * (1 - config.EXECUTION['slippage_bps'] / 10000) + fee * (price + stop)
    shares = min(equity * config.RISK['max_risk_per_trade_pct'] / 100 / rps,
                 max(0, equity * config.RISK['max_position_pct'] / 100 - current_value) / price,
                 cash / (price * (1 + fee)))
    if avg_volume is not None:
        if not finite(avg_volume, True):
            return None
        shares = min(shares, avg_volume * config.EXECUTION['max_volume_participation'])
    shares = max(0, int(shares))
    return {'shares': shares, 'value': shares * price, 'risk': shares * rps, 'rps': rps,
            'cash_required': shares * price * (1 + fee)}
