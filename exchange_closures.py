"""PSX exchange closures for Aug-Dec 2026, from PSX's own notices.

Regular PSX hours live in session_calendar. A closure listed here makes
session_calendar.intervals() return no session for that day, so the engine
neither runs nor names it a completed session. Older dates need their actual
(including Ramadan) session notices before use.

Moved unchanged from research_calendar.py on 2026-10-07 when the research
layer was retired; the dates and sources are exchange facts, not research.
"""
from datetime import date

FROM = date(2026, 8, 1)
THROUGH = date(2026, 12, 31)
CLOSURES = {'2026-08-14', '2026-08-26', '2026-11-09', '2026-12-25'}
VERIFIED_AT = '2026-10-03'
SOURCES = [
    'https://www.psx.com.pk/psx/exchange/general/calendar-holidays',
    'https://dps.psx.com.pk/download/attachment/280986-1.pdf',
    'https://dps.psx.com.pk/download/attachment/281476-1.pdf',
]
# Aug 25 on PSX's annual webpage is provisional; the actual Aug 26 closure was
# confirmed by primary PSX notice N-1023 dated Aug 20, 2026. No bank-only
# Jan 1 / Jul 1 closures are inferred to be exchange holidays.


def is_closed(day):
    return FROM <= day <= THROUGH and day.isoformat() in CLOSURES
