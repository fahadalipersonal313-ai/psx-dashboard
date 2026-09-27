"""Is the PSX news routine allowed to run right now?

Gate for the hourly news cron. GitHub cron fires in UTC and cannot express
"weekdays, session hours only, except holidays", so the schedule is deliberately
loose and this module makes the real decision. Exits the workflow early rather
than fetching outside the session.
"""
import datetime
import sys

import config

PKT = datetime.timezone(datetime.timedelta(hours=5))


def pkt_now():
    # Pakistan is UTC+5 year-round (no DST), and runners/containers are UTC —
    # datetime.now() without this offset puts every session time before the open.
    return datetime.datetime.now(PKT)


def _hhmm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def check(now=None):
    """Return (ok: bool, reason: str). Reason is logged either way."""
    now = now or pkt_now()
    stamp = now.strftime("%Y-%m-%d %H:%M PKT")

    if now.weekday() >= 5:
        return False, f"{stamp}: weekend ({now:%A}) — PSX closed"

    if now.strftime("%Y-%m-%d") in set(config.PSX_HOLIDAYS):
        return False, f"{stamp}: PSX trading holiday"

    key = "fri" if now.weekday() == 4 else "mon_thu"
    start, end = (_hhmm(t) for t in config.NEWS_WINDOW[key])
    if not (start <= now.time() <= end):
        return False, (f"{stamp}: outside the {key} news window "
                       f"{start:%H:%M}-{end:%H:%M}")

    return True, f"{stamp}: inside the {key} window {start:%H:%M}-{end:%H:%M}"


def session_anchor(now=None):
    """Start of the news window that matters for the session in play.

    Rules 1 and 3 of the news policy together: a headline counts for exactly
    one session, and news breaking after a close belongs to the NEXT session.
    So the anchor is always the most recent close that precedes the session
    currently being traded (or about to be):

      - during a session       -> the PREVIOUS close. Overnight news is part of
        what this session must price in, which is precisely rule 3.
      - after today's close     -> today's close. Anything from here belongs to
        tomorrow, not to the session that just ended.
      - weekend / holiday       -> the last close before it.

    Returns a timezone-aware PKT datetime. Anything published before it is
    stale for scoring (it remains in the 24h window for display).
    """
    now = now or pkt_now()
    key = "fri" if now.weekday() == 4 else "mon_thu"
    close_t = _hhmm(config.NEWS_WINDOW[key][1])

    # Past today's close on a trading day: today's close is the anchor.
    if _is_trading_day(now) and now.time() > close_t:
        return now.replace(hour=close_t.hour, minute=close_t.minute,
                           second=0, microsecond=0)

    # Otherwise (mid-session, pre-open, weekend, holiday) walk back to the
    # previous trading day's close.
    probe = now - datetime.timedelta(days=1)
    while not _is_trading_day(probe):
        probe = probe - datetime.timedelta(days=1)
    k = "fri" if probe.weekday() == 4 else "mon_thu"
    c = _hhmm(config.NEWS_WINDOW[k][1])
    return probe.replace(hour=c.hour, minute=c.minute, second=0, microsecond=0)


def _is_trading_day(dt):
    return dt.weekday() < 5 and dt.strftime("%Y-%m-%d") not in set(config.PSX_HOLIDAYS)


if __name__ == "__main__":
    ok, reason = check()
    print(reason)
    sys.exit(0 if ok else 1)
