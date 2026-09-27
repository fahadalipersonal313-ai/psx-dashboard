"""Plain-language presentation for past signal checks."""
import pandas as pd
import config

STATUS = {'target': 'Reached the target', 'stop': 'Reached the loss limit',
          'expired': 'Reached the planned holding limit', 'pending': 'Still waiting',
          'unfilled': 'Could not buy at the allowed price',
          'unavailable': 'Missing prices to finish the check', 'invalid': 'Trade could not be checked'}


def explain_run(row):
    signal = row.get('signal')
    if signal == 'No data':
        return 'There are not enough verified recent prices to assess this stock.', 'Wait for complete daily prices.'
    reason = {'Strong Buy': 'The Buy rules passed on two consecutive trading days.',
              'Buy': 'The stock meets the current Buy rules.',
              'Watch': 'Some signs are positive, but the stock does not meet every Buy rule yet.',
              'Hold': 'The current signs are mixed. There is no new Buy signal.',
              'Avoid': 'The stock does not meet the current buying rules.',
              'Exit': 'The price has weakened below the level used to limit losses.'}.get(signal, 'No clear signal yet.')
    flags = str(row.get('main_reason') or '')
    if 'risk-off' in flags:
        reason += ' The wider market is falling.'
    if 'money flow' in flags:
        reason += ' Buying activity is not strong enough.'
    if 'laggard' in flags:
        reason += ' It is weaker than the wider market.'
    if 'risk/reward' in flags:
        reason += ' The possible gain is too small compared with the possible loss.'
    return reason, 'Prices can fall. Review the loss limit and the amount you invest.'


def coverage_note(result):
    reasons = ' '.join(result.get('vetoes', {}))
    if not reasons:
        return 'All dates checked'
    if 'discontinuity' in reasons or 'action' in reasons:
        return 'A large price change needs verification'
    if 'At least' in reasons:
        return 'Not enough daily prices yet'
    return 'Some daily prices are missing or need verification'


def show(st, result):
    m = result['metrics']
    rows = result.get('results', [result])
    usable = sum(r['coverage']['usable_days'] for r in rows)
    missing = sum(r['coverage']['missing_days'] for r in rows)
    unknown = sum(r['coverage'].get('membership_unverified_days', 0) for r in rows)
    a, b, c = st.columns(3)
    a.metric('Buy signals found', m['opportunities'])
    b.metric('Completed trades', m['resolved'])
    c.metric('Reached the target', m['counts'].get('target', 0))
    if not usable:
        st.warning('There are not enough verified prices to check this period yet.')
    elif not m['opportunities'] and not unknown:
        st.info('The check worked. No Buy signals met all the rules in this period.')
    if unknown:
        st.warning(f'{unknown} stock-days have no verified stock-list membership. Those dates cannot create trades; this is missing evidence, not a failed buying signal.')
    if missing:
        st.warning(f'{missing} stock-days could not be checked because prices were missing or needed verification.')
    if m['net_expectancy_pct'] is not None:
        st.metric('Average return per completed trade, after costs', f"{m['net_expectancy_pct']:.2f}%")
    st.caption('Each signal uses only prices available by its analysis date. Opening fills are daily-price estimates; actual order availability is not verified. Earlier stock-list membership is not assumed.')
    st.dataframe(pd.DataFrame([{'Stock': r['symbol'], 'Days checked': r['coverage']['usable_days'],
        'Days missing': r['coverage']['missing_days'], 'Buy signals': r['metrics']['opportunities'],
        'Membership not verified': r['coverage'].get('membership_unverified_days', 0),
        'Targets reached': r['metrics']['counts'].get('target', 0),
        'Loss limits reached': r['metrics']['counts'].get('stop', 0),
        'Still waiting': r['metrics']['counts'].get('pending', 0),
        'Data check': coverage_note(r)} for r in rows]), hide_index=True)
    trades = [o for r in rows for o in r['outcomes']]
    if trades:
        st.dataframe(pd.DataFrame([{'Stock': o['symbol'], 'Signal date': o['signal_date'],
            'Result': STATUS.get(o['status'], o['status']), 'Trading days held': o.get('holding_sessions'),
            'Planned holding limit': o.get('planned_holding_sessions'), 'Buy price': o.get('entry'),
            'Sell price': o.get('exit_price'), 'Return after costs (%)': o.get('net_return_pct')}
            for o in trades]), hide_index=True)
    st.caption(f'Stock-list membership before {config.UNIVERSE_KNOWN_FROM} is not verified and cannot create entries. Current rules are experimental; results do not show the return on your whole account.')
