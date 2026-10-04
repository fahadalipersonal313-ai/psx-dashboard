"""Read-only, dated candle context for the approved 15-stock research desk.

API:
    history = read_history(database_path, symbol, completed_cutoff, limit=42)
    chart = prepare(desk_row, research_context, history, aware_datetime)
    plot = figure(chart)

``read_history`` never creates/downloads a database. ``prepare`` is pure and
revalidates the bars, independent calendar and combined/technical plan; callers
must display its warnings and overlay_reasons. ``figure`` does not calculate
signals. Prices/volume are raw, complete daily bars only; gaps stay visible.
Company event dates, publication dates and known-at timestamps stay separate.
"""
from datetime import date, datetime, time, timedelta
from contextlib import closing
from html import escape
from pathlib import Path
import math
import sqlite3
from urllib.parse import urlparse

import research_calendar
import research_contract as contract
import research_desk
import session_calendar as cal

# Exact banked identities from the two official full-OHLC ingestion paths.
# A substring such as "unofficial PSX historical" must never pass this gate.
OFFICIAL_SOURCES = frozenset({
    'PSX DPS historical (official)',
    'PSX mkt_summary historical (official download)',
})
DAILY_FIELDS = ('open', 'high', 'low', 'close', 'volume')
ACTION_FIELDS = {'symbol', 'ex_date', 'kind', 'factor', 'volume_factor',
                 'verified', 'source', 'known_at'}
RAW_NOTE = ('Raw, unadjusted daily OHLC and volume. Corporate actions can change '
            'the price/share basis; no synthetic candles, fills or forecasts are shown.')


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Session date must be YYYY-MM-DD')
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError('Session date must be YYYY-MM-DD')
    return day


def _number(value, positive=False):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and (value > 0 if positive else value >= 0))
    except (ValueError, OverflowError):
        return False


def _url(value):
    try:
        parsed = urlparse(value)
        return (parsed.scheme in ('https', 'http') and bool(parsed.hostname)
                and not parsed.username and not parsed.password)
    except (TypeError, ValueError):
        return False


def _unique(values):
    return list(dict.fromkeys(values))


def _bars(rows, symbol, cutoff, limit):
    """Do not fill missing sessions by borrowing earlier observations."""
    warnings, blockers, rejected, selected = [], [], [], []
    try:
        expected = research_calendar.expected(cutoff, limit)
        if expected[-1] != cutoff:
            raise ValueError('Cutoff is not an independently expected exchange session')
    except (ValueError, TypeError, IndexError) as exc:
        expected = []
        blockers.append('Independent session coverage unavailable: ' + str(exc))
    for index, original in enumerate(rows):
        try:
            bar = dict(original)
            _day(bar.get('date'))
        except (TypeError, ValueError):
            rejected.append({'row': index, 'date': (original.get('date') if isinstance(original, dict) else None),
                             'reason': 'Missing or malformed session date'})
            continue
        if bar['date'] > cutoff or (expected and bar['date'] < expected[0]):
            continue
        selected.append(bar)
    selected.sort(key=lambda b: b['date'])
    if not expected:
        selected = selected[-limit:]
    valid, seen = [], set()
    for bar in selected:
        reason = None
        if bar.get('symbol', symbol) != symbol:
            reason = 'Bar symbol differs from selected stock'
        elif bar['date'] in seen:
            reason = 'Duplicate session date'
        elif bar.get('source') not in OFFICIAL_SOURCES:
            reason = 'Unrecognized full-daily-OHLC source identity'
        elif any(not _number(bar.get(k), True) for k in DAILY_FIELDS[:4]):
            reason = 'Missing or invalid full OHLC'
        elif not _number(bar.get('volume')) or bar['volume'] % 1 != 0:
            reason = 'Missing, fractional or invalid full daily share volume'
        elif not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            reason = 'Inconsistent OHLC range'
        seen.add(bar['date'])
        if reason:
            rejected.append({'date': bar['date'], 'reason': reason})
        else:
            valid.append({k: bar[k] for k in ('date', *DAILY_FIELDS, 'source')})
    dates = [bar['date'] for bar in valid]
    missing = [d for d in expected if d not in dates]
    unexpected = [d for d in dates if expected and d not in expected]
    if rejected:
        blockers.append(str(len(rejected)) + ' malformed or untrusted banked row(s) excluded; no repairs made')
        warnings.extend(str(r.get('date') or 'undated row') + ': ' + r['reason'] for r in rejected)
    if missing:
        blockers.append(str(len(missing)) + ' expected session(s) missing: ' + ', '.join(missing))
    if unexpected:
        blockers.append('Unexpected exchange-session dates: ' + ', '.join(unexpected))
    if not valid:
        blockers.append('No validated full daily OHLC is available')
    return {'rows': valid, 'expected_dates': expected, 'missing_dates': missing,
            'unexpected_dates': unexpected, 'rejected_rows': rejected,
            'warnings': warnings, 'blockers': blockers}


def read_history(database, symbol, cutoff, limit=42):
    """Read a bounded expected-session window, including action evidence.

    Status is available/partial/unavailable. Rejected rows and missing_dates are
    explicit. raw_rows are retained for prepare's independent revalidation.
    A missing/incomplete corporate_actions table blocks levels, not valid bars.
    A missing path returns unavailable without creating files or directories.
    """
    if symbol not in contract.UNIVERSE:
        raise ValueError('Charts are limited to the approved 15-stock universe')
    _day(cutoff)
    if type(limit) is not int or not 1 <= limit <= 260:
        raise ValueError('History limit must be an integer between 1 and 260')
    result = {'symbol': symbol, 'cutoff': cutoff, 'limit': limit, 'rows': [],
              'raw_rows': [], 'actions': [], 'action_schema_complete': False,
              'read_errors': [], 'warnings': [], 'blockers': []}
    try:
        path = Path(database) if database is not None else None
    except (TypeError, ValueError):
        path = None
    if path is None or not path.is_file():
        result['read_errors'].append('Banked candle database is unavailable; no download or database creation attempted')
    else:
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA query_only=ON')
                rows = [dict(r) for r in connection.execute(
                    'SELECT symbol,date,open,high,low,close,volume,source FROM daily_ohlc WHERE symbol=?', (symbol,))]
                # Inspect dates before filtering, so SQL ordering cannot hide an
                # undated/invalid record. Dates beyond cutoff are never charted.
                checked = _bars(rows, symbol, cutoff, limit)
                result.update(checked)
                result['raw_rows'] = rows
                columns = {r[1] for r in connection.execute('PRAGMA table_info(corporate_actions)')}
                result['action_schema_complete'] = ACTION_FIELDS <= columns
                if {'symbol', 'ex_date'} <= columns:
                    result['actions'] = [dict(r) for r in connection.execute(
                        'SELECT * FROM corporate_actions WHERE symbol=? ORDER BY ex_date', (symbol,))]
        except (sqlite3.Error, OSError, ValueError) as exc:
            result['read_errors'].append('Banked history could not be read: ' + str(exc))
    if not result['action_schema_complete']:
        result['blockers'].append('Corporate-action audit table or verification fields unavailable')
    result['blockers'] = _unique(result['blockers'] + result['read_errors'])
    result['warnings'] = _unique([RAW_NOTE] + result['warnings'] + result['blockers'])
    result['status'] = 'unavailable' if not result['rows'] else ('partial' if result['blockers'] else 'available')
    result.setdefault('expected_dates', [])
    result.setdefault('missing_dates', [])
    result.setdefault('unexpected_dates', [])
    result.setdefault('rejected_rows', [])
    return result


def _action_audit(actions, bars, symbol, cutoff, now):
    warnings, blockers, shown = [], [], []
    if not isinstance(actions, list):
        return shown, warnings, ['Corporate-action records are missing or malformed']
    if not bars:
        return shown, warnings, blockers
    start, today = bars[0]['date'], cal.local_now(now).date().isoformat()
    seen = set()
    for original in actions:
        try:
            action = dict(original)
            ex = _day(action['ex_date']).isoformat()
            if action.get('symbol') != symbol:
                raise ValueError('Action symbol mismatch')
            if ex <= start:
                continue  # No pre-action candle in this view.
            known_value = action.get('known_at')
            if isinstance(known_value, str) and len(known_value) == 10:
                known_day = _day(known_value).isoformat()
                known_by_now = known_day < today  # Date alone cannot establish a same-day ordering.
            else:
                known = contract.stamp(known_value)
                known_day = cal.local_now(known).date().isoformat()
                known_by_now = known <= now
            if not known_by_now:
                # Do not expose the action, ex-date or terms from future
                # knowledge. A same-day date-only record has no known ordering.
                blockers.append('Corporate-action knowledge is not established by evaluation time; metadata withheld')
                continue
            shown.append(action)
            warnings.append('Corporate action recorded for ' + ex + ': ' + str(action.get('kind', 'unknown'))
                            + '. Candles remain raw and unadjusted.')
            if ex in seen:
                raise ValueError('Duplicate corporate-action date')
            seen.add(ex)
            valid = (type(action.get('verified')) in (bool, int) and action['verified'] in (True, 1)
                     and _url(action.get('source')) and action.get('kind') in ('split', 'bonus', 'rights', 'dividend')
                     and _number(action.get('factor'), True) and _number(action.get('volume_factor'), True)
                     and known_day <= ex and known_by_now)
            if not valid:
                raise ValueError('Unresolved or unsourced corporate action')
            if cutoff < ex <= today:
                raise ValueError('Action occurred after the technical decision; price basis must be refreshed')
            if ex <= cutoff:
                pair = next(((left, right) for left, right in zip(bars, bars[1:]) if right['date'] == ex), None)
                if not pair:
                    raise ValueError('Action date has no adjacent verified daily bars')
                residual = pair[1]['close'] / (pair[0]['close'] * action['factor']) - 1
                if not math.isfinite(residual) or abs(residual) > .105:
                    raise ValueError('Corporate-action factor does not reconcile the raw price sequence')
            elif ex <= (cal.local_now(now).date() + timedelta(days=1)).isoformat():
                raise ValueError('Imminent corporate action requires a fresh price-basis review')
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            blockers.append(str(exc) + ': ' + str((original or {}).get('ex_date', 'undated') if isinstance(original, dict) else 'undated'))
    for left, right in zip(bars, bars[1:]):
        if abs(right['close'] / left['close'] - 1) > .105 or abs(right['open'] / left['close'] - 1) > .105:
            blockers.append('Raw price discontinuity on ' + right['date'] + '; current levels withheld across mixed price bases')
    return shown, _unique(warnings), _unique(blockers)


def _events(context, symbol, now):
    """Publications are not events; verified_at is not a publication time."""
    try:
        contract.validate(context)
        review = next(x for x in context['stocks'] if x['symbol'] == symbol)
    except (KeyError, TypeError, ValueError, StopIteration, OverflowError):
        return [], ['Sourced event/announcement metadata is unavailable or invalid']
    if (contract.stamp(context['generated_at']) > now
            or contract.stamp(context['as_of']) > now):
        return [], ['Research event/announcement context is future-dated; metadata withheld']
    sources = {s['id']: s for s in context['sources']}
    result, warnings = [], []
    stale = now >= contract.stamp(context['expires_at'])
    if stale:
        warnings.append('Event/announcement metadata is from expired research context as of '
                        + context['as_of'] + '; it is historical context, not a current review')
    today = cal.local_now(now).date().isoformat()

    def source_available(source):
        # Recheck each referenced clock independently. Event known_at alone
        # cannot authorize publication/verification evidence from the future.
        return (contract.stamp(source['verified_at']) <= now
                and (not source.get('published_at') or contract.stamp(source['published_at']) <= now)
                and (not source.get('published_date') or source['published_date'] <= today))

    for event in review['fundamentals'].get('events', []):
        day = event['date']
        if contract.stamp(event['known_at']) > now:
            warnings.append('Event known-at time is in the future; event omitted')
            continue
        if not all(source_available(sources[sid]) for sid in event['source_ids']):
            warnings.append('Event source publication/verification is future-dated; event omitted')
            continue
        result.append({'date': day, 'precision': 'date', 'type': 'event', 'kind': event['kind'],
                       'label': event['kind'].replace('_', ' ').capitalize(),
                       'timing': 'scheduled_future' if day > cal.local_now(now).date().isoformat() else 'listed_date',
                       'known_at': event['known_at'], 'sources': [dict(sources[s]) for s in event['source_ids']],
                       'note': 'Listed event date only; exact time and actual occurrence are not established'})
    refs = _unique(review['news']['source_ids'] + review['fundamentals']['source_ids'])
    for sid in refs:
        source = sources[sid]
        if not source_available(source):
            warnings.append('Announcement publication/verification is future-dated; metadata omitted')
            continue
        at, day = source.get('published_at'), source.get('published_date')
        if at:
            if contract.stamp(at) > now:
                continue
            day = cal.local_now(contract.stamp(at)).date().isoformat()
        if day and day > cal.local_now(now).date().isoformat():
            continue
        if not day:
            # Keep a readable undated record instead of inventing publication
            # from retrieval time or using a company-event date for the source.
            precision = 'unknown'
        else:
            precision = 'timestamp' if at else 'date'
        result.append({'date': day, 'published_at': at, 'published_date': source.get('published_date'),
                       'precision': precision, 'type': 'announcement', 'kind': source['kind'],
                       'label': source['title'], 'timing': 'publication' if day else 'undated_publication',
                       'known_at': None, 'verified_at': source['verified_at'], 'sources': [dict(source)],
                       'note': 'Source publication metadata; source verification time is shown separately'})
    for item in result:
        item['context_status'] = 'expired' if stale else 'current'
        item['context_as_of'] = context['as_of']
        item['context_expires_at'] = context['expires_at']
        if stale:
            item['note'] += '. Historical metadata from expired research context as of ' + context['as_of']
    return result, warnings


def prepare(row, context, history, now):
    """Pure chart model. Overlay numbers come only from revalidated engine data.

    The caller supplies a research_desk.build row and the same context. Full
    source/session/action checks are rerun, and a fresh combined desk is built.
    Levels start at now, strictly after completed candles, never at history start.
    Events are metadata, never inferred explanations for a price movement.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Chart evaluation time needs a timezone')
    row = row if isinstance(row, dict) else {}
    history = history if isinstance(history, dict) else {}
    symbol = row.get('symbol')
    if symbol not in contract.UNIVERSE:
        raise ValueError('Charts are limited to the approved 15-stock universe')
    current = cal.last_completed(now)
    cutoff = history.get('cutoff')
    warnings, reasons = [RAW_NOTE], list(history.get('read_errors', []))
    try:
        _day(cutoff)
        limit = history.get('limit', 42)
        if type(limit) is not int or not 1 <= limit <= 260:
            raise ValueError('Invalid history window')
        audit = _bars(history.get('raw_rows', history.get('rows', [])), symbol, cutoff, limit)
    except (ValueError, TypeError):
        audit = {'rows': [], 'warnings': [], 'blockers': ['History cutoff or rows are invalid'],
                 'expected_dates': [], 'missing_dates': [], 'unexpected_dates': [], 'rejected_rows': []}
    warnings.extend(audit['warnings'])
    reasons.extend(audit['blockers'])
    bars = audit['rows']
    if any(b['date'] > current for b in bars):
        reasons.append('History includes an incomplete or future session; those candles are withheld')
        bars = [b for b in bars if b['date'] <= current]
    if history.get('symbol') != symbol:
        reasons.append('History belongs to a different stock')
        bars = []
    if cutoff != current or not bars or bars[-1]['date'] != current:
        reasons.append('History end, technical decision and current completed session must align')
    try:
        if [b['date'] for b in bars[-42:]] != research_calendar.expected(current, 42):
            reasons.append('A complete independently verified 42-session sequence is required for levels')
    except ValueError as exc:
        reasons.append(str(exc))
    if history.get('action_schema_complete') is not True:
        reasons.append('Corporate-action audit table or verification fields unavailable')
    actions, action_warnings, action_reasons = _action_audit(history.get('actions', []), bars, symbol, cutoff, now)
    warnings.extend(action_warnings)
    reasons.extend(action_reasons)
    events, event_warnings = _events(context, symbol, now)
    warnings.extend(event_warnings)
    technical = row.get('technical') if isinstance(row.get('technical'), dict) else None
    try:
        validated, why = research_desk.technical_plan(technical, now)
    except (AttributeError, TypeError, ValueError, OverflowError):
        validated, why = None, 'Technical observation failed validation'
    if not validated:
        reasons.append(why)
    if row.get('blocked_reasons') or row.get('missing'):
        reasons.append('Combined research is blocked or missing required evidence')
    if not row.get('plan'):
        reasons.append('No eligible combined research plan')
    canonical = None
    try:
        rebuilt = research_desk.build(context, {'rows': [technical]} if technical else {},
                                      {'prices': [row['quote']]} if row.get('quote') else {}, now)
        checked = next(r for r in rebuilt['rows'] if r['symbol'] == symbol)
        canonical = checked['plan']
        if not canonical or checked['blocked_reasons'] or checked['missing']:
            reasons.append('Combined context failed independent current-plan validation')
        elif not isinstance(row.get('plan'), dict) or any(row['plan'].get(k) != v for k, v in canonical.items()):
            reasons.append('Supplied plan differs from the independently validated engine plan')
        if validated and technical.get('symbol') != symbol:
            reasons.append('Technical observation belongs to a different stock')
        if validated and bars and not math.isclose(validated['reference_entry'], bars[-1]['close'], rel_tol=1e-9, abs_tol=1e-8):
            reasons.append('Technical reference price differs from banked completed close')
        if validated:
            spans = cal.intervals(_day(validated['decision_session']))
            completed_at = datetime.combine(_day(validated['decision_session']), time.fromisoformat(spans[-1][1]), cal.PKT)
            import config
            completed_at += timedelta(minutes=config.PUBLICATION_DELAY_MINUTES)
            if not completed_at <= contract.stamp(technical['run_time']) <= now:
                reasons.append('Technical run precedes the completed-session availability or is future-dated')
            if now <= completed_at:
                reasons.append('Current evaluation is not after the completed candles')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        reasons.append('Combined plan or timestamp validation failed')
    reasons = _unique(reasons)
    overlay = None
    if not reasons and canonical:
        overlay = {k: canonical.get(k) for k in ('reference_entry', 'stop', 'target1', 'target2', 'buy_zone_low', 'buy_zone_high')}
        overlay.update(start=now.isoformat(), evaluated_at=now.isoformat(), decision_session=current,
                       state=checked['swing_state'],
                       label='Conditional reference levels, evaluated now; not a forecast or executed trade')
        low, high = overlay['buy_zone_low'], overlay['buy_zone_high']
        if not (_number(low, True) and _number(high, True)
                and overlay['stop'] < low <= overlay['reference_entry'] <= high < overlay['target1']):
            overlay['buy_zone_low'] = overlay['buy_zone_high'] = None
            warnings.append('Entry-zone band unavailable or inconsistent; only validated reference levels shown')
    warnings.extend(reasons)
    return {'symbol': symbol, 'status': 'unavailable' if not bars else ('partial' if audit['blockers'] else 'available'),
            'cutoff': cutoff, 'current_completed_session': current, 'evaluated_at': now.isoformat(),
            'rows': bars, 'sources': sorted({b['source'] for b in bars}),
            'expected_dates': audit['expected_dates'], 'missing_dates': audit['missing_dates'],
            'rejected_rows': audit['rejected_rows'], 'actions': actions, 'events': events,
            'overlay': overlay, 'overlay_reasons': reasons, 'warnings': _unique(warnings),
            'source_note': 'Stored source identities shown; labels alone are not independent reconciliation.',
            'price_basis': 'raw_unadjusted', 'chart_kind': 'completed_daily_ohlc'}


def figure(prepared):
    """Render prepare's model. Continuous dates retain weekends and missing bars.

    Level segments occupy a clearly labelled display area to the right of the
    last completed candle. The area has no forecast candles or target dates.
    Future listed events remain in metadata/a caption, not synthetic price bars.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    bars = prepared['rows']
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.055, row_heights=[.76, .24])
    dates = [b['date'] for b in bars]
    hover = [escape(b['date'] + ' · ' + b['source']) + '<br>Raw, unadjusted completed daily bar' for b in bars]
    fig.add_trace(go.Candlestick(x=dates, open=[b['open'] for b in bars], high=[b['high'] for b in bars],
                                low=[b['low'] for b in bars], close=[b['close'] for b in bars],
                                increasing_line_color='#41d6ad', decreasing_line_color='#ff7487',
                                name='Completed daily OHLC', text=hover, hoverinfo='text+x+y'), row=1, col=1)
    fig.add_trace(go.Bar(x=dates, y=[b['volume'] for b in bars], name='Full daily volume',
                        marker_color=['#41d6ad' if b['close'] >= b['open'] else '#ff7487' for b in bars],
                        customdata=[b['source'] for b in bars],
                        hovertemplate='%{x|%Y-%m-%d}<br>Volume: %{y:,.0f} shares<br>%{customdata}<extra></extra>'), row=2, col=1)
    overlay = prepared.get('overlay')
    if overlay:
        start = contract.stamp(overlay['start'])
        # This is a display pad, not a holding period or a predicted target time.
        end = start + timedelta(days=2)
        for key, title, color in (('reference_entry', 'Reference', '#78b7ff'), ('stop', 'Stop', '#ff7487'),
                                  ('target1', 'Target 1', '#41d6ad'), ('target2', 'Target 2', '#b2e981')):
            level = overlay.get(key)
            if level is None:
                continue
            fig.add_shape(type='line', x0=start.isoformat(), x1=end.isoformat(), y0=level, y1=level,
                          line={'color': color, 'width': 1.4, 'dash': 'dot'}, row=1, col=1)
            fig.add_annotation(x=end.isoformat(), y=level, text=title + ' ' + f'{level:,.2f}',
                               showarrow=False, xanchor='left', font={'color': color, 'size': 11}, row=1, col=1)
        if overlay.get('buy_zone_low') is not None:
            fig.add_shape(type='rect', x0=start.isoformat(), x1=end.isoformat(),
                          y0=overlay['buy_zone_low'], y1=overlay['buy_zone_high'],
                          fillcolor='rgba(120,183,255,.12)', line_width=0, layer='below', row=1, col=1)
        fig.add_annotation(x=start.isoformat(), y=1, xref='x', yref='paper', xanchor='left',
                           text='Reference levels as of evaluation<br>Display area only; no forecast',
                           showarrow=False, font={'size': 10, 'color': '#b8c4d8'})
    if bars:
        plotted = [e for e in prepared.get('events', []) if e.get('date') and dates[0] <= e['date'] <= dates[-1]]
        for kind, name, color, marker in (('event', 'Listed company event', '#ffc66d', 'diamond'),
                                        ('announcement', 'Source publication', '#c5a0ff', 'circle')):
            items = [e for e in plotted if e['type'] == kind]
            if not items:
                continue
            lower = min(b['low'] for b in bars)
            details = [escape(e['label']) + '<br>' + escape(e['date'])
                       + (' (date only; no event time)' if e['precision'] == 'date' else ' (publication timestamp available)')
                       + ('<br>Known at: ' + escape(e['known_at']) if e.get('known_at') else '')
                       + '<br>' + escape(e['note']) for e in items]
            fig.add_trace(go.Scatter(x=[e['date'] for e in items], y=[lower * .98] * len(items),
                                     mode='markers', marker={'symbol': marker, 'size': 9, 'color': color},
                                     text=details, hovertemplate='%{text}<extra></extra>', name=name), row=1, col=1)
    if bars:
        actions = [a for a in prepared.get('actions', []) if a.get('ex_date') and dates[0] <= a['ex_date'] <= dates[-1]]
        if actions:
            fig.add_trace(go.Scatter(
                x=[a['ex_date'] for a in actions], y=[max(b['high'] for b in bars) * 1.02] * len(actions),
                mode='markers', marker={'symbol': 'x', 'size': 10, 'color': '#ffc66d'}, name='Banked action record',
                text=[escape(str(a.get('kind', 'unknown'))) + '<br>Recorded ex-date: ' + escape(a['ex_date'])
                      + '<br>Raw bars are not adjusted; see source/action checks' for a in actions],
                hovertemplate='%{text}<extra></extra>'), row=1, col=1)
    fig.update_layout(template='plotly_dark', paper_bgcolor='#111827', plot_bgcolor='#111827',
                      height=580, margin={'l': 45, 'r': 120 if overlay else 25, 't': 65, 'b': 40},
                      # The selected-stock heading already identifies the chart.
                      # Keep the legend above the plot without a competing title.
                      legend={'orientation': 'h', 'y': 1.02, 'yanchor': 'bottom', 'x': 0, 'xanchor': 'left'}, hovermode='x unified',
                      xaxis_rangeslider_visible=False, dragmode='pan')
    fig.update_xaxes(type='date', showgrid=True, gridcolor='rgba(148,163,184,.08)', rangeslider_visible=False)
    fig.update_yaxes(title_text='PKR · raw', gridcolor='rgba(148,163,184,.13)', row=1, col=1)
    fig.update_yaxes(title_text='Shares', gridcolor='rgba(148,163,184,.08)', row=2, col=1)
    if not bars:
        fig.add_annotation(x=.5, y=.5, xref='paper', yref='paper', text='No validated daily candles available', showarrow=False)
    return fig


def show(st, prepared):
    """Streamlit adapter for the selected-stock Annotated daily chart tab."""
    st.caption(RAW_NOTE)
    if prepared['rows']:
        st.plotly_chart(figure(prepared), width='stretch',
                        config={'displaylogo': False, 'scrollZoom': False})
        st.caption(str(len(prepared['rows'])) + ' banked daily candles · '
                   + prepared['rows'][0]['date'] + ' to ' + prepared['rows'][-1]['date']
                   + ' · expected sessions missing: ' + str(len(prepared['missing_dates'])))
    else:
        st.info('No validated full daily candles are available for this stock. No prices are inferred.')
    overlay = prepared.get('overlay')
    if overlay:
        st.write('Current combined condition: ' + overlay['state'])
        st.caption('Conditional reference levels rechecked at ' + research_desk.pkt(prepared['evaluated_at'])
                   + '. The right-side display area is not a target date or a holding deadline. '
                   'These levels were not assumed known during the earlier candles.')
    else:
        st.info('Entry, stop and target overlays are withheld for this chart.')
    with st.expander('Candle sources, gaps and checks', expanded=not bool(prepared['rows'])):
        st.caption(prepared['source_note'])
        for source in prepared['sources']:
            st.write('Stored source: ' + source)
        for warning in prepared['warnings']:
            if warning != RAW_NOTE:
                st.write('• ' + warning)
    with st.expander('Company events and announcement dates'):
        st.caption('Source publication, listed event date, source verification and event known-at are different facts. '
                   'A listed date does not prove an event occurred. This is not a complete corporate-event calendar '
                   'and no price movement is attributed to an event without evidence.')
        if not prepared['events']:
            st.write('No valid sourced event or announcement metadata is available in the research context.')
        for event in prepared['events']:
            if event.get('context_status') == 'expired':
                st.caption('Historical metadata from expired research context · reviewed as of '
                           + research_desk.pkt(event['context_as_of']) + ' · expired '
                           + research_desk.pkt(event['context_expires_at']) + '. Current confirmation is required.')
            if event['type'] == 'event':
                heading = ('Future listed event' if event['timing'] == 'scheduled_future' else 'Listed event')
                st.write(heading + ': ' + event['label'] + ' · ' + event['date'] + ' (date only)')
                st.caption('Known at: ' + research_desk.pkt(event['known_at']) + '. ' + event['note'])
            else:
                publication = (research_desk.pkt(event['published_at']) if event.get('published_at')
                               else event['date'] + ' (date only)' if event.get('date') else 'date unavailable')
                st.write('Source publication: ' + event['label'] + ' · ' + publication)
                st.caption('Source verified: ' + research_desk.pkt(event['verified_at'])
                           + '. Verification is not the publication time.')
            for source in event['sources']:
                st.link_button(source['title'], source['url'])
        for action in prepared['actions']:
            st.write('Banked corporate-action record: ' + str(action.get('kind', 'unknown'))
                     + ' · ex-date ' + str(action.get('ex_date', 'unavailable')))
            st.caption('Recorded verification flag: ' + str(action.get('verified'))
                       + ' · known-at: ' + str(action.get('known_at') or 'unavailable')
                       + '. A stored flag alone does not resolve source, timing or price-basis checks.')
            if _url(action.get('source')):
                st.link_button('Corporate-action source', action['source'])
