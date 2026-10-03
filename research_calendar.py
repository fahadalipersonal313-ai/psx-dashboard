"""Dated research session manifest, separate from technical outcome history.

Regular PSX hours live in session_calendar. This manifest covers Aug-Dec 2026;
older dates need their actual (including Ramadan) session notices before use.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FROM=date(2026,8,1)
THROUGH=date(2026,12,31)
CLOSURES={'2026-08-14','2026-08-26','2026-11-09','2026-12-25'}
VERIFIED_AT='2026-10-03'
SOURCES=[
 'https://www.psx.com.pk/psx/exchange/general/calendar-holidays',
 'https://dps.psx.com.pk/download/attachment/280986-1.pdf',
 'https://dps.psx.com.pk/download/attachment/281476-1.pdf',
]
# Aug 25 on PSX's annual webpage is provisional; actual Aug 26 closure was
# confirmed by primary PSX notice N-1023 dated Aug20,2026. No bank-only
# Jan 1 / Jul 1 closures are inferred to be exchange holidays.

def expected(cutoff,count=42):
    import session_calendar as cal
    day=date.fromisoformat(cutoff)
    if not FROM<=day<=THROUGH:raise ValueError('Exchange-session manifest does not cover cutoff')
    rows=[]
    while len(rows)<count:
        if day<FROM:raise ValueError('Exchange-session manifest does not cover full feature window')
        if cal.intervals(day):rows.append(day.isoformat())
        day-=timedelta(days=1)
    return list(reversed(rows))
