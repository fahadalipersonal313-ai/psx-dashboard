"""Daily-bar target-before-stop evaluation; no invented portfolio equity curve."""
from collections import Counter
from bisect import bisect_right
from copy import deepcopy
import json
import config
import decision_engine
from data_quality import bar_error, valid_levels, source_priority

RESOLVED = ('target', 'stop', 'expired')


def opportunity(decision):
    tech = decision['technical']
    sizing = decision.get('risk', {}).get('position_sizing') or {}
    modern = decision['config']['EXECUTION'].get('capacity_basis') == 'decision_session'
    return {'symbol': decision['symbol'], 'session': decision['decision_session'],
            'reference_entry': tech['price'], 'stop': tech['stop_loss'],
            'target': tech['target1'], 'target2': tech.get('target2'),
            'strategy_version': decision['strategy_version'], 'config_hash': decision['config_hash'],
            'snapshot_hash': decision['snapshot_hash'], 'execution': deepcopy(decision['config']['EXECUTION']),
            'quantity': sizing.get('suggested_shares', 0) if modern else 1,
            'prior_avg_volume': tech.get('avg_volume'),
            'capital_assumed_pkr': sizing.get('capital_assumed_pkr'),
            'probability': None, 'probability_label': 'uncalibrated'}


def resolve(item, bars, sessions, as_of=None, actions=None):
    """One next-session opening order, expires if not fillable that session.

    sessions comes from dated benchmark observations, never stock row positions.
    Missing/locked bars after entry retain unresolved exposure. Same-bar barrier
    ambiguity is stop-first. Fees and slippage are frozen with the opportunity.
    """
    policy = item['execution']
    from data_quality import finite
    if any(not finite(policy.get(k)) or policy[k] < 0 for k in ('holding_sessions','slippage_bps','fee_bps_per_side','entry_gap_limit','max_volume_participation')) or policy['max_volume_participation'] > 1 or policy['slippage_bps'] >= 10000 or policy['fee_bps_per_side'] >= 10000:
        return {'status':'invalid', 'reason':'Invalid frozen execution assumptions'}
    horizon = int(policy['holding_sessions'])
    modern = policy.get('capacity_basis') == 'decision_session'
    if modern and (not finite(item.get('quantity'), True) or not finite(item.get('prior_avg_volume'), True)):
        return {'status': 'invalid', 'reason': 'Verified order size and prior volume required'}
    if modern and item['quantity'] > item['prior_avg_volume'] * policy['max_volume_participation']:
        return {'status': 'unfilled', 'reason': 'Order exceeds prior-session capacity'}
    # Cap raised 10 -> 60 with the v5 contract. It exists to reject a nonsense
    # horizon, not to pin the strategy: at 10 the exit rule was the binding
    # constraint and 51.2% of trades expired undecided (see config.EXECUTION).
    if horizon < 1 or horizon > 60:
        return {'status': 'invalid', 'reason': 'Holding horizon must be 1..60 sessions'}
    if not valid_levels(item['reference_entry'], item['stop'], item['target'], item.get('target2')):
        return {'status': 'invalid', 'reason': 'Invalid frozen trade levels'}
    dates = sorted(set(str(d)[:10] for d in sessions if str(d)[:10] > item['session'] and (as_of is None or str(d)[:10] <= as_of)))[:horizon]
    if as_of and as_of > item['session'] and (not dates or (len(dates) < horizon and dates[-1] < as_of)):
        return {'status':'unavailable','reason':'Benchmark session coverage unavailable'}
    indexed = {}
    for bar in bars:
        day = str(bar['date'])[:10]
        if day in indexed:
            return {'status': 'unavailable', 'reason': 'Duplicate execution session'}
        indexed[day] = bar
    slip, fee = policy['slippage_bps'] / 10000, policy['fee_bps_per_side'] / 10000
    entry = None
    entry_date = None
    def result(status, **extra):
        return dict(status=status, entry=entry, entry_date=entry_date,
                    planned_holding_sessions=horizon,
                    fill_note='Daily-price simulation; opening queue availability is unverified', **extra)
    for number, day in enumerate(dates, 1):
        bar = indexed.get(day)
        if bar is None or bar_error(bar) or source_priority(bar.get('source')) < 3:
            return result('unavailable', reason='Missing, invalid or nonfinal execution bar', unresolved_session=day)
        if any(a['ex_date'] == day for a in actions or []):
            return result('unavailable', reason='Corporate action during opportunity requires reconciliation', unresolved_session=day)
        o, h, l, c, v = (float(bar[k]) for k in ('open', 'high', 'low', 'close', 'volume'))
        if h == l or v <= 0:
            if modern:
                return result('unavailable', reason='Opening or exit fill cannot be verified on a locked session', unresolved_session=day)
            return result('unfilled' if entry is None else 'unavailable', reason='Locked or untraded session', exit_date=day)
        if entry is None:
            entry = o * (1 + slip)
            entry_date = day
            if abs(entry / item['reference_entry'] - 1) > policy['entry_gap_limit'] or not item['stop'] < entry < item['target'] or (not modern and item.get('quantity', 1) > v * policy['max_volume_participation']):
                entry = None
                return result('unfilled', reason='Opening price or participation outside entry policy', exit_date=day)
        ambiguous = l <= item['stop'] and h >= item['target']
        if o <= item['stop']:
            status, exit_price = 'stop', o * (1 - slip)
        elif o >= item['target']:
            status, exit_price = 'target', item['target']  # resting limit; no favorable gap windfall
        elif l <= item['stop']:
            status, exit_price = 'stop', item['stop'] * (1 - slip)
        elif h >= item['target']:
            status, exit_price = 'target', item['target']
        elif number == horizon:
            status, exit_price = 'expired', c * (1 - slip)
        else:
            continue
        net = (exit_price * (1 - fee) / (entry * (1 + fee)) - 1) * 100
        return result(status, exit_price=exit_price, exit_date=day, holding_sessions=number,
                      net_return_pct=net, ambiguous=ambiguous,
                      target_by_5=status == 'target' and number <= 5,
                      target_by_10=status == 'target' and number <= 10)
    return result('pending', observed_sessions=len(dates))


def metrics(outcomes):
    counts = Counter(o['status'] for o in outcomes)
    resolved = [o for o in outcomes if o['status'] in RESOLVED]
    returns = [o['net_return_pct'] for o in resolved]
    gains = sum(max(0, r) for r in returns)
    losses = -sum(min(0, r) for r in returns)
    n = len(resolved)
    return {'opportunities': len(outcomes), 'counts': dict(counts), 'resolved': n,
            'target_by_5_pct': 100 * sum(o.get('target_by_5', False) for o in resolved) / n if n else None,
            'target_by_10_pct': 100 * sum(o.get('target_by_10', False) for o in resolved) / n if n else None,
            'target_within_horizon_pct': 100 * counts['target'] / n if n else None,
            'net_expectancy_pct': sum(returns) / n if n else None,
            'profit_factor': gains / losses if losses else None,
            'unresolved_risk': counts['pending'] + counts['unavailable'],
            'note': 'Resolved-opportunity statistics, not calibrated forecasts. Unavailable exposure remains unresolved. No portfolio return or drawdown is inferred.'}


def replay(symbol, bars, benchmark, lookback=None, eligible=True, actions=None):
    lookback = config.REPLAY_LOOKBACK if lookback is None else lookback
    if lookback < 1:
        raise ValueError('Choose at least one trading day')
    bars = sorted(bars, key=lambda b: str(b['date']))
    benchmark = sorted(benchmark, key=lambda b: str(b['date']))
    sessions = [str(b['date'])[:10] for b in benchmark]
    start = sessions[max(0, len(sessions) - lookback)] if sessions else ''
    previous, active, outcomes, decisions = None, None, [], []
    unknown_membership = 0
    vetoes = Counter()
    bar_dates = [str(b['date'])[:10] for b in bars]
    # Warm up the same previous-session state before the evaluation boundary.
    # Each decision trims only at its own cutoff using the same live limit.
    for pos in range(max(0, len(sessions) - lookback - 1), len(sessions)):
        day = sessions[pos]
        end = bisect_right(bar_dates, day)
        window = config.FEATURE_HISTORY_LIMIT
        membership = eligible(day) if callable(eligible) else eligible
        if day >= start and membership is None:
            unknown_membership += 1
        decision = decision_engine.decide(symbol, bars[max(0, end-window):end],
                    benchmark[max(0, pos+1-window):pos+1], day,
                    bool(membership), previous, actions)
        previous = decision_engine.state(decision)
        if day < start:
            continue
        if active:
            outcome = resolve(active, bars, sessions, as_of=day, actions=actions)
            if outcome['status'] not in ('pending', 'unavailable'):
                outcomes.append(dict(outcome, symbol=symbol, signal_date=active['session']))
                active = None
        decisions.append(previous)
        if decision['signal']['signal'] == 'No data':
            vetoes.update(decision['signal']['reasons'])
        if not active and decision['signal']['signal'] in ('Buy', 'Strong Buy'):
            active = opportunity(decision)
    if active:
        outcomes.append(dict(resolve(active, bars, sessions, actions=actions),
                             symbol=symbol, signal_date=active['session']))
    usable = sum(d['signal'] != 'No data' for d in decisions)
    return {'symbol': symbol, 'metrics': metrics(outcomes), 'outcomes': outcomes,
            'decisions': decisions, 'vetoes': dict(vetoes),
            'coverage': {'tested_days': len(decisions), 'usable_days': usable,
                         'membership_unverified_days': unknown_membership,
                         'missing_days': len(decisions)-usable, 'start': start,
                         'end': sessions[-1] if sessions else None},
            'validation': 'Current rules tested on past prices. Stock-list membership is withheld before its documented date when using the database backtest. Past results do not guarantee future results.'}


def backtest(symbol, lookback=None, hold_days=None, **kwargs):
    import database as db
    import shariah_checker
    if hold_days is not None and hold_days != config.EXECUTION['holding_sessions']:
        raise ValueError('Change and register the execution contract before using a different horizon')
    return replay(symbol, db.get_daily_ohlc(symbol, 100000), db.get_eod_history(config.BENCHMARK_INDEX, 100000),
                  lookback if lookback is not None else config.REPLAY_LOOKBACK,
                  lambda day: shariah_checker.check(symbol)['eligible_for_ranking'] if day >= config.UNIVERSE_KNOWN_FROM else None, db.get_corporate_actions(symbol))


def backtest_portfolio(symbols=None, **kwargs):
    results = [backtest(symbol, **kwargs) for symbol in (symbols or config.STOCKS)]
    return {'metrics': metrics([o for r in results for o in r['outcomes']]), 'results': results,
            'note': 'Results combine separate stock trades. They do not represent the return on your whole account.'}


def update_outcomes():
    import database as db
    from session_calendar import last_completed
    cutoff = last_completed()
    sessions = [b['date'] for b in db.get_eod_history(config.BENCHMARK_INDEX, 100000)]
    for row in db.open_opportunities():
        item = json.loads(row['payload'])
        with db.conn() as c:
            old = c.execute('SELECT payload FROM opportunity_outcomes WHERE opportunity_id=?',(row['id'],)).fetchone()
        old = json.loads(old['payload']) if old else {}
        bars = {b['date']: b for b in db.get_daily_ohlc(row['symbol'], 100000) if item['session'] < b['date'] <= cutoff}
        # Once a finalized execution observation has been used, later feed
        # revisions cannot silently change the recorded fill or economic path.
        bars.update({b['date']: b for b in old.get('execution_bars', [])})
        result = resolve(item, list(bars.values()), sessions, cutoff, db.get_corporate_actions(row['symbol']))
        end = result.get('exit_date') or result.get('unresolved_session') or cutoff
        result['execution_bars'] = [b for b in bars.values() if b['date'] <= end and not bar_error(b) and source_priority(b.get('source')) >= 3]
        result['observed_at_session'] = cutoff
        db.save_opportunity_outcome(row['id'], result)

def update_cohort_outcomes():
    """Grade the prospective cohort with the SAME resolver the emitted
    opportunities use, so the two are directly comparable.

    The cohort is every candidate that reached the Watch band with valid levels,
    including the ones vetoes downgraded. Grading the rejected side is the whole
    point: without it, "does this gate reject a worse subset than it passes?" can
    only ever be answered from a backtest, and every gate this engine has
    measured that way behaved differently live.
    """
    import database as db
    from session_calendar import last_completed
    cutoff = last_completed()
    sessions = [b['date'] for b in db.get_eod_history(config.BENCHMARK_INDEX, 100000)]
    graded = 0
    for row in db.open_cohort_candidates():
        item = json.loads(row['payload'])
        with db.conn() as c:
            old = c.execute('SELECT payload FROM cohort_outcomes WHERE candidate_id=?',
                            (row['id'],)).fetchone()
        old = json.loads(old['payload']) if old else {}
        bars = {b['date']: b for b in db.get_daily_ohlc(row['symbol'], 100000)
                if item['session'] < b['date'] <= cutoff}
        # Same immutability rule as the opportunity path: a finalised execution
        # observation cannot be silently rewritten by a later feed revision.
        bars.update({b['date']: b for b in old.get('execution_bars', [])})
        result = resolve(item, list(bars.values()), sessions, cutoff,
                         db.get_corporate_actions(row['symbol']))
        end = result.get('exit_date') or result.get('unresolved_session') or cutoff
        result['execution_bars'] = [b for b in bars.values() if b['date'] <= end
                                    and not bar_error(b) and source_priority(b.get('source')) >= 3]
        result['observed_at_session'] = cutoff
        # Carry the attribution forward so the cohort can be sliced by what the
        # engine actually did with the candidate.
        result['emitted_signal'] = item.get('emitted_signal')
        result['pre_veto'] = row['pre_veto']
        result['final_score'] = item.get('final_score')
        db.save_cohort_outcome(row['id'], result)
        graded += 1
    return graded
