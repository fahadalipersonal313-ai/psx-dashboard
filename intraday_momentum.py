"""Current-session watch ideas, with a separate archive; never changes swing history."""
import concurrent.futures
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests
import config
import database as db
import momentum
import session_calendar as calendar
import shariah_checker
from data_quality import bar_error, source_priority

PATH = Path('intraday_momentum.json')


def detect(symbol, ticks, history, now):
    today = calendar.local_now(now).date().isoformat()
    rows = []
    for tick in ticks:
        ts, price, volume = map(float, tick[:3])
        if not all(math.isfinite(x) for x in (ts, price, volume)) or price <= 0 or volume < 0:
            raise ValueError('Invalid intraday trade')
        at = datetime.fromtimestamp(ts, timezone.utc)
        if at > now:
            raise ValueError('Future intraday trade')
        if calendar.local_now(at).date().isoformat() == today:
            rows.append((at, price, volume))
    if not rows:
        return None
    rows.sort()
    at, price, _ = rows[-1]
    if (now - at).total_seconds() > 1200:
        return None
    prior = [x for x in history if x['date'] < today][-20:]
    if len(prior) != 20 or prior[-1]['date'] != calendar.last_completed(datetime.combine(calendar.local_now(now).date(), datetime.min.time(), calendar.PKT)):
        return None
    if any(bar_error(x) or source_priority(x.get('source')) < 3 or x['volume'] <= 0 for x in prior):
        return None
    if not shariah_checker.check(symbol)['eligible_for_ranking']:
        return None
    if any(abs(b['close'] / a['close'] - 1) > .105 for a, b in zip(prior, prior[1:])):
        return None
    turnover = statistics.median(x['close'] * x['volume'] for x in prior)
    if turnover < momentum.MIN_TURNOVER:
        return None
    gain = (price / prior[-1]['close'] - 1) * 100
    if abs(gain) > 10.5:
        return None  # possible unadjusted corporate action; do not invent a gain
    # Conservative: volume SO FAR must exceed a full day's average. No assumed
    # volume curve, Friday extrapolation, or historical success rate for live calls.
    multiple = sum(x[2] for x in rows) / statistics.mean(x['volume'] for x in prior)
    if gain < momentum.MIN_GAIN_PCT or multiple < momentum.MIN_VOL_MULT:
        return None
    return {'symbol': symbol, 'gain_pct': gain, 'vol_mult': multiple,
            'price': price, 'last_trade': at.isoformat(), 'date': today}


def quote(symbol, ticks, history, now):
    """Last traded price this session, for EVERY symbol -- no momentum filter.

    detect() answers "is this a Watch idea?" and returns None for the other 59
    names, so the prices it had in hand were thrown away. That is why the
    dashboard could show nothing live on a day when all 60 symbols had fresh
    ticks. This keeps the price; it applies no opinion to it.

    Never fabricates: a missing or unvalidated prior close yields change=None
    rather than a number, and a tick older than 20 minutes is returned marked
    stale rather than hidden, because a silently old price is the dangerous one.
    """
    today = calendar.local_now(now).date().isoformat()
    rows = []
    for tick in ticks:
        try:
            ts, price, volume = map(float, tick[:3])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (ts, price, volume)) or price <= 0 or volume < 0:
            continue
        at = datetime.fromtimestamp(ts, timezone.utc)
        if at > now:
            continue                      # a future trade is bad data, not a quote
        if calendar.local_now(at).date().isoformat() == today:
            rows.append((at, price, volume))
    if not rows:
        return None
    rows.sort()
    at, price, _ = rows[-1]
    age_s = (now - at).total_seconds()

    prior_close = change = None
    prior = [x for x in history if x['date'] < today]
    if prior:
        last = prior[-1]
        if not bar_error(last) and source_priority(last.get('source')) >= 3 \
                and last.get('close'):
            prior_close = float(last['close'])
            change = (price / prior_close - 1) * 100
    suspect = change is not None and abs(change) > 10.5   # beyond the circuit limit
    return {'symbol': symbol, 'price': price, 'prior_close': prior_close,
            'change_pct': None if suspect else change,
            'day_volume': sum(r[2] for r in rows), 'trades': len(rows),
            'last_trade': at.isoformat(), 'stale': age_s > 1200,
            'age_minutes': round(age_s / 60, 1),
            # A move beyond the circuit limit means an unadjusted corporate
            # action far more often than a real 10%+ gap, so the percentage is
            # withheld and the reason is stated rather than a wrong number shown.
            'note': 'change withheld: move exceeds the circuit limit, '
                    'likely an unadjusted corporate action' if suspect else None}


def collect(now=None):
    now = now or datetime.now(timezone.utc)
    day = calendar.local_now(now).date().isoformat()
    result = {'session': day, 'checked_at': now.isoformat(), 'source': 'PSX DPS intraday',
              'checked': 0, 'fresh': 0, 'failed': [], 'items': [], 'prices': [], 'observations': []}
    import intraday_tracking
    import psx_market_watch
    market, market_meta = psx_market_watch.fetch() if calendar.is_live(now) else ({}, {'ok': False, 'error': 'Market closed'})
    result['market_watch'] = market_meta
    result['news_reviews'] = {}
    for name in ('news_ai_ratings.json', 'news_codex_ratings.json'):
        try:
            result['news_reviews'][name] = json.loads((Path(config.BASE_DIR) / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            result['news_reviews'][name] = {'status': 'unavailable'}
    with db.conn() as c:
        history = {s: [dict(x) for x in c.execute(
            'SELECT * FROM daily_ohlc WHERE symbol=? AND date<? ORDER BY date DESC LIMIT 20', (s, day))][::-1]
            for s in config.STOCKS}
    def fetch(symbol):
        response = requests.get(config.PSX_INTRADAY_URL.format(symbol=symbol), timeout=15)
        response.raise_for_status()
        ticks = response.json()['data']
        checked = datetime.now(timezone.utc)
        try:
            candidate = detect(symbol, ticks, history[symbol], checked)
        except (ValueError, TypeError, IndexError):
            candidate = None
        live = quote(symbol, ticks, history[symbol], checked)
        last = datetime.fromisoformat(live['last_trade']) if live else None
        fresh = bool(last and calendar.local_now(last).date().isoformat() == day
                     and 0 <= (checked-last).total_seconds() <= 1200)
        observation = None
        if live:
            prior_day = calendar.last_completed(datetime.combine(calendar.local_now(checked).date(), datetime.min.time(), calendar.PKT))
            valid_prior = bool(history[symbol] and history[symbol][-1]['date'] == prior_day)
            compliance = shariah_checker.check(symbol)
            observation = intraday_tracking.assess(live, ticks, market.get(symbol), checked,
                eligible=valid_prior and compliance['eligible_for_ranking'])
            observation['compliance'] = compliance
            observation['burst'] = bool(candidate)
            observation['full_day_volume_multiple'] = candidate.get('vol_mult') if candidate else None
        return candidate, fresh, live, observation
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, s): s for s in config.STOCKS}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                row, fresh, live, observation = future.result()
                result['checked'] += 1
                result['fresh'] += int(fresh)
                if row:
                    result['items'].append(row)
                if live:
                    result['prices'].append(live)
                if observation:
                    result['observations'].append(observation)
            except Exception:
                result['failed'].append(symbol)
    result['items'].sort(key=lambda x: -x['gain_pct'])
    result['prices'].sort(key=lambda x: (x['change_pct'] is None, -(x['change_pct'] or 0)))
    observed = {r['symbol'] for r in result['observations']}
    for symbol in config.STOCKS:
        if symbol not in observed:
            result['observations'].append({'symbol': symbol, 'state': 'Unavailable', 'qualifies': False,
                'version': intraday_tracking.RULES['version'], 'reason': 'No usable current-session observation',
                'risk': 'Feed missing or failed; no entry assessment'})
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    result = intraday_tracking.archive(result)
    temp = PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    temp.replace(PATH)
    print(f"Intraday scan {day}: {result['checked']}/{len(config.STOCKS)} checked; {len(result['items'])} candidates; {len(result['prices'])} live prices; {len(result['failed'])} failed")
    return result


def show(st, now=None, details=True):
    now = now or datetime.now(timezone.utc)
    # Read ONCE, then decide per panel. The momentum trigger needs a capture
    # under 20 minutes old to mean anything; a price does not stop being the
    # last traded price because the file is 25 minutes old. Prices used to sit
    # behind the momentum freshness gate and simply vanished whenever the
    # dashboard's checkout lagged the loop -- which is most of the time, since
    # the loop commits every ~15 minutes and the host does not redeploy on
    # every commit. Showing a stale price WITH its age is the whole point.
    data, age = None, None
    try:
        data = json.loads(PATH.read_text(encoding='utf-8'))
        age = (now - datetime.fromisoformat(data['checked_at'])).total_seconds()
    except (OSError, ValueError, KeyError):
        data, age = None, None
    if data is not None and 'observations' in data:
        from opportunity_cards import intraday_panel
        intraday_panel(st, data, now, details=details)
        return
    if data is not None and details:
        with st.expander("Last captured prices · all stocks", expanded=False):
            show_prices(st, data, now, age)

    st.markdown('### Intraday momentum · legacy capture')
    try:
        if data is None:
            raise ValueError('No capture')
        if data['session'] != calendar.local_now(now).date().isoformat() or not 0 <= age <= 1200:
            raise ValueError('Capture is old')
    except (OSError, ValueError, KeyError):
        st.info('No fresh current-session scan. Earlier-session stocks are shown separately below.')
        return
    items = [x for x in data['items'] if 0 <= (now - datetime.fromisoformat(x['last_trade'])).total_seconds() <= 1200]
    st.caption(f"Checked {age/60:.0f} minutes ago · {data['checked']}/{len(config.STOCKS)} stocks checked · {data.get('fresh', 0)} with fresh trades · {len(data['failed'])} failed. Updates about every 15 minutes while the loop runs.")
    st.caption('Watch ideas only. Price is up at least 2%; traded volume already exceeds 1.3 times the previous 20-day daily average. These live checks have no measured success rate and can fade. Daily Buy calls still use completed sessions.')
    if items:
        st.dataframe([{'Stock': x['symbol'], 'Rise %': round(x['gain_pct'], 2),
                       'Volume / daily average': round(x['vol_mult'], 2), 'Price': x['price'],
                       'Last trade (PKT)': calendar.local_now(datetime.fromisoformat(x['last_trade'])).strftime('%H:%M:%S')}
                      for x in items], hide_index=True)
    elif not data.get('fresh'):
        st.info('No stocks have fresh current-session trades. Current momentum is unavailable.')
    else:
        st.info('No freshly traded stocks passed these momentum checks.')


def quote_is_current(quote, now):
    """Judge freshness at page viewing time, never from a saved fresh flag."""
    try:
        at = datetime.fromisoformat(quote['last_trade'])
        return (calendar.is_live(now)
                and calendar.local_now(at).date() == calendar.local_now(now).date()
                and 0 <= (now - at).total_seconds() <= 1200)
    except (KeyError, TypeError, ValueError):
        return False


def show_prices(st, data, now, capture_age_s=None):
    """Today's price for every stock, whether or not it is a Watch idea.

    The momentum panel above answers one question and correctly stays silent on
    a quiet day. That silence was being read as "no live data" when in fact all
    60 symbols had fresh ticks, so the prices were fetched every cycle and then
    discarded. This shows them.

    Context only, and that is enforced rather than promised: nothing here is
    written to daily_ohlc, reaches decision_engine, or carries score weight. The
    signals above it are still computed from the last COMPLETED session, which
    is why they can legitimately differ from these numbers all day.
    """
    prices = data.get('prices') or []
    if not prices:
        return
    st.markdown('### Last captured prices')
    # State the capture's own age first. A price from a session that is not
    # today is labelled as such rather than passed off as live.
    session = data.get('session')
    today = calendar.local_now(now).date().isoformat()
    if session != today:
        st.warning(f"These are the last prices captured on {session}, not today. "
                   "The engine loop has not published a scan for today yet.")
    elif capture_age_s is not None and capture_age_s > 1200:
        st.warning(f"Captured {capture_age_s / 60:.0f} minutes ago — the page is "
                   "behind the engine, or the loop has stopped. Prices below are "
                   "real but not current; reload before acting on them.")
    fresh = [p for p in prices if quote_is_current(p, now)]
    moved = [p for p in fresh if p.get('change_pct') is not None]
    up = sum(1 for p in moved if p['change_pct'] > 0)
    down = sum(1 for p in moved if p['change_pct'] < 0)
    st.caption(
        f"{len(fresh)} of {len(prices)} stocks have current quotes · "
        f"{up} up, {down} down, {len(moved)-up-down} unchanged. "
        "Swing calls use completed sessions; this table is price context.")
    rows = []
    for p in prices:
        change = p.get('change_pct')
        rows.append({
            'Stock': p['symbol'],
            'Last price': round(p['price'], 2),
            'Change %': None if change is None else round(change, 2),
            'Previous close': None if p.get('prior_close') is None else round(p['prior_close'], 2),
            'Trades in captured session': p.get('trades'),
            'Last trade (PKT)': calendar.local_now(
                datetime.fromisoformat(p['last_trade'])).strftime('%H:%M:%S'),
            'Note': p.get('note') or ('' if quote_is_current(p, now) else 'Not current'),
        })
    st.dataframe(rows, hide_index=True, height=420)


if __name__ == '__main__':
    collect()
