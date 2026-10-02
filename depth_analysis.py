"""Read-only intraday quote context. Never changes completed-session signals."""
import csv
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

UTC = timezone.utc
FOLDER = Path(__file__).resolve().parent / 'private_depth'


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('A timestamp must include its time zone')
    return dt.astimezone(UTC)


def number(value):
    n = float(str(value).replace(',', ''))
    if not math.isfinite(n) or n <= 0:
        raise ValueError('A positive price and size are required on both sides')
    return n


def parse(text, filename='capture.csv'):
    """CSV: timestamp,symbol,level,bid_price,bid_size,ask_price,ask_size,source.

    Accept the existing Investify bid_volume/ask_volume format as best quotes.
    A bare clock requires a filename date and means Pakistan time, never today.
    """
    groups, rejected = {}, 0
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        try:
            sym = row['symbol'].strip().upper()
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,19}', sym):
                raise ValueError('Invalid symbol')
            raw = row.get('captured_at') or row.get('timestamp') or row.get('t') or row.get('time')
            if raw and len(raw) <= 8:
                match = re.search(r'\d{4}-\d{2}-\d{2}', filename)
                if not match:
                    raise ValueError('A date is required')
                raw = match.group() + 'T' + raw + '+05:00'
            ts = timestamp(raw)
            source = (row.get('source') or 'Uploaded best quotes').strip()[:80]
            level = int(row.get('level') or 1)
            if not 1 <= level <= 10:
                raise ValueError('Unsupported level')
            values = (number(row['bid_price']), number(row.get('bid_size') or row.get('bid_volume')),
                      number(row['ask_price']), number(row.get('ask_size') or row.get('ask_volume')))
            if values[0] >= values[2]:
                raise ValueError('Crossed or locked quotes are not usable')
            key = (sym, ts, source)
            group = groups.setdefault(key, {'symbol': sym, 'time': ts, 'source': source, 'levels': {}, 'invalid': False})
            if level in group['levels'] and group['levels'][level] != values:
                group['invalid'] = True
            group['levels'][level] = values
            volume = row.get('day_volume') or row.get('volume')
            group['day_volume'] = number(volume) if volume else None
        except (ValueError, KeyError, TypeError, OverflowError):
            rejected += 1
    out = []
    for group in groups.values():
        levels = [v for _, v in sorted(group['levels'].items())]
        if group['invalid'] or sorted(group['levels']) != list(range(1, len(levels) + 1)):
            rejected += 1
            continue
        if any(a[0] <= b[0] or a[2] >= b[2] for a, b in zip(levels, levels[1:])):
            rejected += 1
            continue
        group['levels'] = levels
        group.pop('invalid')
        out.append(group)
    return sorted(out, key=lambda s: s['time']), rejected


def features(snapshot):
    levels = snapshot['levels']
    bid, _, ask, _ = levels[0]
    mid = (bid + ask) / 2
    # Ignore orders far from the market; never infer unseen levels.
    buy = sum(p * q for p, q, _, _ in levels if p >= mid * .99)
    sell = sum(p * q for _, _, p, q in levels if p <= mid * 1.01)
    return mid, (ask - bid) / mid * 100, (buy - sell) / (buy + sell) if buy + sell else 0


def analyze(snapshots, symbol, now=None):
    from session_calendar import is_live
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('Current time must include its time zone')
    base = {'Stock': symbol, 'Read': 'No recent quotes', 'Score effect': 'None — not validated',
            'Note': 'Displayed orders can change or disappear. This is not proof of accumulation.'}
    if not is_live(now):
        return dict(base, Read='Market closed — no live assessment')
    series = [s for s in snapshots if s['symbol'] == symbol and now - timedelta(minutes=5) <= s['time'] <= now]
    if not series:
        return base
    latest = max(series, key=lambda s: s['time'])
    age = (now - latest['time']).total_seconds()
    base.update({'Quote time': latest['time'].isoformat(), 'Age (seconds)': round(age),
                 'Source': latest['source'], 'Data shown': 'Best bid/offer only' if len(latest['levels']) == 1 else f"{len(latest['levels'])} supplied levels"})
    if age > 30:
        return dict(base, Read='Quotes too old — wait for an update')
    # Never mix vendors, levels or multiple samples inside the same five seconds.
    buckets = {}
    for s in sorted(series, key=lambda s: s['time']):
        if s['source'] == latest['source'] and len(s['levels']) == len(latest['levels']):
            buckets[int(s['time'].timestamp()) // 5] = s
    series = sorted(buckets.values(), key=lambda s: s['time'])
    changes = len({(tuple(s['levels']), s.get('day_volume')) for s in series})
    base['Distinct updates'] = changes
    if changes < 12 or (series[-1]['time'] - series[0]['time']).total_seconds() < 60:
        return dict(base, Read='Collect at least 12 changing quotes over one minute')
    if any((b['time'] - a['time']).total_seconds() > 30 for a, b in zip(series, series[1:])):
        return dict(base, Read='Capture has gaps — wait for continuous quotes')
    mids, spreads, pressure = zip(*(features(s) for s in series))
    buy_fraction = sum(p >= .2 for p in pressure) / len(pressure)
    sell_fraction = sum(p <= -.2 for p in pressure) / len(pressure)
    range_pct = (max(mids) - min(mids)) / median(mids) * 100
    base.update({'Typical spread (%)': round(median(spreads), 3),
                 'Price range (%)': round(range_pct, 3),
                 'Buying interest persisted (%)': round(buy_fraction * 100),
                 'Selling interest persisted (%)': round(sell_fraction * 100)})
    if median(spreads) > .5:
        return dict(base, Read='Wide spread — entry and exit may be costly')
    if pressure[-1] < 0 and buy_fraction >= .6:
        return dict(base, Read='Earlier buying interest faded — wait')
    if buy_fraction >= .7 and mids[-1] >= mids[0]:
        read = 'Persistent displayed buying interest; price holding' if range_pct <= .5 else 'Persistent displayed buying interest; price moving'
    elif sell_fraction >= .7:
        read = 'Persistent displayed selling interest'
    else:
        read = 'Mixed orders; no clear confirmation'
    return dict(base, Read=read)


def local_captures():
    snapshots, rejected = [], 0
    if FOLDER.exists():
        for file in sorted(FOLDER.glob('*.csv'), key=lambda p: p.stat().st_mtime)[-10:]:
            if file.stat().st_size > 5_000_000:
                rejected += 1
                continue
            try:
                rows, bad = parse(file.read_text(encoding='utf-8-sig'), file.name)
                snapshots.extend(rows)
                rejected += bad
            except (OSError, UnicodeError, csv.Error):
                rejected += 1
    return snapshots, rejected


def show(st):
    with st.expander('Intraday best bid / offer — separate from daily calls'):
        st.caption('KTrade/Investify best quotes show one price on each side, not the full order book. No paid feed is connected. Capture CSVs can be checked here; uploads stay in this session and are not committed to GitHub.')
        files = st.file_uploader('Load your quote captures', type=['csv'], accept_multiple_files=True, key='depth_csv')
        snapshots, rejected = local_captures()
        for file in files or []:
            try:
                if file.size > 5_000_000:
                    raise ValueError('Capture too large; use a shorter window')
                rows, bad = parse(file.getvalue().decode('utf-8-sig'), file.name)
                snapshots.extend(rows)
                rejected += bad
            except (UnicodeError, csv.Error, ValueError) as exc:
                st.warning(str(exc))
        if rejected:
            st.warning(f'{rejected} rows or files were unusable. Empty, crossed, missing-time and invalid quotes cannot confirm a trade.')
        symbols = sorted({s['symbol'] for s in snapshots})
        if not symbols:
            st.info('No usable capture loaded. Open your logged-in quote page during market hours, record best quotes, then load the downloaded CSV. See the free capture guide below.')
        else:
            symbol = st.selectbox('Stock for intraday review', symbols, key='depth_symbol')
            result = analyze(snapshots, symbol)
            st.write(result['Read'])
            st.dataframe([result], hide_index=True)
        st.caption('A narrow price range is only consolidation-like behavior. Quote sizes alone cannot establish actual buying, absorption, or accumulation. Research thresholds are not a calibrated probability or a Buy signal.')
        st.markdown('[Free capture guide](https://github.com/fahadalipersonal313-ai/psx-engine/blob/main/docs/TRADING_AND_DEPTH.md)')


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description='Monitor local quote CSVs without broker login or orders')
    parser.add_argument('symbol')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    previous = None
    while True:
        rows, bad = local_captures()
        result = analyze(rows, args.symbol.upper())
        message = json.dumps(dict(result, rejected=bad), ensure_ascii=False)
        if message != previous:
            print(message, flush=True)
            previous = message
        if not args.watch:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
