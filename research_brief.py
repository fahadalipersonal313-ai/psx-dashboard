"""Read-only pre-session brief from existing, independently dated artifacts.

No fetching, persistence, schedule, ranking, forecast, or new trading rule. The
review clock is ``as_of``; rebuilding an artifact does not renew that review.
Publication timestamps identify individual sources, never complete news coverage.
"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import json
from urllib.parse import urlsplit, urlunsplit

import research_calendar
import research_contract as contract
import research_desk
import session_calendar as cal

_ACTIVITY_FIELDS = (
    'swing', 'investment', 'company_review', 'company_bias', 'financial_period',
    'event_review_required', 'events', 'blocked_reasons',
)
_SOURCE_FIELDS = (
    'id', 'url', 'title', 'kind', 'published_at', 'published_date',
    'publication_precision', 'verified_at',
)


def _sessions(now):
    """Use only covered calendar dates; Friday lunch belongs to Friday."""
    local = cal.local_now(now)
    if not research_calendar.FROM <= local.date() <= research_calendar.THROUGH:
        raise ValueError('The verified exchange calendar does not cover this date')

    def spans(day):
        result = []
        for start, end in cal.intervals(day):
            opened = datetime.combine(day, time.fromisoformat(start), cal.PKT)
            closed = datetime.combine(day, time.fromisoformat(end), cal.PKT)
            if opened >= closed or (result and opened <= result[-1][1]):
                raise ValueError('Invalid exchange-session intervals')
            result.append((opened, closed))
        return result

    target = local.date()
    while target <= research_calendar.THROUGH:
        segments = spans(target)
        if segments and now < segments[-1][1]:
            break
        target += timedelta(days=1)
    else:
        raise ValueError('The next session exceeds the verified exchange calendar')
    previous = target - timedelta(days=1)
    while previous >= research_calendar.FROM:
        prior = spans(previous)
        if prior:
            break
        previous -= timedelta(days=1)
    else:
        raise ValueError('The prior session is outside the verified exchange calendar')
    return {
        'target_session': target.isoformat(),
        'target_open': segments[0][0].isoformat(),
        'target_close': segments[-1][1].isoformat(),
        'target_segments': [{'open': a.isoformat(), 'close': b.isoformat()} for a, b in segments],
        'prior_session': previous.isoformat(),
        # Actual regular close, not last_completed()'s data-publication delay.
        'prior_close': prior[-1][1].isoformat(),
        'phase': ('pre_session' if now < segments[0][0] else
                  'open' if any(a <= now < b for a, b in segments) else 'session_break'),
        'source_refs': [{'kind': 'exchange_calendar', 'url': u} for u in
                        [*research_calendar.SOURCES,
                         'https://www.psx.com.pk/psx/exchange/general/trading-hours']],
    }


def _review(context, now):
    if not isinstance(context, dict):
        return 'missing', 'Research context is unavailable'
    try:
        asof, generated, expires = (contract.stamp(context[k]) for k in
                                    ('as_of', 'generated_at', 'expires_at'))
        contract.validate(context)
        if generated > now or asof > now:
            return 'future', 'Future-dated research is withheld'
        if now >= expires:
            return 'expired', 'Research has expired; a new sourced review is required'
        if not contract.current(context, now):
            return 'invalid', 'Research contract validation failed'
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return 'invalid', 'Research contract validation failed'
    return 'current', ''


def _refs(context, source_ids):
    wanted = set(source_ids)
    return [{k: deepcopy(s[k]) for k in _SOURCE_FIELDS if k in s}
            for s in sorted(context['sources'], key=lambda s: s['id']) if s['id'] in wanted]


def _item(meta, **values):
    return {**meta, **values}


def _coverage(context, now):
    stocks = context['stocks'] if context else []
    total = len(contract.UNIVERSE)
    market = context['market_context'] if context else []
    components = [r[k] for r in stocks for k in
                  ('news', 'sector', 'fundamentals', 'public_sentiment')]
    missing_market = sum(not any(e['category'] == k and e['status'] == 'available'
                                 for e in market) for k in ('macro', 'geopolitical'))
    return {
        'tracked_stocks': total,
        'stocks_with_research': len(stocks),
        'unavailable_company_news': total - sum(r['news']['status'] != 'unavailable' for r in stocks),
        'unavailable_sector_reviews': total - sum(r['sector']['status'] == 'available' for r in stocks),
        'unavailable_public_sentiment': total - sum(r['public_sentiment']['status'] == 'available' for r in stocks),
        'unavailable_financial_reviews': total - sum(bool(contract.fundamentals_current(r['fundamentals'], now)) for r in stocks),
        'unavailable_macro_or_geopolitical_reviews': missing_market,
        'components_without_sources': ((total - len(stocks)) * 4 +
                                       sum(not e['source_ids'] for e in components) +
                                       sum(not any(e['category'] == k and e['source_ids'] for e in market)
                                           for k in ('macro', 'geopolitical'))),
        'source_count': len(context['sources']) if context else 0,
        'sources_without_publication_time': sum(not s.get('published_at') for s in context['sources']) if context else 0,
    }


def _url_key(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
                       parsed.query, ''))


def _publications(context, session, now, meta):
    """Strictly after the prior close and before first open; date-only is separate."""
    start, end = (contract.stamp(session[k]) for k in ('prior_close', 'target_open'))
    timed, dated = {}, {}
    for source in context['sources']:
        if contract.stamp(source['verified_at']) > now:
            continue
        published = source.get('published_at')
        day = source.get('published_date')
        if published:
            at = contract.stamp(published)
            if not start < at < end or at > now:
                continue
            key = (_url_key(source['url']), at)
            records = timed
            fields = {'published_at': published, 'published_date': day,
                      'timing': 'Source timestamp falls within the pre-session window'}
        elif day:
            # Include boundary dates here, but never infer which side of the close
            # or open they fall on. An entire calendar day is not a clock time.
            if not start.astimezone(cal.PKT).date() <= date.fromisoformat(day) <= min(end, now).astimezone(cal.PKT).date():
                continue
            key = (_url_key(source['url']), day)
            records = dated
            fields = {'published_at': None, 'published_date': day,
                      'timing': 'Publication time unknown; not verified as overnight'}
        else:
            continue
        if key not in records:
            records[key] = _item(meta, title=source['title'], url=source['url'],
                                 source_refs=[], **fields)
        records[key]['source_refs'].extend(_refs(context, [source['id']]))
    return (sorted(timed.values(), key=lambda v: (contract.stamp(v['published_at']), v['url'])),
            sorted(dated.values(), key=lambda v: (v['published_date'], v['url'])))


def _activity_clock(activity, now):
    result = {
        'status': 'unavailable', 'checked_at': None, 'checked_age_seconds': None,
        'items': [], 'excluded_items': 0, 'window_start': None,
        'range_coverage': 'unverified',
        'coverage_note': ('The starting checkpoint and complete retained history are not verified. '
                          'Only recorded items after the prior close and through the stated checkpoint are shown; '
                          'zero listed items does not verify no changes.'),
        'note': 'Recorded dashboard-condition changes are not new company events or verified publication times.',
    }
    if isinstance(activity, dict) and activity.get('schema_version') == 1:
        try:
            checked = contract.stamp(activity['checked_at'])
            result.update(checked_at=activity['checked_at'], checked_age_seconds=(now - checked).total_seconds())
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    return result


def _changes(activity, session, now, meta):
    result = _activity_clock(activity, now)
    if result['checked_at'] is None:
        return result
    try:
        checked = contract.stamp(result['checked_at'])
        start = contract.stamp(session['prior_close'])
        result['window_start'] = session['prior_close']
        if checked > now or not isinstance(activity['items'], list):
            return result
        if checked < start:
            result['status'] = 'stale'
            return result
    except (KeyError, ValueError, TypeError):
        return result
    result['status'] = 'available'
    seen = set()
    for item in activity['items']:
        try:
            recorded = contract.stamp(item['recorded_at'])
            before, after = item['before'], item['after']
            if (item['symbol'] not in contract.UNIVERSE or not start < recorded <= checked or
                    not isinstance(before, dict) or not isinstance(after, dict) or
                    not isinstance(item['changed_fields'], list)):
                result['excluded_items'] += 1
                continue
            fields = [k for k in _ACTIVITY_FIELDS if k in item['changed_fields'] and before.get(k) != after.get(k)]
            if not fields:
                continue
            key = json.dumps([item['symbol'], recorded.isoformat(), before, after], sort_keys=True, allow_nan=False)
            if key in seen:
                continue
            seen.add(key)
            result['items'].append(_item(
                meta, symbol=item['symbol'], title=item['symbol'] + ': dashboard conditions changed',
                recorded_at=item['recorded_at'], changed_fields=fields,
                before=deepcopy(before), after=deepcopy(after),
                source_refs=[{'kind': 'dashboard_activity', 'artifact': 'research_activity.json',
                              'checkpoint_id': item.get('checkpoint_id'), 'id': item.get('id'),
                              'recorded_at': item['recorded_at']}],
                source_note='Activity records do not retain original publication-source links; no current sources are retroactively attached.'))
        except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
            result['excluded_items'] += 1
    result['items'].sort(key=lambda v: (contract.stamp(v['recorded_at']), v['symbol']))
    return result


def _attention(desk, context, session, now, meta, current_session):
    result, seen = [], set()
    target = date.fromisoformat(session['target_session'])
    reviews = {r['symbol']: r for r in context['stocks']}
    if current_session and cal.is_live(now):
        # Preserve the desk's existing assessment. Recheck its clocks, rather
        # than promoting conditional or cached plans into new recommendations.
        try:
            desk_time_ok = contract.stamp(desk['evaluated_at']) <= now
        except (KeyError, TypeError, ValueError):
            desk_time_ok = False
        supplied = [r for r in desk.get('rows', []) if isinstance(r, dict)]
        rechecked = research_desk.build(
            context,
            {'rows': [r['technical'] for r in supplied if isinstance(r.get('technical'), dict)]},
            {'prices': [r['quote'] for r in supplied if isinstance(r.get('quote'), dict)],
             'observations': [r['observation'] for r in supplied if isinstance(r.get('observation'), dict)]},
            now=now)
        current_rows = {r['symbol']: r for r in rechecked['rows']}
        for row in supplied:
            symbol = row.get('symbol')
            current_row = current_rows.get(symbol, {})
            if (not desk_time_ok or not desk.get('research_current') or symbol not in reviews or
                    row.get('swing_state') != 'Swing setup for review' or
                    current_row.get('swing_state') != 'Swing setup for review'):
                continue
            if ('swing', symbol) in seen:
                continue
            seen.add(('swing', symbol))
            review = reviews[symbol]
            ids = {sid for k in ('news', 'sector', 'fundamentals') for sid in review[k]['source_ids']}
            ids.update(review['horizons']['swing']['source_ids'])
            ids.update(sid for e in context['market_context'] for sid in e['source_ids'])
            result.append(_item(meta, symbol=symbol, kind='existing_swing_setup',
                                title=symbol + ': Swing setup for review',
                                detail='Existing desk state only; recheck delayed price, costs and quantity. No order or fill implied.',
                                quote_as_of=research_desk.source_time(row['quote']),
                                source_refs=_refs(context, ids)))
    for symbol, review in reviews.items():
        for event in review['fundamentals'].get('events', []):
            if not 0 <= (date.fromisoformat(event['date']) - target).days <= 5:
                continue
            if contract.stamp(event['known_at']) > now:
                continue
            refs = _refs(context, event['source_ids'])
            if not refs:
                continue
            key = ('event', symbol, event['kind'], event['date'])
            if key in seen:
                continue
            seen.add(key)
            result.append(_item(meta, symbol=symbol, kind='listed_event',
                                title=symbol + ': ' + event['kind'].replace('_', ' ') + ' on ' + event['date'],
                                event_kind=event['kind'], event_date=event['date'], known_at=event['known_at'],
                                detail='Listed event within five calendar days of the target session; not a complete company-event calendar.',
                                source_refs=refs))
    return sorted(result, key=lambda v: (v['symbol'], v['kind'], v.get('event_date', ''), v.get('event_kind', '')))


def build(desk, activity=None, now=None):
    """Build a deterministic plain dictionary; callers must supply an aware now.

    ``desk`` is research_desk.build's result. Expired/invalid/missing reviews
    withhold evidence and attention. Valid prior reviews may show cached listed
    events and recorded condition changes, but never claim overnight coverage.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('An explicit timezone-aware evaluation time is required')
    desk = desk if isinstance(desk, dict) else {}
    context = desk.get('context')
    raw = context if isinstance(context, dict) else {}
    review_state, reason = _review(context, now)
    output = {
        'evaluated_at': now.isoformat(), 'as_of': raw.get('as_of'),
        'generated_at': raw.get('generated_at'), 'expires_at': raw.get('expires_at'),
        'review_status': review_state, 'status': review_state, 'label': reason,
        'research_current': review_state == 'current', 'current_session_review': False,
        'target_session': None, 'target_open': None, 'target_close': None,
        'target_segments': [], 'prior_session': None, 'prior_close': None,
        'calendar_status': 'unavailable', 'calendar_source_refs': [],
        'overnight_status': 'not_verified', 'overnight_publications': [],
        'publication_time_unknown': [], 'attention': [],
        'condition_changes': {**_activity_clock(activity, now), 'status': 'withheld'},
        'coverage': _coverage(context if review_state == 'current' else None, now),
        'limitations': [
            'Saved public research only; this brief does not fetch new sources or verify complete overnight coverage.',
            'Artifact generation time is not a new research review or a source publication time.',
            'Attention uses existing desk states and sourced listed events; no new ranking or forecast.',
        ],
    }
    try:
        session = _sessions(now)
    except (ValueError, TypeError, KeyError, OverflowError):
        output.update(status='calendar_unavailable', label='Session window unavailable: verified calendar coverage is required')
        return output
    output.update({k: v for k, v in session.items() if k != 'source_refs'})
    output.update(calendar_status='verified', calendar_source_refs=session['source_refs'])
    if review_state != 'current':
        return output
    same_day = cal.local_now(contract.stamp(context['as_of'])).date().isoformat() == session['target_session']
    if same_day:
        state = ('current_preopen_review' if contract.stamp(context['as_of']) < contract.stamp(session['target_open'])
                 else 'current_session_update')
        dated = research_desk.pkt(context['as_of'])
        label = ('Current-session pre-open review as of ' + dated if state == 'current_preopen_review'
                 else 'Current-session review updated after open as of ' + dated + '; not a pre-open review')
        if state == 'current_preopen_review' and now >= contract.stamp(session['target_open']):
            label += '; later changes not verified'
        output.update(status=state, label=label, current_session_review=True,
                      overnight_status='source_timed_items_only')
    else:
        output.update(status='cached_prior_review', label='Cached prior review / overnight changes not verified')
    meta = {k: output[k] for k in ('as_of', 'expires_at', 'target_session', 'target_open', 'prior_close')}
    meta['review_status'] = output['status']
    if same_day:
        output['overnight_publications'], output['publication_time_unknown'] = _publications(context, session, now, meta)
    output['condition_changes'] = _changes(activity, session, now, meta)
    output['attention'] = _attention(desk, context, session, now, meta, same_day)
    return output


def _show_sources(st, sources):
    seen = set()
    for index, source in enumerate(sources, 1):
        url = source.get('url')
        if url and url not in seen:
            seen.add(url)
            st.link_button(source.get('title') or 'Exchange calendar source ' + str(index), url)


def show(st, brief):
    """Render an already built brief; no hidden reads or current-clock calls."""
    st.markdown('#### Morning / pre-session brief')
    getattr(st, 'info' if brief['current_session_review'] else 'warning')(brief['label'])
    st.caption('Target regular session: ' + str(brief['target_session'] or 'unavailable') +
               ' · first open: ' + research_desk.pkt(brief['target_open']) +
               ' · prior actual close: ' + research_desk.pkt(brief['prior_close']))
    st.caption('Research as of: ' + research_desk.pkt(brief['as_of']) +
               ' · expires: ' + research_desk.pkt(brief['expires_at']) +
               ' · artifact generated: ' + research_desk.pkt(brief['generated_at']))
    coverage = brief['coverage']
    st.write('Unavailable public-sentiment reviews: ' + str(coverage['unavailable_public_sentiment']) +
             '/' + str(coverage['tracked_stocks']) + ' · financial reviews due or unavailable: ' +
             str(coverage['unavailable_financial_reviews']) + '/' + str(coverage['tracked_stocks']) +
             ' · evidence components without sources: ' + str(coverage['components_without_sources']))
    st.caption('Coverage counts describe the saved review at the evaluation time. Sources without publication clock times: ' +
               str(coverage['sources_without_publication_time']) + '.')
    if brief['overnight_status'] == 'source_timed_items_only':
        st.write('Source-timed publications between the prior close and first open: ' + str(len(brief['overnight_publications'])))
        if not brief['overnight_publications']:
            st.caption('No qualifying source timestamps in the saved review. This does not establish that no news occurred.')
    else:
        st.caption('Overnight changes are not verified for the target session.')
    for item in brief['overnight_publications']:
        st.write(item['title'] + ' · published: ' + research_desk.pkt(item['published_at']))
        _show_sources(st, item['source_refs'])
        with st.expander('Publication sources · ' + item['title']):
            st.json(item)
    for item in brief['publication_time_unknown']:
        st.write(item['title'] + ' · publication date: ' + item['published_date'] + ' · time unknown')
        _show_sources(st, item['source_refs'])
        with st.expander('Date-only source · ' + item['title']):
            st.json(item)
    st.write('Existing setups / listed events needing attention: ' + str(len(brief['attention'])))
    for item in brief['attention']:
        st.write(item['title'])
        st.caption(item['detail'])
        _show_sources(st, item['source_refs'])
        with st.expander('Attention evidence · ' + item['title']):
            st.json(item)
    changes = brief['condition_changes']
    st.write('Recorded dashboard-condition changes since the prior close: ' + str(len(changes['items'])))
    seconds = changes.get('checked_age_seconds')
    age = ('unavailable' if seconds is None else 'future timestamp' if seconds < 0 else
           str(round(seconds / 60, 1)) + ' minutes old' if seconds < 3600 else
           str(round(seconds / 3600, 1)) + ' hours old' if seconds < 86400 else
           str(round(seconds / 86400, 1)) + ' days old')
    st.caption('Activity checkpoint through: ' + research_desk.pkt(changes.get('checked_at')) + ' · age: ' + age)
    st.caption(changes['coverage_note'])
    if changes['status'] == 'stale':
        st.caption('The activity checkpoint predates the prior close; it cannot cover this session window.')
    elif changes['status'] != 'available':
        st.caption('A usable activity checkpoint for this session window is unavailable.')
    for item in changes['items']:
        st.write(item['title'] + ' · recorded: ' + research_desk.pkt(item['recorded_at']))
        st.caption('Changed: ' + ', '.join(k.replace('_', ' ') for k in item['changed_fields']) + '.')
        with st.expander('Recorded condition change · ' + item['symbol'] + ' · ' + research_desk.pkt(item['recorded_at'])):
            st.json(item)
    st.caption('Condition-change timestamps are engine recording times, not verified source-publication or dashboard-visibility times.')
    with st.expander('Brief timing, source coverage and limitations'):
        _show_sources(st, brief['calendar_source_refs'])
        st.json({k: brief[k] for k in ('evaluated_at', 'target_segments', 'calendar_source_refs', 'coverage', 'limitations')})
        st.json({k: changes[k] for k in ('status', 'checked_at', 'checked_age_seconds', 'window_start', 'range_coverage')})
