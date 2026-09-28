"""Reuse PSX providers; isolate research cache from the production database."""
import argparse
import csv
import html
import io
import json
import re
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests
import config as engine_config
import psx_historical
import psx_market_watch
from session_calendar import PKT, last_completed
from short_horizon import ROOT, build, config, digest, number, stamp, universe_rows


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def baseline():
    with zipfile.ZipFile(ROOT / 'analysis/baselines/2026-09-18/original.zip') as z:
        return json.loads(z.read('work/final-data.json'))


def parse_screener(text):
    headers, raw = psx_market_watch.parse(text)
    # PSX supplies precise machine numbers alongside rounded visible B/M values.
    table_rows = []
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', text.partition('</thead>')[2], re.S | re.I):
        cells = re.findall(r'<td\b([^>]*)>(.*?)</td>', tr, re.S | re.I)
        if len(cells) >= len(headers):
            table_rows.append(cells)
    if len(table_rows) != len(raw):
        raise ValueError('Screener row alignment changed')
    for row, cells in zip(raw, table_rows):
        for key, (attrs, content) in zip(headers, cells):
            if key == 'listed':
                row[key] = html.unescape(re.sub(r'<[^>]+>', '', content)).strip()
            machine = re.search(r'data-order="([^"]+)"', attrs)
            if key in ('marketcap', 'close', 'peratio', 'volume30avg') and machine:
                row[key] = machine[1]
    return headers, raw


def review_evidence(now):
    items = []
    errors = []
    for name in ('news_ratings.json', 'news_codex_ratings.json'):
        review = read_json(ROOT / name, {})
        for symbol, item in review.get('ratings', {}).items():
            publications = item.get('source_published') or item.get('source_published_at') or []
            try:
                known = stamp(review['as_of'])
                expires = (known + timedelta(hours=config()['news_max_age_hours'])).isoformat()
            except (KeyError, ValueError):
                errors.append(name + ': invalid review time')
                continue
            items.append({'symbol': symbol, 'assessment': item.get('rating'),
                          'rationale': item.get('reason'), 'source': item.get('sources', []),
                          'published_at': max(publications) if publications else None,
                          'known_at': known.isoformat(), 'expires_at': expires,
                          'event_date': None, 'event_risk': 'unreviewed', 'linkage': item.get('relevance'),
                          'quality': 'Existing analyst review; original event document not certified by this adapter',
                          'reviewer': review.get('provider', name), 'source_file': name})
    manual = read_json(ROOT / 'short_horizon_evidence.json', {})
    items.extend(manual.get('items', []))
    return items, manual.get('exclusions', {}), errors


def collect(now=None, cutoff=None, budget=None):
    import ssl_compat
    ssl_compat.enable()
    cfg = config()
    now = now or datetime.now(PKT)
    cutoff = cutoff or last_completed(now)
    if cutoff > last_completed(now):
        raise ValueError('Future or incomplete market session')
    started = time.monotonic()
    budget = cfg['refresh_budget_seconds'] if budget is None else budget
    cache = ROOT / 'reports_out/short_horizon_cache'
    cache.mkdir(parents=True, exist_ok=True)
    errors = []
    sess = requests.Session()
    universe_capture = read_json(cache / 'universe.json', {})
    try:
        age = (now - stamp(universe_capture.get('observed_at'))).total_seconds()
    except ValueError:
        age = -1
    if not 0 <= age < 6 * 3600 or universe_capture.get('parser_version') != 3:
        try:
            response = sess.get(engine_config.PSX_DPS_BASE + '/screener/', headers=psx_market_watch.HEADERS, timeout=20)
            response.raise_for_status()
            headers, raw = parse_screener(response.text)
            if not {'symbol', 'listed', 'marketcap', 'close', 'volume30avg', 'peratio', 'sector'}.issubset(headers) or len(raw) < 100:
                raise ValueError('Official screener missing required columns/rows')
            universe_capture = {'parser_version': 3, 'observed_at': datetime.now(PKT).isoformat(), 'rows': universe_rows(raw),
                                'source': response.url, 'raw_sha256': __import__('hashlib').sha256(response.content).hexdigest()}
            # Metadata may be absent: no guessed company names or sector translations.
            universe_capture['companies'] = {m[0]: html.unescape(m[1]) for m in re.findall(
                r'href="/company/([A-Z0-9]+)"\s+data-title="([^"]+)"', response.text)}
            sectors = {code: html.unescape(label).title() for code, label in
                       re.findall(r'<option value="(08\d\d)">([^<]+)</option>', response.text)}
            for row in universe_capture['rows']:
                row['sector_code'] = row['sector']
                row['sector'] = sectors.get(row['sector'], 'Unmapped sector ' + str(row['sector']))
            write_json(cache / 'universe.json', universe_capture)
        except (requests.RequestException, ValueError) as exc:
            errors.append('Universe refresh failed: ' + str(exc))
            # Previous capture is retained as evidence, but never made freshly eligible.
            universe_capture = {**universe_capture, 'refresh_failed': True}
    if not universe_capture.get('rows'):
        raise RuntimeError('; '.join(errors) or 'No official universe')
    originals = baseline()
    companies = {r['ticker']: r['company'] for r in originals}
    companies.update(universe_capture.get('companies', {}))
    universe = universe_capture['rows']
    symbols = {r['symbol'] for r in universe if r['member'] and not r['exchange_nc']
               and (r['market_cap'] or 0) >= cfg['minimum_market_cap']
               and (r['average_volume'] or 0) >= cfg['minimum_average_volume']
               and (r['quote_price'] or 0) >= cfg['minimum_price']}
    con = sqlite3.connect(Path(engine_config.DB_PATH).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        dates = [r[0] for r in con.execute('SELECT date FROM daily_eod WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT ?',
                                         (engine_config.BENCHMARK_INDEX, cutoff, cfg['sessions']))][::-1]
        if len(dates) != cfg['sessions'] or dates[-1] != cutoff:
            raise RuntimeError('Benchmark session history is not current; refresh existing engine first')
        bars = {s: {} for s in symbols}
        # Published inputs seed subsequent runners; avoid downloading 42 sessions
        # again merely because a GitHub worker has a fresh local cache.
        prior_inputs = read_json(ROOT / 'short_horizon_inputs.json', {})
        for symbol, history in prior_inputs.get('bars', {}).items():
            if symbol in bars:
                for b in history:
                    if dates[0] <= b['date'] <= cutoff:
                        bars[symbol][b['date']] = b
        for raw in con.execute('SELECT * FROM daily_ohlc WHERE date>=? AND date<=?', (dates[0], cutoff)):
            b = dict(raw)
            if b['symbol'] in symbols and b['date'] not in bars[b['symbol']]:
                bars[b['symbol']][b['date']] = b
        actions = [dict(r) for r in con.execute('SELECT * FROM corporate_actions WHERE ex_date>=? AND ex_date<=?', (dates[0], cutoff))]
    finally:
        con.close()
    provenance = {'database': 'Read-only daily_ohlc and daily_eod; original retrieval timestamps unavailable',
                  'history_source': psx_historical.URL, 'fetched_sessions': [], 'cached_sessions': []}
    # Shared whole-market day requests; at most one request per date, never one per stock.
    for day in reversed(dates):
        cached = read_json(cache / (day + '.json'), {})
        if cached.get('session') == day:
            for b in cached.get('bars', []):
                if b['symbol'] in bars:
                    bars[b['symbol']][day] = b
            provenance['cached_sessions'].append({'date': day, 'retrieved_at': cached.get('retrieved_at')})
        if all(day in bars[s] for s in symbols):
            continue
        if cached.get('session') == day:
            continue  # Absent/suspended names stay missing; do not endlessly retry valid day pages.
        if time.monotonic() - started >= budget:
            errors.append('History refresh budget reached; missing symbols remain blocked')
            break
        try:
            raw = psx_historical.fetch_day(day, sess)
            if len(raw) < 100:
                raise ValueError('Expected trading session returned insufficient rows')
            fetched = [{**b, 'date': day, 'source': 'PSX official historical'} for b in raw]
            retrieved = datetime.now(PKT).isoformat()
            write_json(cache / (day + '.json'), {'session': day, 'retrieved_at': retrieved, 'bars': fetched})
            for b in fetched:
                if b['symbol'] in bars:
                    bars[b['symbol']][day] = b
            provenance['fetched_sessions'].append({'date': day, 'retrieved_at': retrieved})
        except (requests.RequestException, ValueError) as exc:
            errors.append(day + ': historical provider failure: ' + str(exc))
        time.sleep(cfg['request_pause_seconds'])
    evidence, exclusions, review_errors = review_evidence(now)
    # Review timestamp is AFTER input collection, so fetched inputs are never future data.
    as_of = datetime.now(PKT) if now.date() == datetime.now(PKT).date() else now
    return {'config': cfg, 'as_of': as_of.isoformat(), 'market_session': cutoff, 'sessions': dates,
            'universe': universe, 'universe_observed_at': universe_capture.get('observed_at'),
            'universe_refresh_failed': universe_capture.get('refresh_failed', False),
            'universe_source': universe_capture.get('source'), 'universe_raw_sha256': universe_capture.get('raw_sha256'),
            'bars': {s: sorted(b.values(), key=lambda x: x['date']) for s, b in bars.items()},
            'actions': actions, 'evidence': evidence, 'exclusions': exclusions, 'companies': companies,
            'financials': {r['ticker']: r for r in originals}, 'errors': errors + review_errors, 'provenance': provenance}


def csv_report(report):
    out = io.StringIO()
    fields = ['rank', 'symbol', 'company', 'price', 'change', 'volume', 'market_cap', 'pe', 'sector',
              'classification', 'score', 'market_session', 'confirmation_reference', 'failure_reference', 'reasons']
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for row in report['rows']:
        values = {**row, 'reasons': '; '.join(row['reasons'])}
        # Spreadsheet exports must treat source text as text, never executable formulas.
        for k, v in values.items():
            if isinstance(v, str) and v.startswith(('=', '+', '-', '@')):
                values[k] = "'" + v
        writer.writerow(values)
    return out.getvalue()


def refresh():
    previous = read_json(ROOT / 'short_horizon_latest.json', {})
    snapshot = collect()
    report = build(snapshot, previous=previous)
    archive = ROOT / 'reports_out/short_horizon_snapshots' / report['input_sha256']
    if not archive.exists():
        write_json(archive / 'inputs.json', snapshot)
        write_json(archive / 'report.json', report)
    write_json(ROOT / 'short_horizon_inputs.json', snapshot)
    write_json(ROOT / 'short_horizon_latest.json', report)
    (ROOT / 'reports_out/short_horizon.csv').write_text(csv_report(report), encoding='utf-8-sig')
    write_json(ROOT / 'short_horizon_status.json', {'ok': True, 'attempted_at': snapshot['as_of'], 'input_sha256': report['input_sha256']})
    return report


def safe_refresh():
    try:
        return refresh()
    except Exception as exc:
        # Old output remains labelled by its own date; failure is separately published.
        write_json(ROOT / 'short_horizon_status.json', {'ok': False, 'attempted_at': datetime.now(PKT).isoformat(), 'error': str(exc)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay', type=Path, help='Recompute frozen inputs without network or database writes')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports_out/short_horizon_replay.json')
    args = parser.parse_args()
    if args.replay:
        result = build(json.loads(args.replay.read_text(encoding='utf-8')))
        write_json(args.output, result)
    else:
        result = safe_refresh()
    print(json.dumps({k: result[k] for k in ('method', 'as_of', 'market_session', 'status', 'eligible_count', 'input_sha256')}))
