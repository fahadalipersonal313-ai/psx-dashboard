"""Validation shared by ingestion, decisions and execution. No inferred repairs."""
import math
from datetime import date


def finite(value, positive=False):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and (not positive or float(value) > 0)
    except (TypeError, ValueError, OverflowError):
        return False


def bar_error(bar):
    try:
        date.fromisoformat(str(bar['date']))
    except (KeyError, TypeError, ValueError):
        return 'invalid session date'
    if any(not finite(bar.get(k), True) for k in ('open', 'high', 'low', 'close')):
        return 'missing or invalid OHLC'
    if not finite(bar.get('volume')) or float(bar['volume']) < 0:
        return 'invalid volume'
    o, h, l, c = (float(bar[k]) for k in ('open', 'high', 'low', 'close'))
    if not l <= min(o, c) <= max(o, c) <= h:
        return 'inconsistent OHLC'
    return None


def valid_levels(entry, stop, target, target2=None):
    return (all(finite(v, True) for v in (entry, stop, target))
            and float(stop) < float(entry) < float(target)
            and (target2 is None or (finite(target2, True) and float(target2) > float(target))))


def source_priority(source):
    value = str(source).lower()
    if 'historical' in value and 'psx' in value:
        return 3
    if 'official' in value and 'intraday' in value:
        return 2
    if 'intraday' in value:
        return 1
    return 0
