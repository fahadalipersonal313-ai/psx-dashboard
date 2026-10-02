"""Read-only candle research display; never changes trading signals."""
import base64
from contextlib import closing
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

REPO = 'fahadalipersonal313-ai/psx-dashboard'
API = 'https://api.github.com/repos/' + REPO
WEB = 'https://github.com/' + REPO
BASELINE = Path(__file__).parent / 'docs/candle-research/evidence.json'
MAX_BYTES = 512_000
PATTERNS = {
    'bullish_engulfing': 'Upward reversal candle',
    'hammer_after_decline': 'Long lower wick after a fall',
    'volume_breakout': 'Price breakout with higher volume',
    'trend_pullback': 'Pullback in an upward trend',
}


def _json(url, get=None):
    import requests
    with (get or requests.get)(url, timeout=(3, 6), stream=True,
                              headers={'Accept': 'application/vnd.github+json'}) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(16_384):
            body.extend(chunk)
            if len(body) > 2 * MAX_BYTES:
                raise ValueError('Research response exceeds size limit')
        return json.loads(body)


def _blob(sha, get=None):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid research file identifier')
    blob = _json(API + '/git/blobs/' + sha, get)
    if blob.get('sha') != sha or blob.get('encoding') != 'base64':
        raise ValueError('Research blob does not match its listed identifier')
    raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
    if len(raw) > MAX_BYTES or blob.get('size') != len(raw):
        raise ValueError('Invalid research file size')
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if actual != sha:
        raise ValueError('Research file checksum mismatch')
    return json.loads(raw)


def _validate(record, cutoff):
    if (not isinstance(record, dict) or record.get('cutoff') != cutoff
            or record.get('status') != 'available'
            or not isinstance(record.get('coverage'), list) or not record['coverage']
            or not isinstance(record.get('pattern_diagnostics'), list)
            or not isinstance(record.get('specification'), dict)):
        raise ValueError('Research checkpoint is incomplete or has the wrong date')
    symbols = []
    for row in record['coverage']:
        symbols.append(row['symbol'])
        if (not isinstance(row['symbol'], str) or not row['symbol']
                or type(row['bars']) is not int or row['bars'] < 0
                or type(row['unusable']) is not int or not 0 <= row['unusable'] <= row['bars']
                or not isinstance(row.get('latest_patterns'), list)
                or any(not isinstance(pattern, str) for pattern in row['latest_patterns'])
                or not date.fromisoformat(row['first']).isoformat() <= date.fromisoformat(row['last']).isoformat() <= cutoff):
            raise ValueError('Invalid stock research coverage')
    if len(symbols) != len(set(symbols)):
        raise ValueError('Duplicate stock coverage')
    if any(not isinstance(record.get(key, []), list) or any(not isinstance(row, dict) for row in record.get(key, []))
           for key in ('proposals', 'outcomes')):
        raise ValueError('Invalid research idea records')
    pairs = set()
    for row in record['pattern_diagnostics']:
        if (row['partition'] not in ('train', 'validation', 'diagnostic_test')
                or not isinstance(row['pattern'], str)
                or any(type(row[key]) is not int or row[key] < 0 for key in ('priced', 'unresolved', 'unfilled', 'purged'))):
            raise ValueError('Invalid pattern research counts')
        pair = row['partition'], row['pattern']
        if pair in pairs:
            raise ValueError('Duplicate pattern results')
        pairs.add(pair)
        fraction = row.get('priced_coverage_fraction')
        if fraction is not None and (not math.isfinite(fraction) or not 0 <= fraction <= 1):
            raise ValueError('Invalid research coverage fraction')
        for scenario in row['cost_scenarios']:
            if type(scenario['round_trip_bps']) is not int:
                raise ValueError('Invalid cost scenario')
            for key in ('positive_fraction', 'mean_pct'):
                if scenario[key] is not None and not math.isfinite(scenario[key]):
                    raise ValueError('Invalid pattern returns')
            if scenario['positive_fraction'] is not None and not 0 <= scenario['positive_fraction'] <= 1:
                raise ValueError('Invalid positive-result fraction')
    return record


def load_latest(get=None, blob_loader=None, baseline=BASELINE):
    """Read a listed immutable blob; an offline baseline is always explicitly labelled."""
    try:
        listing = _json(API + '/contents/checkpoints?ref=research-evidence', get)
        if not isinstance(listing, list) or len(listing) >= 1000:
            raise ValueError('Research listing unavailable or needs year partitioning')
        candidates = []
        for item in listing:
            name = item.get('name', '')
            if item.get('type') != 'file' or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', name):
                continue
            try:
                day = date.fromisoformat(name[:-5]).isoformat()
            except ValueError:
                continue
            if day == name[:-5]:
                candidates.append((day, item))
        cutoff, item = max(candidates, key=lambda pair: pair[0])
        if not isinstance(item.get('size'), int) or not 0 < item['size'] <= MAX_BYTES:
            raise ValueError('Research checkpoint exceeds size limit')
        record = (blob_loader or (lambda sha: _blob(sha, get)))(item['sha'])
        return _validate(record, cutoff), {'source': 'cloud', 'cutoff': cutoff, 'sha': item['sha']}
    except Exception:
        try:
            seed = json.loads(Path(baseline).read_text(encoding='utf-8'))
            cutoff = min(row['last'] for row in seed['coverage'])
            record = {**seed, 'cutoff': cutoff, 'status': 'baseline', 'proposals': [], 'outcomes': []}
            _validate({**record, 'status': 'available'}, cutoff)
            return record, {'source': 'bundled baseline', 'cutoff': cutoff}
        except (OSError, ValueError, TypeError, KeyError):
            return None, {'source': 'unavailable'}


def current_status(record, meta, expected):
    if not record:
        return 'unavailable'
    if record['cutoff'] > expected:
        return 'future-dated'
    if meta['source'] != 'cloud':
        return 'offline baseline'
    return 'current' if record['cutoff'] == expected else 'waiting for newer data'


def result_rows(record, partition='diagnostic_test', cost=70):
    rows = []
    for pattern in record['pattern_diagnostics']:
        if pattern['partition'] != partition:
            continue
        scenario = next((c for c in pattern['cost_scenarios'] if c['round_trip_bps'] == cost), None)
        if scenario is None:
            continue
        percent = lambda value: f'{100 * value:.1f}%' if value is not None else 'Not measured'
        rows.append({'Pattern': PATTERNS.get(pattern['pattern'], pattern['pattern']),
                     'Results with prices': pattern['priced'],
                     'Positive results': percent(scenario['positive_fraction']),
                     'Average return': f"{scenario['mean_pct']:+.2f}%" if scenario['mean_pct'] is not None else 'Not measured',
                     'Results with usable prices': percent(pattern.get('priced_coverage_fraction')),
                     'Waiting / could not fill': pattern['unresolved'] + pattern['unfilled']})
    return rows


def candles(database, symbol, cutoff):
    """Reuse at most 120 local bars read-only; never download a second database."""
    if database is None:
        return []
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute('SELECT date,open,high,low,close,volume FROM daily_ohlc '
                               'WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 120',
                               (symbol, cutoff)).fetchall()
        from data_quality import bar_error
        return [dict(row) for row in reversed(rows) if not bar_error(dict(row))]
    except (OSError, sqlite3.Error):
        return []


def show(st, loaded=None, database=None):
    if loaded is None:
        @st.cache_data(ttl=86_400, show_spinner=False)
        def cached_blob(sha):
            return _blob(sha)

        @st.cache_data(ttl=300, show_spinner=False)
        def cached_latest():
            return load_latest(blob_loader=cached_blob)

        loaded = cached_latest()
    record, meta = loaded
    import session_calendar
    expected = session_calendar.last_completed()
    state = current_status(record, meta, expected)
    st.subheader('Candle research')
    st.caption('Daily study of the tracked stocks. Findings need validation before becoming buy rules.')
    if state in ('unavailable', 'future-dated'):
        st.warning('Research is unavailable or dated after the latest completed session. Results are not shown.')
        st.link_button('Check the cloud research run', WEB + '/actions/workflows/candle-research.yml')
        return
    if state == 'offline baseline':
        st.warning('Cloud research could not be loaded. Showing the original saved study, not a live cloud update.')
    elif state != 'current':
        st.warning(f"Research prices end on {record['cutoff']}. Waiting for {expected}; the saved results below are older.")
    st.caption(f"Prices through {record['cutoff']} · {meta['source']} · daily run: 03:20 Pakistan time, Tuesday–Saturday")
    coverage = record['coverage']
    metrics = st.columns(3)
    metrics[0].metric('Stocks studied', len(coverage))
    metrics[1].metric('Daily candles studied', f"{sum(row['bars'] for row in coverage):,}")
    metrics[2].metric('Chance of a profitable trade', 'Not measured')
    choices = {'2026 so far': 'diagnostic_test', '2025': 'validation', 'Through 2024': 'train'}
    left, right = st.columns(2)
    period = left.selectbox('Study period', list(choices), key='candle_research_period')
    cost = right.selectbox('Assumed total buying and selling cost', [70, 100, 0],
                           format_func=lambda value: f'{value / 100:.2f}%', key='candle_research_cost')
    results = result_rows(record, choices[period], cost)
    st.dataframe(results, hide_index=True, width='stretch')
    means = [c['mean_pct'] for p in record['pattern_diagnostics'] if p['partition'] == choices[period]
             for c in p['cost_scenarios'] if c['round_trip_bps'] == cost and c['mean_pct'] is not None]
    if means and all(value < 0 for value in means):
        st.info('Every pattern shown had a negative average return in this study. They do not support new buy rules.')
    else:
        st.info('These historical results still need future validation before a pattern becomes a buy rule.')
    st.caption('Positive results are past price examples, not a probability of profit. Costs are assumptions, '
               'not confirmed broker charges. The example buys at the next opening price and exits at the fifth later close. '
               'Missing prices and unfilled examples remain visible; patterns and stocks can overlap.')
    excluded = sum(p['purged'] for p in record['pattern_diagnostics'] if p['partition'] == choices[period])
    if excluded:
        st.caption(f'{excluded} examples crossing the study-period boundary were excluded.')

    st.markdown('**Latest candle observations**')
    observed = [{'Stock': row['symbol'], 'Pattern': PATTERNS.get(pattern, pattern),
                 'Meaning': 'Research observation; not a buy signal'}
                for row in coverage for pattern in row.get('latest_patterns', [])]
    if observed:
        st.dataframe(observed, hide_index=True, width='stretch')
    else:
        st.caption('No tracked candle pattern was present on the study date.')
    proposals, outcomes = record.get('proposals', []), record.get('outcomes', [])
    eligible = sum(bool(p.get('prospective_eligible')) for p in proposals)
    st.caption(f"New ideas saved before later prices: {eligible}. Earlier ideas checked: {len(outcomes)}. "
               'The first daily record is a historical starting point, not future validation.')

    symbols = {row['symbol']: row for row in coverage}
    symbol = st.selectbox('Read one stock', sorted(symbols), key='candle_research_stock')
    stock = symbols[symbol]
    st.caption(f"{symbol}: {stock['bars']:,} daily candles · {stock['first']} to {stock['last']} · "
               f"{stock['unusable']} rows flagged for price review")
    bars = candles(database, symbol, record['cutoff'])
    if bars:
        import plotly.graph_objects as go
        figure = go.Figure(go.Candlestick(x=[r['date'] for r in bars], open=[r['open'] for r in bars],
                                         high=[r['high'] for r in bars], low=[r['low'] for r in bars],
                                         close=[r['close'] for r in bars], name=symbol))
        figure.update_layout(template='plotly_dark', height=340, xaxis_rangeslider_visible=False,
                             margin=dict(l=12, r=12, t=15, b=12))
        st.plotly_chart(figure, width='stretch', key='candle_research_chart')
        st.caption(f"Available dashboard candles through {bars[-1]['date']}. This local chart may be older than "
                   'the research snapshot. Raw prices can contain split or dividend gaps.')
    st.caption('Price adjustments, actual fills and fees, and reliable intraday trade outcomes still need checking. '
               'The study covers today’s stock list; it is not an untouched future test.')
    a, b = st.columns(2)
    a.link_button('Read the full research audit', WEB + '/blob/main/docs/candle-research/AUDIT.md')
    b.link_button('Open daily reports and all 60 charts', WEB + '/actions/workflows/candle-research.yml')
