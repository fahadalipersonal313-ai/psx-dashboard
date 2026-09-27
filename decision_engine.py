"""Pure completed-session decision path shared by live ingestion and replay."""
import hashlib
import json
from copy import deepcopy
import pandas as pd
import config
import technical_analyzer
import scoring_engine
import signal_generator
import risk_manager
import market_regime
from data_quality import bar_error, finite, source_priority


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def contract():
    # Explicit v6 decision inputs: display/replay settings cannot retire live
    # decisions. Older records retain their frozen contract and hash.
    excluded = ('PATH', 'DIR', 'EMAIL', 'SMTP', 'PASSWORD', 'TOKEN', 'SECRET', 'KEY')
    values = {}
    inputs = set('''STRATEGY_VERSION FEATURE_HISTORY_LIMIT MIN_STRATEGY_HISTORY
        PURE_TECHNICAL RS_LOOKBACKS RS_WEIGHTS RS_POINTS BENCHMARK_INDEX REGIME_EMA_SPAN
        EXECUTION RISK MIN_TURNOVER_PKR MIN_OHLC_BARS_FOR_TRUE PULLBACK_EMA_SPAN
        CONCENTRATION_VETO_ENABLED POOR_RR_VETO_ENABLED BUY_MIN_CMF CHASE_GUARD_ENABLED
        EARLY_WATCH_ENABLED EARLY_WATCH_MIN_CMF EARLY_WATCH_MIN_RS EARLY_WATCH_SCORE_BAND
        EARNINGS_BLACKOUT_DAYS REGIME_GATE_ENABLED RS_LAGGARD_VETO SIGNAL_THRESHOLDS
        HYSTERESIS_BAND WEIGHTS NEWS_SCORE_ADJUST_MAX SECTOR_NEWS_SCORE_ADJUST_MAX
        CIRCUIT_LIMIT_PCT EXCHANGE_HOLIDAYS SESSION_OVERRIDES PUBLICATION_DELAY_MINUTES
        STOCKS KMI30_VERIFIED KMIALLSHR_VERIFIED LOWER_PRICE_VERIFIED OTHER_COMPLIANT
        UNIVERSE_KNOWN_FROM'''.split())
    for key, value in vars(config).items():
        if key not in inputs:
            continue
        if not key.isupper() or any(word in key for word in excluded):
            continue
        if isinstance(value, (set, frozenset)):
            value = sorted(value)
        try:
            canonical(value)
        except (TypeError, ValueError):
            continue
        values[key] = deepcopy(value)
    return values


def _history(rows, cutoff, ohlc):
    rows = rows.to_dict('records') if isinstance(rows, pd.DataFrame) else list(rows or [])
    selected = []
    for original in rows:
        row = dict(original)
        dt = pd.Timestamp(row.get('date'))
        if pd.isna(dt):
            raise ValueError('Missing session date')
        if dt.tzinfo is not None:
            dt = dt.tz_convert('Asia/Karachi').tz_localize(None)
        day = dt.date().isoformat()
        if day > cutoff:
            continue
        row['date'] = day
        selected.append(row)
    selected.sort(key=lambda b: b['date'])
    selected = selected[-config.FEATURE_HISTORY_LIMIT:]
    if len({b['date'] for b in selected}) != len(selected):
        raise ValueError('Duplicate sessions')
    for row in selected:
        if ohlc:
            error = bar_error(row)
            if error:
                raise ValueError(error + ' on ' + row['date'])
            if source_priority(row.get('source')) < 3:
                raise ValueError('Finalized official OHLC required on ' + row['date'])
        elif not finite(row.get('close'), True):
            raise ValueError('Invalid benchmark close')
    return selected


def decide(symbol, bars, benchmark, cutoff, eligible=True, previous=None, actions=None):
    """Inputs are supplied explicitly; no network, database or wall-clock reads.

    cutoff is the last completed exchange session, not an intraday quote date.
    Full feature history is retained independently of replay evaluation dates.
    """
    params = contract()
    neutral = {'score': 50.0, 'low_confidence': False, 'notes': [], 'flags': [],
               'explanation': 'Optional context excluded from technical strategy',
               'verdict': 'Not used', 'mentions': 0}
    out = {'symbol': symbol, 'decision_session': cutoff,
           'strategy_version': config.STRATEGY_VERSION, 'config_hash': digest(params),
           'config': params, 'technical': {'score': None, 'classification': 'No data'},
           'scoring': {'final_score': None, 'confidence': 0, 'data_quality': 'unavailable',
                       'history_note': 'Required data unavailable; no entry',
                       'breakdown': {'technical': None, 'macro_news': None, 'sentiment': None}},
           'risk': {'risk_level': 'High', 'warnings': [], 'vetoes': [], 'position_sizing': None},
           'signal': {'signal': 'No data', 'confidence': 0, 'reasons': [], 'raw_qualified': False},
           'macro': dict(neutral), 'sentiment': dict(neutral), 'fundamentals': dict(neutral),
           'relative_strength': None, 'regime': {'regime': 'unknown'}}
    try:
        if not config.PURE_TECHNICAL:
            raise ValueError('Versioned strategy requires PURE_TECHNICAL')
        pd.Timestamp(cutoff)  # reject malformed dates
        stock = _history(bars, cutoff, True)
        index = _history(benchmark, cutoff, False)
        minimum = config.MIN_STRATEGY_HISTORY
        if len(stock) < minimum or len(index) < max(minimum, max(config.RS_LOOKBACKS.values()) + 1):
            raise ValueError(f'At least {minimum} stock and benchmark sessions required')
        if stock[-1]['date'] != cutoff or index[-1]['date'] != cutoff:
            raise ValueError('Required completed session unavailable')
        recent_index = [r['date'] for r in index][-minimum:]
        if [r['date'] for r in stock][-minimum:] != recent_index:
            raise ValueError('Stock and benchmark session coverage differs')
        import corporate_actions
        adjusted = corporate_actions.verified_bars(stock, actions or [], cutoff)
        for before, after in zip(adjusted, adjusted[1:]):
            if abs(float(after['close']) / float(before['close']) - 1) > .105:
                raise ValueError('Unresolved price discontinuity on ' + after['date'])
        snapshot = {'bars': stock, 'benchmark': index, 'cutoff': cutoff,
                    'eligible': bool(eligible), 'previous': previous, 'actions': actions or [], 'config': params}
        out['snapshot'] = snapshot
        out['snapshot_hash'] = digest(snapshot)
        df, ix = pd.DataFrame(adjusted), pd.DataFrame(index)
        rs = market_regime.relative_strength(df, ix)
        if rs is None:
            raise ValueError('Aligned relative strength unavailable')
        regime = market_regime.assess_regime(ix)
        tech = technical_analyzer.analyze(symbol, df, {'price': adjusted[-1]['close'], 'volume': adjusted[-1]['volume']}, rs['rs_score'], adjusted)
        from statistics import median
        tech['median_turnover_pkr'] = median(float(b['close']) * float(b['volume']) for b in stock[-20:])
        if not finite(tech.get('cmf')) or not tech.get('atr_is_true') or not finite(tech.get('adx_proxy')):
            raise ValueError('Required OHLC indicators unavailable')
        score = scoring_engine.compute(symbol, neutral, neutral, tech, neutral)
        risk = risk_manager.assess(symbol, tech, neutral, neutral, regime=regime['regime'], regime_pct_above=regime['pct_above'])
        # Only the immediately prior exchange session can confirm. Repeated
        # polls of the same session never become additional confirmations.
        prev = previous if previous and previous.get('decision_session') == index[-2]['date'] and previous.get('config_hash') == out['config_hash'] else {}
        signal = signal_generator.generate(symbol, score['final_score'], score['confidence'], risk,
                    {'eligible_for_ranking': eligible}, tech, regime=regime['regime'],
                    regime_pct_above=regime['pct_above'], prev_signal=prev.get('signal'),
                    previous_qualified=bool(prev.get('raw_qualified')))
        out.update(technical=tech, scoring=score, risk=risk, signal=signal, relative_strength=rs, regime=regime)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        out['signal']['reasons'] = [str(exc)]
    return out


def state(decision):
    return {'decision_session': decision['decision_session'], 'config_hash': decision['config_hash'],
            'signal': decision['signal']['signal'], 'raw_qualified': bool(decision['signal'].get('raw_qualified'))}
