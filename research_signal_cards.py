"""Read-only presentation of the one canonical, combined research decision.

The caller owns loading, validation, readiness and time-based reevaluation. This
module never fetches data, changes a signal, or falls back to technical levels.
"""
from datetime import date, datetime
import math
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from research_signals import VERSION, STATUSES


FILTER_KEY = 'combined_signal_filter'
PKT = ZoneInfo('Asia/Karachi')


def _plain(value, fallback='Unavailable'):
    """Escape external text for Streamlit's Markdown-capable text elements."""
    if value is None or value == '':
        value = fallback
    value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+\-.!|>])', r'\\\1', value)


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def pkt(value):
    """Render aware clocks only; a naive source time is not silently localized."""
    parsed = _stamp(value)
    return parsed.astimezone(PKT).strftime('%d %b %Y, %H:%M:%S PKT') if parsed else 'Unavailable'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _money(value):
    return f'{value:,.2f}' if _number(value) and value > 0 else 'Unavailable'


def _eligible_plan(signal):
    """Defense in depth: never expose a reference-close or non-ready plan."""
    plan = signal.get('plan')
    if signal.get('status') != 'Ready for review' or not isinstance(plan, dict):
        return None
    entry, stop, target = (plan.get(key) for key in ('observed_entry', 'stop', 'target1'))
    if not all(_number(value) and value > 0 for value in (entry, stop, target)):
        return None
    if not stop < entry < target:
        return None
    second = plan.get('target2')
    if second is not None and (not _number(second) or second <= target):
        return None
    return plan


def _url(value):
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            return None
        return value
    except ValueError:
        return None


def _reasons(signal):
    reasons = signal.get('reasons')
    if not isinstance(reasons, list):
        reasons = []
    return list(dict.fromkeys(str(value) for value in reasons if value))


def _publication(link):
    """Never invent midnight or a time zone for date-only source evidence."""
    if link.get('published_at'):
        return pkt(link['published_at'])
    try:
        value = date.fromisoformat(str(link.get('published_date')))
        return value.strftime('%d %b %Y') + ' (date only; publication time unavailable)'
    except (TypeError, ValueError):
        return 'Unavailable'


def _show_quote(st, signal):
    quote = signal.get('quote')
    if not isinstance(quote, dict):
        st.caption('Last observed quote: unavailable. No current entry price is inferred.')
        return
    state = 'delayed observation' if quote.get('fresh') is True else 'historical / stale observation'
    st.caption(_plain('Last observed quote: PKR ' + _money(quote.get('price')) + ' · ' + state))
    st.caption(_plain('Source update: ' + pkt(quote.get('source_as_of')) +
                      ' · Fetched: ' + pkt(quote.get('fetched_at'))))
    st.caption('Nominal 5-minute source delay; this is not an executable real-time price.')


def _show_plan(st, signal):
    plan = _eligible_plan(signal)
    if plan is None:
        if signal.get('status') == 'Ready for review':
            st.warning('Eligible observed-entry levels are unavailable in this payload. Recheck the decision before acting.')
        else:
            st.caption('Entry, stop and target levels withheld until Ready for review.')
        return
    st.markdown('**Swing review levels · PKR**')
    st.markdown(_plain('Observed entry reference ' + _money(plan['observed_entry']) +
                      ' · Stop reference ' + _money(plan['stop'])))
    targets = 'Target 1 ' + _money(plan['target1'])
    if plan.get('target2') is not None:
        targets += ' · Target 2 ' + _money(plan['target2'])
    st.markdown(_plain(targets))
    low, high = plan.get('buy_zone_low'), plan.get('buy_zone_high')
    if _number(low) and _number(high) and 0 < low <= high:
        st.caption(_plain('Validated entry zone: ' + _money(low) + '–' + _money(high)))
    st.caption('Recheck spread, available volume, costs and quantity. These are review references, not an order or a guaranteed fill.')


def _show_evidence(st, signal, payload):
    symbol = str(signal.get('symbol') or 'Unknown stock')
    with st.expander(_plain('Research evidence and vetoes · ' + symbol)):
        reasons = _reasons(signal)
        if reasons:
            for reason in reasons:
                st.markdown(_plain(reason))
        else:
            st.caption('No additional reason list was supplied with this decision.')
        st.caption(_plain('Research reviewed: ' + pkt(signal.get('research_as_of')) +
                          ' · Research expires: ' + pkt(signal.get('research_expires_at'))))
        st.caption(_plain('Shared context: ' + pkt(payload.get('context_as_of')) +
                          ' · Context expires: ' + pkt(payload.get('context_expires_at'))))
        links = [link for link in signal.get('source_links', []) if isinstance(link, dict)]
        quote = signal.get('quote') or {}
        if isinstance(quote, dict) and quote.get('source_url'):
            links.append({'title': 'Official quote source', 'url': quote['source_url']})
        seen = set()
        for link in links:
            url = _url(link.get('url'))
            if not url:
                st.caption(_plain(str(link.get('title') or 'Source') + ': source link unavailable or invalid'))
            elif url not in seen:
                st.link_button(_plain(link.get('title') or 'Evidence source'), url)
                if any(link.get(key) for key in ('published_at', 'published_date', 'verified_at')):
                    st.caption(_plain('Published: ' + _publication(link) +
                                      ' · Source verified: ' + pkt(link.get('verified_at'))))
                seen.add(url)
        if not seen:
            st.caption('No usable evidence source links were supplied.')
    with st.expander(_plain('Original technical attribution · ' + symbol)):
        technical = signal.get('technical') or {}
        if not isinstance(technical, dict):
            technical = {}
        st.caption('Original completed-session attribution only. The combined status above is the current decision; a technical Buy does not override research vetoes.')
        st.markdown(_plain('Original technical signal: ' + str(technical.get('signal') or 'Unavailable')))
        st.markdown(_plain(technical.get('reason')))
        st.caption(_plain('Decision session: ' + str(technical.get('decision_session') or 'Unavailable') +
                          ' · Technical run: ' + pkt(technical.get('run_time'))))
        for label, key in (('Strategy version', 'strategy_version'), ('Configuration hash', 'config_hash'),
                           ('Snapshot hash', 'snapshot_hash')):
            st.caption(_plain(label + ': ' + str(technical.get(key) or 'Unavailable')))


def show_card(st, signal, payload):
    """Render a canonical row without interpreting its original technical call."""
    with st.container(border=True):
        st.subheader(_plain(str(signal.get('symbol') or 'Unknown stock') + ' · ' +
                            str(signal.get('status') or 'Unavailable')))
        reason = signal.get('reason') or '; '.join(_reasons(signal)) or 'No decision reason was supplied.'
        summary = reason if len(reason)<=600 else reason[:600].rstrip()+'… Full reasons are in this card’s evidence below.'
        st.markdown('**Why:** ' + _plain(summary))
        for label, key in (('Swing', 'swing_state'), ('Intraday · watch-only', 'intraday_state'),
                           ('Investment research', 'investment_state')):
            st.markdown('**' + label + ':** ' + _plain(signal.get(key)))
        _show_plan(st, signal)
        _show_quote(st, signal)
        st.caption(_plain('Decision evaluated: ' + pkt(payload.get('evaluated_at')) +
                          ' · Review expires: ' + pkt(signal.get('expires_at'))))
        _show_evidence(st, signal, payload)


def table_rows(payload):
    """Use the same canonical ordering and withholding rule as the cards."""
    rows = []
    for signal in payload.get('signals', []):
        plan = _eligible_plan(signal) or {}
        quote = signal.get('quote') or {}
        rows.append({
            'Stock': signal.get('symbol'), 'Combined status': signal.get('status'),
            'Reason / next condition': signal.get('reason') or '; '.join(_reasons(signal)),
            'Swing': signal.get('swing_state'), 'Intraday · watch-only': signal.get('intraday_state'),
            'Investment research': signal.get('investment_state'),
            'Observed entry reference (PKR)': plan.get('observed_entry'),
            'Stop reference (PKR)': plan.get('stop'), 'Target 1 (PKR)': plan.get('target1'),
            'Target 2 (PKR)': plan.get('target2'),
            'Last observed quote (PKR)': quote.get('price') if _number(quote.get('price')) else None,
            'Quote state': ('Delayed observation' if quote.get('fresh') is True else
                            'Historical / stale observation') if quote else 'Unavailable',
            'Quote source update (PKT)': pkt(quote.get('source_as_of')),
            'Quote fetched (PKT)': pkt(quote.get('fetched_at')),
            'Review expires (PKT)': pkt(signal.get('expires_at')),
        })
    return rows


def show_table(st, payload):
    st.caption('The same combined decisions as the Trading desk, in canonical stock order. Entry levels appear only for Ready for review. Quote observations have a nominal 5-minute source delay and are not executable real-time prices.')
    st.dataframe(table_rows(payload), hide_index=True, width='stretch')


def _primary_state(state):
    if not isinstance(state, dict):
        return None
    return state.get('primary_status') or state.get('status') or state.get('swing_state') or state.get('swing')


def _ready_state(state):
    return _primary_state(state) in ('Ready for review', 'Swing setup for review')


def _show_changes(st, activity, payload):
    with st.expander('Important changes and revocations'):
        if not activity:
            st.caption('No activity checkpoint is available. An absent log does not establish that conditions were unchanged.')
            return
        checked = _stamp(activity.get('checked_at'))
        evaluated = _stamp(payload.get('evaluated_at'))
        st.caption(_plain('Recorded changes through: ' + pkt(activity.get('checked_at')) +
                          ' · Decision evaluated: ' + pkt(payload.get('evaluated_at'))))
        st.caption('This is the published change log through its stated time. Later changes are not implied, and historical changes do not create new signals.')
        if checked is None or (evaluated and checked > evaluated):
            st.warning('The activity checkpoint clock is unavailable or later than this decision. Its changes are withheld.')
            return
        items = activity.get('items', [])
        if not items:
            st.caption('No changes were recorded through this checkpoint. The initial state is a baseline, not a new alert.')
        for item in reversed(items[-20:]):
            if not isinstance(item, dict):
                continue
            recorded = _stamp(item.get('recorded_at'))
            if recorded is None or recorded > checked:
                st.warning('A change with an invalid or future timestamp was withheld.')
                continue
            before, after = item.get('before') or {}, item.get('after') or {}
            revoked = _ready_state(before) and _primary_state(after) is not None and not _ready_state(after)
            title = str(item.get('symbol') or 'Stock') + ': entry review revoked' if revoked else str(item.get('title') or 'Research conditions changed')
            st.markdown('**' + _plain(title) + '**')
            st.caption(_plain(pkt(item.get('recorded_at'))))
            st.markdown(_plain(item.get('detail')))
            if _primary_state(before) or _primary_state(after):
                st.caption(_plain('Before: ' + str(_primary_state(before) or 'Unavailable') +
                                  ' · After: ' + str(_primary_state(after) or 'Unavailable')))
            for reason in (after.get('blocked_reasons') or []) if isinstance(after, dict) else []:
                st.markdown(_plain(reason))


def _show_checkpoints(st, payload, journal, collection):
    with st.expander('Observation and decision checkpoints'):
        if payload.get('recorded_at'):
            st.caption(_plain('Recorded decision checkpoint: ' + pkt(payload['recorded_at'])))
        if payload.get('recorded_checkpoint'):
            st.caption(_plain('Recorded checkpoint ID: ' + str(payload['recorded_checkpoint'])))
        if payload.get('sourcehash_coherence') is not None:
            st.caption('Current source hashes ' + ('match' if payload['sourcehash_coherence'] else 'differ from') +
                       ' the recorded checkpoint. The displayed decision uses the current canonical evaluation.')
        for name, value in (payload.get('source_hashes') or {}).items():
            st.caption(_plain(str(name).replace('_', ' ') + ': ' + str(value or 'Unavailable')))
        evaluated = _stamp(payload.get('evaluated_at'))
        for label, record, limit in (('Observation collection', collection, 660),
                                     ('Decision journal', journal, 1200)):
            record = record or {}
            checked = _stamp(record.get('checked_at'))
            st.caption(_plain(label + ' published through: ' + pkt(record.get('checked_at'))))
            if payload.get('market_open') and (not checked or not evaluated or
                                               not 0 <= (evaluated - checked).total_seconds() <= limit):
                st.warning(label + ' is late, unavailable or future-dated relative to this decision. Collection cadence is best-effort.')
            if record.get('checkpoint_id'):
                st.caption(_plain(label + ' checkpoint: ' + str(record['checkpoint_id'])))
        if collection:
            if collection.get('current_usable_points') is not None:
                st.caption(_plain('Usable new source points at last capture: ' + str(collection['current_usable_points']) + '/15'))
            if collection.get('missed_poll_windows'):
                st.warning('Missed observation windows through the published capture: ' + str(collection['missed_poll_windows']) + '. No missing samples are invented.')
        st.caption('Point observations do not establish complete intraday candles, executable liquidity, fills or stop-versus-target ordering.')


def _show_current_changes(st, payload):
    changes = payload.get('current_changes') or []
    if not changes:
        return
    st.caption(_plain('Compared with the recorded decision at ' + pkt(payload.get('recorded_at')) +
                      ', reevaluated at ' + pkt(payload.get('evaluated_at')) + ':'))
    for change in changes:
        previous, current = change.get('previous_status'), change.get('current_status')
        withdrawn = previous == 'Ready for review' and current != 'Ready for review'
        label = 'Entry review withdrawn since recorded checkpoint' if withdrawn else 'Combined status changed since recorded checkpoint'
        message = (str(change.get('symbol') or 'Stock') + ': ' + label + '. ' +
                   str(previous or 'Unavailable') + ' → ' + str(current or 'Unavailable') + '. ' +
                   str(change.get('reason') or 'Review the current reasons below.'))
        (st.warning if withdrawn else st.info)(_plain(message))
    st.caption('These are current checks against the recorded decision, not additional stored alerts. A withdrawn entry review is not a sell instruction or a filled exit.')


def show(st, payload, activity=None, journal=None, collection=None):
    """Show all 15 combined decisions, with an optional presentation-only filter."""
    if not isinstance(payload, dict) or payload.get('version') != VERSION:
        st.error('The combined research decision payload is unavailable or unsupported. Trading signals are withheld.')
        return
    st.subheader('Combined research signals')
    st.caption(_plain('Evaluated: ' + pkt(payload.get('evaluated_at')) + ' · Market ' +
                      ('open' if payload.get('market_open') else 'closed') + ' · Research ' +
                      ('current' if payload.get('research_current') else 'unavailable or expired')))
    st.caption(_plain('Context reviewed: ' + pkt(payload.get('context_as_of')) +
                      ' · Context expires: ' + pkt(payload.get('context_expires_at'))))
    counts = payload.get('counts') or {}
    for column, status in zip(st.columns(3), STATUSES):
        column.metric(status, counts.get(status, 'Unavailable'))
    st.caption('Ready means the combined evidence permits a swing entry review. Verify spread, costs and quantity first. Intraday remains watch-only; investment research is a separate horizon. No guaranteed result or fill is implied.')
    for error in payload.get('errors') or []:
        st.warning(_plain(error))
    _show_current_changes(st, payload)
    signals = payload.get('signals') or []
    if len(signals) != 15:
        st.warning('Coverage is incomplete: ' + str(len(signals)) + ' of 15 combined stock decisions were supplied. Missing decisions are not invented.')
    selected = st.radio('Show combined signals', ['All', *STATUSES], horizontal=True, key=FILTER_KEY)
    shown = [signal for signal in signals if selected == 'All' or signal.get('status') == selected]
    st.caption(_plain('Showing ' + str(len(shown)) + ' of ' + str(len(signals)) + ' supplied decisions. Counts above cover all stocks.'))
    if not shown:
        st.info('No stocks have this combined status at the evaluated time.')
    for offset in range(0, len(shown), 2):
        for column, signal in zip(st.columns(2), shown[offset:offset + 2]):
            with column:
                show_card(st, signal, payload)
    _show_changes(st, activity, payload)
    _show_checkpoints(st, payload, journal, collection)
