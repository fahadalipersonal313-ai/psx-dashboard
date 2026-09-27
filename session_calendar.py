"""PSX regular market clock. Dated closures/overrides must come from notices.

Regular hours verified 2026-09-05 against:
https://www.psx.com.pk/psx/exchange/general/trading-hours
The publication delay is an operational assumption, not an exchange promise.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import config

PKT = ZoneInfo('Asia/Karachi')


def local_now(now=None):
    now = now or datetime.now(PKT)
    return now.replace(tzinfo=PKT) if now.tzinfo is None else now.astimezone(PKT)


def intervals(day):
    day = date.fromisoformat(str(day)) if not isinstance(day, date) else day
    key = day.isoformat()
    if key in config.SESSION_OVERRIDES:
        return config.SESSION_OVERRIDES[key]
    if day.weekday() > 4 or key in config.EXCHANGE_HOLIDAYS:
        return []
    return [('09:17', '12:00'), ('14:32', '16:30')] if day.weekday() == 4 else [('09:32', '15:30')]


def is_live(now=None):
    now = local_now(now)
    return any(time.fromisoformat(a) <= now.time().replace(tzinfo=None) < time.fromisoformat(b)
               for a, b in intervals(now.date()))


def last_completed(now=None):
    now = local_now(now)
    day = now.date()
    for _ in range(370):
        spans = intervals(day)
        if spans:
            ready = datetime.combine(day, time.fromisoformat(spans[-1][1]), PKT) + timedelta(minutes=config.PUBLICATION_DELAY_MINUTES)
            if now >= ready:
                return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError('No completed session in configured calendar')


def worker_state(now=None):
    now = local_now(now)
    spans = intervals(now.date())
    if not spans or now.strftime('%H:%M') >= spans[-1][1]:
        return 'closed'
    return 'open' if is_live(now) else 'wait'


if __name__ == '__main__':
    print(worker_state())
