"""Versioned, completed-session research view; never an order or swing-signal input."""
import hashlib
import json
import math
import re
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd
from corporate_actions import valid_action, verified_bars
from data_quality import bar_error, source_priority
from session_calendar import PKT, last_completed
from technical_analyzer import rsi, macd, true_atr_adx, chaikin_money_flow

ROOT = Path(__file__).resolve().parent


def number(value):
    """PKR/share, whole shares, percentage points; B/M/K explicitly expanded."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(',', '').replace('\u2212', '-')
    match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([KMB%]?)', text, re.I)
    if not match:
        return None
    result = float(match[1]) * {'K': 1e3, 'M': 1e6, 'B': 1e9}.get(match[2].upper(), 1)
    return result if math.isfinite(result) else None


def stamp(value):
    """Date-only evidence becomes available at end of that PKT day, never midnight."""
    if not value:
        raise ValueError('missing timestamp')
    text = str(value)
    if len(text) == 10:
        return datetime.combine(datetime.fromisoformat(text).date(), time.max, PKT)
    dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp needs timezone')
    return dt.astimezone(PKT)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def config():
    cfg = json.loads((ROOT / 'short_horizon_config.json').read_text())
    if cfg['sessions'] < 42 or cfg['limit'] < 1 or any(v < 0 for v in cfg['weights'].values()):
        raise ValueError('invalid research settings')
    if sum(cfg['weights'].values()) != 100:
        raise ValueError('research weights must total 100')
    return cfg


def universe_rows(rows):
    out = []
    seen = set()
    for raw in rows:
        parts = raw['symbol'].upper().split()
        symbol = parts[0] if parts else ''
        if not re.fullmatch(r'[A-Z0-9]+', symbol) or symbol in seen:
            raise ValueError('invalid or duplicate exchange identity: ' + symbol)
        seen.add(symbol)
        # Never collapse ASLPS into ASL, or DCCL into DCL.
        indices = re.findall(r'[A-Z][A-Z0-9]*', raw['listed'].upper())
        out.append({'symbol': symbol, 'member': 'KMIALLSHR' in indices,
                    'exchange_nc': 'NC' in parts[1:], 'membership_effective_date': None,
                    'sector': raw.get('sector'), 'market_cap': number(raw.get('marketcap')),
                    'pe': number(raw.get('peratio')), 'average_volume': number(raw.get('volume30avg')),
                    'quote_price': number(raw.get('close'))})
    return out


def financial_context(record):
    if not record:
        return {'status': 'Not available; no earnings score'}
    annual, ttm = number(record.get('annualGrowth')), number(record.get('growth'))
    return {'status': 'Historical supporting context only; periods differ',
            'captured_date': '2026-09-20', 'market_session': record.get('quoteDate'),
            'annual_period': record.get('annualYear'), 'annual_growth_percent': annual,
            'ttm_growth_percent': ttm, 'ttm_period_end': None,
            'opposite_directions': annual is not None and ttm is not None and annual * ttm < 0,
            'source': record.get('url'), 'quality': 'Preserved source capture; not refreshed financial statements'}


def news_for(symbol, evidence, as_of, cfg):
    accepted, rejected = [], []
    for item in evidence:
        if item.get('symbol') != symbol:
            continue
        reason = None
        try:
            published, known, expires = (stamp(item[k]) for k in ('published_at', 'known_at', 'expires_at'))
            if published > as_of or known > as_of:
                reason = 'Not yet known at this review time'
            elif expires < as_of or (as_of - published).total_seconds() > cfg['news_max_age_hours'] * 3600:
                reason = 'Expired; review again'
            elif not item.get('source') or not item.get('rationale'):
                reason = 'Missing source or rationale'
        except (KeyError, TypeError, ValueError):
            reason = 'Publication, review or expiry time is missing/invalid'
        if reason:
            rejected.append({**item, 'unusable_reason': reason})
        else:
            accepted.append(item)
    # Context/sector/headline-only items never certify a stock-specific catalyst.
    verified = [x for x in accepted if x.get('quality') == 'primary-full'
                and x.get('linkage') == 'direct' and x.get('assessment') in ('positive', 'negative', 'neutral')]
    return {'status': 'Reviewed direct evidence' if verified else 'No current direct catalyst verified',
            'items': accepted, 'not_used': rejected, 'verified': verified}


def metrics(bars):
    close = pd.Series([b['close'] for b in bars], dtype=float)
    volume = pd.Series([b['volume'] for b in bars], dtype=float)
    ema10, ema20, ema40 = [float(close.ewm(span=p, adjust=False).mean().iloc[-1]) for p in (10, 20, 40)]
    _, _, hist = macd(close)
    strength = true_atr_adx(bars)
    atr = number(strength['atr'])
    prior_volume = float(volume.iloc[-21:-1].mean())
    return {'price': float(close.iloc[-1]), 'change': float((close.iloc[-1] / close.iloc[-2] - 1) * 100),
            'volume': float(volume.iloc[-1]), 'ema10': ema10, 'ema20': ema20, 'ema40': ema40,
            'rsi': number(float(rsi(close).iloc[-1])), 'macd_histogram': number(float(hist.iloc[-1])),
            'return_10_sessions': float((close.iloc[-1] / close.iloc[-11] - 1) * 100),
            'relative_volume': float(volume.iloc[-1] / prior_volume) if prior_volume > 0 else None,
            'median_turnover': float((close * volume).iloc[-20:].median()),
            'atr': atr, 'adx': number(strength['adx']),
            'atr_percent': atr / close.iloc[-1] * 100 if atr and atr > 0 else None,
            'extension_atr': (close.iloc[-1] - ema20) / atr if atr and atr > 0 else None,
            'money_flow': chaikin_money_flow(bars),
            'confirmation_reference': max(b['high'] for b in bars[-5:]),
            'failure_reference': min(b['low'] for b in bars[-5:])}


def classify(m, news, cfg):
    required = ('price', 'ema10', 'ema20', 'ema40', 'rsi', 'macd_histogram', 'return_10_sessions',
                'relative_volume', 'atr_percent', 'extension_atr', 'adx', 'money_flow', 'median_turnover', 'volume')
    if any(number(m.get(k)) is None for k in required):
        return 'Data incomplete', None, {}, ['Required indicator unavailable']
    trend = (m['price'] > m['ema10'] > m['ema20'] > m['ema40'])
    momentum = m['macd_histogram'] > 0 and m['return_10_sessions'] > 0 and 50 <= m['rsi'] <= cfg['maximum_rsi']
    participation = m['relative_volume'] >= cfg['minimum_relative_volume'] and m['money_flow'] > 0
    strength = m['adx'] >= cfg['minimum_adx']
    # Four explicit dimensions; provider summary Buy/Sell ratings are not scored again.
    fractions = {'trend': sum((m['price'] > m['ema10'], m['ema10'] > m['ema20'], m['ema20'] > m['ema40'])) / 3,
                 'momentum': sum((m['macd_histogram'] > 0, m['return_10_sessions'] > 0,
                                   50 <= m['rsi'] <= cfg['maximum_rsi'])) / 3,
                 'participation': sum((m['relative_volume'] >= cfg['minimum_relative_volume'], m['money_flow'] > 0)) / 2,
                 'strength': float(strength)}
    components = {k: round(v * cfg['weights'][k], 2) for k, v in fractions.items()}
    score = round(min(100, max(0, sum(components.values()))), 2)
    if m['volume'] < cfg['minimum_volume'] or m['median_turnover'] < cfg['minimum_median_turnover']:
        return 'Liquidity too low', score, components, ['Recent trading value/volume below configured minimum']
    if abs(m['change']) >= 9.7:
        return 'Confirmation required', score, components, ['Large daily move; check exchange limit and ability to trade']
    if m['rsi'] > cfg['maximum_rsi'] or m['extension_atr'] > cfg['maximum_extension_atr']:
        return 'Overextended', score, components, ['Price has run ahead; wait for a new base']
    if m['price'] < m['ema20'] or m['macd_histogram'] <= 0:
        return 'Reversal required', score, components, ['Price trend or momentum has not turned upward']
    reasons = []
    for ok, label in ((trend, 'Moving averages not aligned upward'), (momentum, 'Momentum not confirmed'),
                      (participation, 'Volume/closing-pressure confirmation missing'), (strength, 'Trend strength below minimum')):
        if not ok:
            reasons.append(label)
    if m['atr_percent'] > cfg['maximum_atr_percent']:
        reasons.append('Daily price swings exceed risk threshold')
    if not news['verified']:
        reasons.append('Stock-specific news/event review still needed')
    elif not any(x.get('event_risk') in ('clear', 'resolved') for x in news['verified']):
        reasons.append('Event status still needs confirmation')
    if any(x.get('assessment') == 'negative' or x.get('event_risk') in ('pending', 'high', 'unresolved') for x in news['items']):
        reasons.append('Negative evidence or unresolved event needs review')
    return ('Confirmation required' if reasons else 'Momentum candidate'), score, components, reasons or ['Trend, momentum and participation aligned; conditional research candidate']


def build(snapshot, cfg=None, previous=None):
    cfg = cfg or snapshot.get('config') or config()
    as_of, cutoff = stamp(snapshot['as_of']), snapshot['market_session']
    if cutoff > last_completed(as_of):
        raise ValueError('Incomplete/future session cannot be scored')
    dates = snapshot['sessions'][-cfg['sessions']:]
    if len(dates) != cfg['sessions'] or dates != sorted(set(dates)) or dates[-1] != cutoff:
        raise ValueError('Expected complete, ordered session calendar')
    universe = snapshot['universe']
    global_errors = list(snapshot.get('errors', []))
    try:
        observed = stamp(snapshot['universe_observed_at'])
        universe_ok = (not snapshot.get('universe_refresh_failed') and
                       0 <= (as_of - observed).total_seconds() <= cfg['universe_max_age_hours'] * 3600)
    except (ValueError, KeyError):
        universe_ok = False
    if not universe_ok:
        global_errors.append('Official membership capture stale or not yet available')
    rows, excluded = [], []
    for u in universe:
        sym = u['symbol']
        why = []
        if not u.get('member'):
            why.append('Not observed in KMI All Share')
        if u.get('exchange_nc'):
            why.append('Exchange NC flag (not a Shariah ruling)')
        effective = u.get('membership_effective_date')
        if effective and effective > as_of.date().isoformat():
            why.append('Membership not yet effective')
        for key, setting in (('quote_price', 'minimum_price'), ('market_cap', 'minimum_market_cap'), ('average_volume', 'minimum_average_volume')):
            val = number(u.get(key))
            if val is None or val < cfg[setting]:
                why.append(key + ' missing/below minimum')
        override = snapshot.get('exclusions', {}).get(sym)
        if override:
            try:
                if stamp(override['known_at']) <= as_of:
                    why.append('Analyst exclusion: ' + override['reason'] + ('; review overdue' if stamp(override['review_by']) < as_of else ''))
            except (KeyError, ValueError):
                why.append('Invalid analyst exclusion requires review')
        if why:
            excluded.append({'symbol': sym, 'reasons': why, 'membership': u})
            continue
        row = {**u, 'company': snapshot.get('companies', {}).get(sym), 'classification': 'Data incomplete',
               'score': None, 'components': {}, 'reasons': [], 'market_session': cutoff,
               'overview_observed_at': snapshot.get('universe_observed_at'),
               'overview_source': snapshot.get('universe_source'),
               'pe_period': 'PSX displayed P/E; denominator period not supplied',
               'financials': financial_context(snapshot.get('financials', {}).get(sym))}
        news = news_for(sym, snapshot.get('evidence', []), as_of, cfg)
        row['news'] = news
        bars = sorted((b for b in snapshot.get('bars', {}).get(sym, []) if b['date'] <= cutoff), key=lambda b: b['date'])
        bars = [b for b in bars if b['date'] >= dates[0]]
        errors = []
        if not universe_ok:
            errors.append('Membership freshness not confirmed')
        if [b['date'] for b in bars] != dates:
            errors.append('Missing/duplicate sessions in required history')
        if any(bar_error(b) or source_priority(b.get('source', '')) < 3 for b in bars):
            errors.append('Invalid OHLC or non-official historical source')
        actions = [a for a in snapshot.get('actions', []) if a.get('symbol') == sym and dates[0] <= a.get('ex_date', '') <= cutoff]
        if any(not valid_action(a) or a.get('known_at', '') > cutoff for a in actions):
            errors.append('Corporate action terms not verified at cutoff')
        if not errors:
            adjusted = verified_bars(bars, actions, cutoff)
            if any(abs(b['close'] / a['close'] - 1) > .105 for a, b in zip(adjusted, adjusted[1:])):
                errors.append('Unexplained price jump; action/price verification needed')
            else:
                m = metrics(adjusted)
                row.update(m)
                if m['price'] < cfg['minimum_price']:
                    errors.append('Completed-session price below minimum')
                else:
                    label, score, components, reasons = classify(m, news, cfg)
                    row.update(classification=label, score=score, components=components, reasons=reasons)
        if errors:
            row.update(classification='Data incomplete', reasons=errors, score=None)
        row['confirmation'] = 'Next session: sustained trade above the five-session high with adequate volume; check live spread and event outcome.'
        row['failure'] = 'A fall below the five-session low or loss of upward trend requires a fresh review; reference is not a guaranteed stop.'
        rows.append(row)
    # All eligible names receive the same calculations before display selection.
    rows.sort(key=lambda r: (r['classification'] in ('Data incomplete', 'Liquidity too low'),
                             r['score'] is None, -(r['score'] or 0), -(r.get('median_turnover') or 0), r['symbol']))
    for rank, row in enumerate(rows, 1):
        row['rank'] = rank
    selected = rows[:cfg['limit']]
    old = {r['symbol']: r for r in (previous or {}).get('rows', [])}
    new = {r['symbol']: r for r in selected}
    changes = {'entries': sorted(new.keys() - old.keys()), 'exits': sorted(old.keys() - new.keys()), 'changed': []}
    for sym in sorted(new.keys() & old.keys()):
        a, b = old[sym], new[sym]
        fields = [k for k in ('rank', 'classification', 'news', 'reasons') if a.get(k) != b.get(k)]
        if fields:
            changes['changed'].append({'symbol': sym, 'fields': fields, 'previous_rank': a.get('rank'),
                                       'rank': b['rank'], 'previous_classification': a.get('classification'), 'classification': b['classification']})
    return {'method': cfg['method'], 'config': cfg, 'input_sha256': digest(snapshot),
            'previous_input_sha256': (previous or {}).get('input_sha256'),
            'as_of': snapshot['as_of'], 'market_session': cutoff, 'timezone': 'Asia/Karachi',
            'timeframe': 'Daily completed sessions; 1–5-session research horizon', 'validated': False,
            'status': 'Partial coverage' if global_errors or any(r['score'] is None for r in rows) else 'Research snapshot',
            'warnings': global_errors, 'universe_count': len(universe), 'eligible_count': len(rows),
            'rows': selected, 'all_eligible': rows, 'excluded': excluded, 'changes': changes,
            'limitations': ['Heuristic scores are not probabilities. No performance claim or automatic trade.',
                           'Observed index membership has no verified effective-date history; not suitable for historical membership backtests.',
                           'News is supporting evidence, never ticker-specific bonus points. Unreviewed is not Neutral.',
                           'Daily bars cannot verify intraday breakout fills, spread, depth or current momentum.',
                           'Calendar uses existing configured exchange holidays; missing sessions block affected rows.']}
