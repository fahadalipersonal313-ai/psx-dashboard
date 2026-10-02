"""Read-only candle research. Fixed rules; no engine calls, tuning or orders."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SPEC = {
    'version': 'psx-candle-diagnostic-v2', 'context_sessions': 40,
    'patterns': ['bullish_engulfing', 'hammer_after_decline', 'volume_breakout', 'trend_pullback'],
    'horizon_sessions': 5, 'entry': 'next observed session open',
    'exit': 'fifth session close', 'cost_scenarios_round_trip_bps': [0, 70, 100],
    'train_end': '2024-12-31', 'validation_end': '2025-12-31',
    'diagnostic_test_start': '2026-01-01',
    'limits': 'Current selected universe, raw-price diagnostic; not untouched holdout or engine backtest.',
}


def features(frame, sessions=None):
    """All decision features depend only on the current candle and preceding candles."""
    f = frame.copy()
    good = (np.isfinite(f[['open', 'high', 'low', 'close', 'volume']]).all(axis=1)
            & (f[['open', 'high', 'low', 'close']] > 0).all(axis=1)
            & (f.volume >= 0) & (f.high >= f.low)
            & f.open.between(f.low, f.high) & f.close.between(f.low, f.high))
    discontinuity = f.close.pct_change(fill_method=None).abs() > .105
    usable = good & ~discontinuity
    clean_context = usable.rolling(41, min_periods=41).sum().eq(41)
    if sessions is not None:
        position = pd.Series({d: i for i, d in enumerate(sessions)})
        clean_context &= f.date.map(position).diff(40).eq(40)
    body = (f.close-f.open).abs()
    lower = f[['open', 'close']].min(axis=1)-f.low
    upper = f.high-f[['open', 'close']].max(axis=1)
    sma20, sma40 = f.close.rolling(20).mean(), f.close.rolling(40).mean()
    prior = f.shift(1)
    liquid = (prior.close*prior.volume).rolling(20).median().ge(5_000_000)
    patterns = pd.DataFrame(index=f.index)
    patterns['bullish_engulfing'] = ((prior.close < prior.open) & (f.close > f.open)
        & (f.open <= prior.close) & (f.close >= prior.open) & (body > 0))
    patterns['hammer_after_decline'] = ((lower >= 2*body) & (upper <= body)
        & (body > 0) & ((f.close-f.low)/(f.high-f.low).replace(0, np.nan) >= .65)
        & (prior.close < f.close.shift(11)))
    patterns['volume_breakout'] = ((f.close > prior.high.rolling(20).max())
        & (f.volume > 1.5*prior.volume.rolling(20).median()))
    patterns['trend_pullback'] = ((sma20 > sma40) & (f.low <= sma20)
        & (f.close > sma20) & (f.close > prior.high))
    return patterns.mul(clean_context & liquid, axis=0).astype(bool), usable


def partition(day):
    if day <= SPEC['train_end']: return 'train'
    if day <= SPEC['validation_end']: return 'validation'
    return 'diagnostic_test'


def sample(frame, flags, usable, sessions):
    out = []
    positions = {d: i for i, d in enumerate(sessions)}
    for pattern in flags.columns:
        occupied_until = -1
        for i in range(len(frame)):
            if not flags[pattern].iloc[i] or i <= occupied_until: continue
            row = frame.iloc[i]
            record = {'symbol': row.symbol, 'pattern': pattern, 'decision_date': row.date,
                      'partition': partition(row.date), 'status': 'unresolved', 'gross_pct': None}
            end = i+SPEC['horizon_sessions']
            occupied_until = end  # setup identity cannot depend on available future outcomes
            if end >= len(frame):
                record['reason'] = 'Future horizon incomplete'
            else:
                entry, exit_row = frame.iloc[i+1], frame.iloc[end]
                record.update(entry_date=entry.date, exit_date=exit_row.date)
                # Purge windows crossing chronological split boundaries.
                if partition(row.date) != partition(exit_row.date):
                    record.update(status='purged', reason='Outcome crosses partition boundary')
                elif not usable.iloc[i+1:end+1].all():
                    record['reason'] = 'Invalid candle or unexplained price discontinuity'
                elif positions[exit_row.date]-positions[row.date] != SPEC['horizon_sessions']:
                    record['reason'] = 'Missing universe session; no synthetic candle'
                elif abs(entry.open/row.close-1) >= .097:
                    record.update(status='unfilled', reason='Opening move near circuit; queue unverified')
                elif exit_row.volume <= 0 or abs(exit_row.close/frame.iloc[end-1].close-1) >= .097:
                    record['reason'] = 'Exit volume or circuit availability unverified'
                else:
                    record.update(status='priced_scenario', gross_pct=float(100*(exit_row.close/entry.open-1)))
            out.append(record)
    return out


def summarize(rows):
    summaries = []
    for part in ['train', 'validation', 'diagnostic_test']:
        for pattern in SPEC['patterns']:
            group = [r for r in rows if r['partition']==part and r['pattern']==pattern]
            priced = [r for r in group if r['status']=='priced_scenario']
            summary = {'partition': part, 'pattern': pattern, 'observations': len(group),
                       'priced': len(priced), 'unresolved': sum(r['status']=='unresolved' for r in group),
                       'unfilled': sum(r['status']=='unfilled' for r in group),
                       'purged': sum(r['status']=='purged' for r in group), 'cost_scenarios': []}
            eligible = len(group)-summary['purged']
            summary['priced_coverage_fraction'] = len(priced)/eligible if eligible else None
            for cost in SPEC['cost_scenarios_round_trip_bps']:
                returns = np.array([r['gross_pct'] for r in priced])-cost/100
                # Equal-weight decision-date averages: overlapping stocks are not independent trades.
                dated = {}
                for r, ret in zip(priced, returns): dated.setdefault(r['decision_date'], []).append(ret)
                day_returns = [float(np.mean(v)) for v in dated.values()]
                summary['cost_scenarios'].append({'round_trip_bps': cost,
                    'positive_fraction': float(np.mean(returns>0)) if len(returns) else None,
                    'mean_pct': float(np.mean(returns)) if len(returns) else None,
                    'median_pct': float(np.median(returns)) if len(returns) else None,
                    'worst_pct': float(np.min(returns)) if len(returns) else None,
                    'decision_dates': len(dated),
                    'equal_date_mean_pct': float(np.mean(day_returns)) if day_returns else None})
            summaries.append(summary)
    return summaries


def gallery(frames, destination):
    import plotly.graph_objects as go
    from plotly.offline import get_plotlyjs
    charts = []
    for symbol, frame in frames:
        f = frame.tail(120)
        fig = go.Figure(go.Candlestick(x=f.date, open=f.open, high=f.high, low=f.low,
                                      close=f.close, name='Stored raw candles'))
        fig.update_layout(template='plotly_dark', title=symbol+' · latest 120 stored sessions',
                          height=420, xaxis_rangeslider_visible=False)
        charts.append('<details><summary>'+symbol+'</summary>'+fig.to_html(full_html=False, include_plotlyjs=False)+'</details>')
    destination.write_text('<!doctype html><meta charset="utf-8"><title>PSX candle evidence</title>'
        '<style>body{background:#111827;color:#eee;font:15px Segoe UI;margin:25px}summary{padding:12px;cursor:pointer}</style>'
        '<h1>60-stock candle evidence</h1><p>All stored candles were scanned numerically. Charts show the latest 120 sessions. '
        'Raw prices can contain corporate-action gaps; patterns do not establish profitability.</p><script>'+get_plotlyjs()+'</script>'
        +''.join(charts), encoding='utf-8')


def run(repo, database, destination, make_gallery=True, data_commit=None):
    sys.path.insert(0, str(repo))
    import config
    destination.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as con:
        params=','.join('?'*len(config.STOCKS))
        frame=pd.read_sql_query('select * from daily_ohlc where symbol in ('+params+') order by symbol,date', con, params=config.STOCKS)
    dataset_hash=hashlib.sha256(frame.to_csv(index=False, lineterminator='\n').encode()).hexdigest()
    code_commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    manifest={**SPEC, 'dataset_hash':dataset_hash, 'symbols':config.STOCKS, 'bars':len(frame),
              'runner_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'code_commit':code_commit, 'data_commit':data_commit,
              'completed_data_cutoff':str(frame.date.max()),
              'session_reference':'Union of stored dates; not an independently verified exchange calendar'}
    manifest_path=destination/'specification.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8'))!=manifest:
        raise ValueError('Changed dataset/specification needs a new dated output directory')
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    sessions=sorted(frame.date.unique())
    coverage, rows, frames=[], [], []
    for symbol in config.STOCKS:
        f=frame.loc[frame.symbol==symbol].reset_index(drop=True)
        if f.empty:
            coverage.append({'symbol':symbol,'bars':0});continue
        flags, usable=features(f, sessions)
        rows.extend(sample(f, flags, usable, sessions))
        coverage.append({'symbol':symbol, 'bars':len(f), 'first':f.date.min(), 'last':f.date.max(),
                         'unusable':int((~usable).sum()), 'latest_patterns':[p for p in flags if flags[p].iloc[-1]],
                         'sources':sorted(f.source.dropna().unique())})
        frames.append((symbol,f))
    result={'specification':manifest, 'coverage':coverage, 'pattern_diagnostics':summarize(rows),
            'limits': ['Current-universe survivorship/selection bias; historical membership not reconstructed.',
                      'Session continuity uses union of observed dates; dates absent for all stocks cannot be detected.',
                      'Priced outcomes are a conditional subset; future-dependent exclusions can bias return statistics.',
                      'Raw prices; discontinuity checks do not establish complete action or dividend reconciliation.',
                      'Next-session opens and fifth-session closes are price scenarios, not guaranteed fills.',
                      'Cross-stock and cross-pattern overlap remains; no independent-trade confidence interval.',
                      '2026 was already available during research; diagnostic test is not an untouched holdout.',
                      'No training or probability calibration; no strategy is selected or changed by this script.']}
    (destination/'evidence.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    (destination/'observations.json').write_text(json.dumps(rows, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    if make_gallery: gallery(frames,destination/'charts.html')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-gallery', action='store_true')
    parser.add_argument('--data-commit')
    args=parser.parse_args()
    result=run(args.repo,args.database,args.output,not args.no_gallery,args.data_commit)
    print(json.dumps({'stocks':len(result['coverage']), 'bars':result['specification']['bars'],
                      'output':str(args.output), 'confidence':'Not measured; no guaranteed-profit calls'}, indent=2))
