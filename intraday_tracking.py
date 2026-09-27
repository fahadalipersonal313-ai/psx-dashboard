"""Independent intraday observations and setup episodes; never writes swing history."""
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import config
import session_calendar as cal

PATH = Path(config.BASE_DIR) / 'intraday_observations.db'
RULES = {'version': 'intraday-observation-v1', 'window_minutes': 15,
         'anchor_tolerance_seconds': 120, 'minimum_gain_percent': 1,
         'minimum_recent_percent': .15, 'minimum_window_turnover': 1000000,
         'fresh_seconds': 1200, 'confirm_gap_min_seconds': 600,
         'confirm_gap_max_seconds': 1200}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def time_of(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Observation time needs timezone')
    return dt


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def segment(at):
    local = cal.local_now(at)
    for start, end in cal.intervals(local.date()):
        if start <= local.strftime('%H:%M') < end:
            return local.date().isoformat(), start
    return None


def assess(quote, ticks, market_bar, now, eligible=True):
    """Fixed-window tick measurements; missing/open/gap evidence fails closed."""
    sym = quote['symbol']
    out = {**quote, 'version': RULES['version'], 'state': 'Unavailable', 'qualifies': False,
           'open': None, 'gap_pct': None, 'since_open_pct': None, 'recent_pct': None,
           'window_volume': None, 'window_turnover': None, 'confirmation_reference': None,
           'failure_reference': None, 'volume_confirmation': 'Same-time historical comparison not yet available',
           'risk': 'Spread, fill and exit availability are not verified. Research watch only.', 'reason': ''}
    if not cal.is_live(now):
        out['reason'] = 'Outside regular trading hours'
        return out
    at = time_of(quote['last_trade'])
    if not 0 <= (now-at).total_seconds() <= RULES['fresh_seconds']:
        out['reason'] = 'Latest trade is stale or future-dated'
        return out
    if not eligible or quote.get('note') or quote.get('change_pct') is None:
        out['reason'] = 'Compliance, prior close or price-adjustment check incomplete'
        return out
    points = []
    for raw in ticks:
        try:
            ts, price, volume = map(float, raw[:3])
            dt = datetime.fromtimestamp(ts, now.tzinfo)
            if all(math.isfinite(v) for v in (ts, price, volume)) and price > 0 and volume >= 0 and dt <= now and segment(dt) == segment(now):
                points.append((dt, price, volume))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    points.sort()
    target = now - timedelta(minutes=RULES['window_minutes'])
    anchors = [p for p in points if p[0] <= target and (target-p[0]).total_seconds() <= RULES['anchor_tolerance_seconds']]
    # Undated market-watch rows alone cannot establish today's opening price.
    # Require a current-session tick near the open agreeing with the exchange open.
    opening = None
    for raw in ticks:
        try:
            dt = cal.local_now(datetime.fromtimestamp(float(raw[0]), now.tzinfo))
            spans = cal.intervals(dt.date())
            start = time_of(dt.date().isoformat() + 'T' + spans[0][0] + ':00+05:00') if spans else None
            if dt <= now and dt.date() == cal.local_now(now).date() and start and 0 <= (dt-start).total_seconds() <= 120:
                if opening is None or dt < opening[0]:
                    opening = (dt, float(raw[1]))
        except (ValueError, TypeError, IndexError, OverflowError, OSError):
            continue
    op = (market_bar or {}).get('open')
    if finite(op) and op > 0 and opening and abs(opening[1]/op-1) <= .001:
        out['open'] = op
        out['opening_evidence'] = {'trade_at': opening[0].isoformat(), 'trade_price': opening[1],
                                   'market_watch_open': op}
        out['gap_pct'] = (op / quote['prior_close'] - 1) * 100
        out['since_open_pct'] = (quote['price'] / op - 1) * 100
    if not anchors or segment(target) != segment(now):
        out['reason'] = 'Need a complete 15-minute window within regular trading hours'
        return out
    anchor = anchors[-1]
    out['window_anchor'] = {'time': anchor[0].isoformat(), 'price': anchor[1], 'target_time': target.isoformat()}
    window = [p for p in points if target < p[0] <= now]
    if not window:
        out['reason'] = 'No recent trades'
        return out
    out['recent_pct'] = (quote['price']/anchor[1]-1)*100
    out['window_volume'] = sum(p[2] for p in window)
    out['window_turnover'] = sum(p[1]*p[2] for p in window)
    out['confirmation_reference'] = max(p[1] for p in window)
    out['failure_reference'] = min(p[1] for p in window)
    if out['open'] is None:
        out['reason'] = 'Opening price not verified; recent movement shown without a qualifying setup'
        return out
    if out['recent_pct'] < -RULES['minimum_recent_percent']:
        out.update(state='Weakening', reason='Price is falling over the latest 15-minute window')
    elif (quote['change_pct'] >= RULES['minimum_gain_percent'] and out['since_open_pct'] > 0
          and out['recent_pct'] >= RULES['minimum_recent_percent']
          and out['window_turnover'] >= RULES['minimum_window_turnover']):
        out.update(state='Strengthening', qualifies=True,
                   reason='Above previous close and today’s open; rising over 15 minutes with sufficient traded value')
    else:
        out.update(state='Watching', reason='Price direction or recent trading value has not met all intraday checks')
    return out


def archive(capture, path=None):
    """Same run is idempotent; subsequent observations retained, episodes not doubled."""
    path = Path(path or PATH)
    run_id = digest({'checked_at': capture['checked_at'], 'session': capture['session'], 'rules': RULES})
    now = time_of(capture['checked_at'])
    if cal.local_now(now).date().isoformat() != capture['session']:
        raise ValueError('Run/session mismatch')
    enriched = []
    with closing(sqlite3.connect(path)) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, session TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(run_id TEXT, symbol TEXT, session TEXT, state TEXT, episode TEXT, payload TEXT, PRIMARY KEY(run_id,symbol));
        CREATE TABLE IF NOT EXISTS sessions(symbol TEXT, session TEXT, latest_run TEXT, latest_at TEXT, payload TEXT, PRIMARY KEY(symbol,session));
        CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY, symbol TEXT, session TEXT, activated_at TEXT, ended_at TEXT, first_payload TEXT);
        CREATE TABLE IF NOT EXISTS changes(run_id TEXT, symbol TEXT, old_state TEXT, new_state TEXT, PRIMARY KEY(run_id,symbol));
        ''')
        old_run = c.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if old_run:
            return json.loads(old_run[0])
        # An episode is a same-day watch, never an overnight position. Closing
        # it here records when its expiry was observed, not an executable exit.
        c.execute('UPDATE episodes SET ended_at=? WHERE session<? AND ended_at IS NULL',
                  (capture['checked_at'], capture['session']))
        for row in sorted(capture.get('observations', []), key=lambda x: x['symbol']):
            previous = c.execute('SELECT latest_at,payload FROM sessions WHERE symbol=? AND session=?', (row['symbol'], capture['session'])).fetchone()
            prior = json.loads(previous[1]) if previous else {}
            if previous and time_of(previous[0]) >= now:
                raise ValueError('Out-of-order run would overwrite a newer session')
            gap = (now-time_of(previous[0])).total_seconds() if previous else None
            consecutive = (gap is not None and RULES['confirm_gap_min_seconds'] <= gap <= RULES['confirm_gap_max_seconds']
                           and segment(now) == segment(time_of(previous[0])) and segment(now) is not None
                           and prior.get('version') == row.get('version')
                           and row.get('last_trade') != prior.get('last_trade'))
            row = dict(row)
            episode = prior.get('episode')
            if episode and (not row.get('qualifies') or not consecutive):
                c.execute('UPDATE episodes SET ended_at=? WHERE id=? AND ended_at IS NULL', (capture['checked_at'], episode))
                episode = None
            if row.get('qualifies') and consecutive and prior.get('qualifies'):
                row['state'] = 'Momentum confirmed · watch'
                if not episode:
                    episode = digest({'run': run_id, 'symbol': row['symbol']})
                    c.execute('INSERT INTO episodes VALUES(?,?,?,?,NULL,?)',
                              (episode, row['symbol'], capture['session'], capture['checked_at'], json.dumps(row, allow_nan=False)))
            row['episode'] = episode
            row['observed_at'] = capture['checked_at']
            if prior.get('state') != row['state']:
                c.execute('INSERT INTO changes VALUES(?,?,?,?)', (run_id, row['symbol'], prior.get('state'), row['state']))
            payload = json.dumps(row, allow_nan=False)
            c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)', (run_id, row['symbol'], capture['session'], row['state'], episode, payload))
            c.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?)', (row['symbol'], capture['session'], run_id, capture['checked_at'], payload))
            enriched.append(row)
        result = {**capture, 'run_id': run_id, 'intraday_rules': RULES, 'observations': enriched}
        c.execute('INSERT INTO runs VALUES(?,?,?,?)', (run_id, capture['checked_at'], capture['session'], json.dumps(result, allow_nan=False)))
    return result
