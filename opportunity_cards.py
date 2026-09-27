"""Compact, escaped cards shared by Streamlit and the local layout preview."""
import html
import math
from datetime import datetime

import config
import session_calendar as cal

CSS = '''<style>
.trade-card{background:#111e30;color:#eef5ff;border:1px solid #2b4058;border-radius:14px;padding:16px 18px;margin:8px 0;font-family:Inter,Segoe UI,sans-serif;font-size:13px;line-height:1.45}
.trade-head{display:flex;justify-content:space-between;align-items:center;gap:10px}.trade-symbol{font-size:21px;font-weight:750;letter-spacing:.4px}.trade-sub{font-size:11px;color:#a9bfd4}
.trade-badge{border-radius:6px;padding:4px 8px;font-size:11px;font-weight:700;background:#183c35;color:#8aefd0}.trade-badge.watch{background:#3b3420;color:#ffda83}
.trade-levels{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:13px 0}.trade-levels small{display:block;color:#a9bfd4;font-size:10px}.trade-levels b{font-size:16px;font-weight:650}
.trade-line{margin:7px 0;overflow-wrap:anywhere}.trade-line strong{color:#a9bfd4;font-size:11px;display:inline-block;min-width:45px}.trade-risk{color:#ffcf9a}.trade-foot{border-top:1px solid #293b50;margin-top:10px;padding-top:8px;display:flex;justify-content:space-between;gap:10px;font-size:10px;color:#a9bfd4}
.trade-news{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#bfcee1}.trade-news span{background:#1c2a40;padding:3px 7px;border-radius:5px}
@media(max-width:550px){.trade-levels{grid-template-columns:repeat(2,minmax(0,1fr))}.trade-head{align-items:flex-start}.trade-card{padding:13px}}
</style>'''


def text(value, fallback='Not available'):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return fallback
    return str(value)


def fmt(value, signed=False):
    try:
        return format(float(value), '+.2f' if signed else '.2f') if math.isfinite(float(value)) else '—'
    except (TypeError, ValueError):
        return '—'


def card_html(symbol, mode, state, levels, reason, condition, risk, news, stamp, foot):
    esc = html.escape
    badges = ''.join('<span>' + esc(str(n)) + '</span>' for n in news)
    values = ''.join('<div><small>' + esc(k) + '</small><b>' + esc(str(v)) + '</b></div>' for k, v in levels)
    tone = '' if state in ('Buy', 'Strong Buy') else ' watch'
    return (f'<article class="trade-card"><div class="trade-head"><div><div class="trade-symbol">{esc(symbol)}</div>'
            f'<div class="trade-sub">{esc(mode)}</div></div><span class="trade-badge{tone}">{esc(state)}</span></div>'
            f'<div class="trade-levels">{values}</div><div class="trade-line"><strong>WHY</strong> {esc(reason)}</div>'
            f'<div class="trade-line"><strong>ENTRY</strong> {esc(condition)}</div>'
            f'<div class="trade-line trade-risk"><strong>RISK</strong> {esc(risk)}</div>'
            f'<div class="trade-news">{badges}</div><div class="trade-foot"><span>{esc(stamp)}</span><span>{esc(foot)}</span></div></article>')


def reviewers():
    import news_review_panel
    return [(label, *news_review_panel.load(name)) for label, name in
            (('Claude', 'news_ai_ratings.json'), ('Codex', 'news_codex_ratings.json'))]


def news_tags(symbol, reviews):
    from news_review_panel import LABELS
    tags = []
    for label, ratings, meta in reviews:
        rating = ratings.get(symbol)
        desc = LABELS.get((rating or {}).get('rating'), 'No fresh review')
        tags.append(label + ': ' + desc)
    return tags


def show_swing(st, rows, on_detail=None, details=True):
    st.markdown(CSS, unsafe_allow_html=True)
    reviews = reviewers()
    st.caption('Swing opportunities · completed-session analysis. Check the live price before entry; these levels are not guaranteed fills.')
    for offset in range(0, len(rows), 2):
        columns = st.columns(2)
        for col, r in zip(columns, rows[offset:offset+2]):
            symbol = r['symbol']
            zone = f"{fmt(r.get('buy_zone_low'))}–{fmt(r.get('buy_zone_high'))}"
            condition = ('Consider only after price holds the stated entry area; confirm current spread and available volume.'
                         if '—' not in zone else 'Entry area unavailable; verify current structure and price before considering an entry.')
            state = text(r.get('display_signal', r.get('signal')))
            actual = text(r.get('signal'))
            if state != actual:
                state = 'What-if ' + state + ' · actual ' + actual
            if r.get('decision_session') != cal.last_completed():
                state = 'Historical · ' + state
                condition = 'Completed-session data is behind or undated. Refresh before considering an entry.'
            if actual == 'Exit':
                condition = 'Review the existing position and exit conditions; this is not a new entry.'
            with col:
                st.markdown(card_html(symbol, 'SWING · ' + config.SECTORS.get(symbol, 'Sector not available'), state,
                    [('Signal close', fmt(r.get('price'))), ('Entry area', zone),
                     ('Loss reference', fmt(r.get('stop_loss'))), ('Target reference', fmt(r.get('target1')))],
                    text(r.get('main_reason'))[:185], condition, text(r.get('main_risk'))[:150],
                    news_tags(symbol, reviews), 'Price session ' + text(r.get('decision_session')),
                    'Data: ' + text(r.get('data_quality')) + ' · Shariah: ' + text(r.get('shariah_status'))), unsafe_allow_html=True)
                if not details:
                    continue
                with st.expander('Full reason, levels & news · ' + symbol):
                    st.write('Why:', text(r.get('main_reason')))
                    st.write('Main risk:', text(r.get('main_risk')))
                    st.write('Support / resistance:', fmt(r.get('support')), '/', fmt(r.get('resistance')))
                    st.write('Second target:', fmt(r.get('target2')))
                    st.write('Market conditions:', text(r.get('market_regime')))
                    for label, ratings, meta in reviews:
                        review = ratings.get(symbol)
                        st.write(label + ' review time:', meta.get('as_of', meta.get('status')))
                        if review:
                            st.write(review.get('reason'))
                            st.write(review.get('sources', []))
                    if on_detail:
                        on_detail(r)


def intraday_panel(st, capture, now, details=True):
    st.markdown(CSS, unsafe_allow_html=True)
    st.subheader('Intraday momentum')
    checked = datetime.fromisoformat(capture['checked_at'])
    current = (cal.is_live(now) and capture['session'] == cal.local_now(now).date().isoformat()
               and 0 <= (now-checked).total_seconds() <= 1200)
    if not current:
        import html
        message = ('Market closed · live momentum resumes next trading session.' if not cal.is_live(now)
                   else 'Waiting for a fresh scan. Previous captures are not current trading opportunities.')
        st.markdown('<div class="desk-note">' + html.escape(message) + '</div>', unsafe_allow_html=True)
    else:
        st.caption('Opening gap · movement since open · last 15 minutes · traded value')
    reviews = reviewers()
    rows = capture.get('observations', [])
    candidates = [r for r in rows if r.get('qualifies') and r.get('state') != 'Unavailable']
    candidates.sort(key=lambda r: (not bool(r.get('episode')), -(r.get('recent_pct') or 0), r['symbol']))
    if not current:
        candidates = []
    elif not candidates:
        st.markdown('<div class="desk-note">No stocks pass all intraday checks yet. Waiting for sufficient live evidence.</div>', unsafe_allow_html=True)
    for offset in range(0, len(candidates), 2):
        for col, r in zip(st.columns(2), candidates[offset:offset+2]):
            row_fresh = current and 0 <= (now-datetime.fromisoformat(r['last_trade'])).total_seconds() <= 1200
            col.markdown(card_html(r['symbol'], 'INTRADAY · 15-minute observations', r['state'] if row_fresh else 'Historical · not current',
                [('Last price', fmt(r.get('price'))), ('Opening gap %', fmt(r.get('gap_pct'), True)),
                 ('Since open %', fmt(r.get('since_open_pct'), True)), ('Last 15 min %', fmt(r.get('recent_pct'), True))],
                text(r.get('reason')), 'Watch above ' + fmt(r.get('confirmation_reference')) + '; reassess below ' + fmt(r.get('failure_reference')) + '. References only; verify live spread.',
                text(r.get('risk')), news_tags(r['symbol'], reviews), 'Last trade ' + cal.local_now(datetime.fromisoformat(r['last_trade'])).strftime('%H:%M:%S PKT'),
                ('Daily-volume burst · ' if r.get('burst') else '') + '15-min traded value PKR ' + fmt(r.get('window_turnover'))), unsafe_allow_html=True)
    if not details:
        return
    with st.expander('All intraday observations · including weakening and unavailable stocks'):
        st.dataframe([{'Stock': r['symbol'], 'State': r['state'] if current else 'Historical: ' + r['state'],
                       'Price': r.get('price'), 'Opening gap %': r.get('gap_pct'), 'Since open %': r.get('since_open_pct'),
                       'Last 15 min %': r.get('recent_pct'), '15-min traded value': r.get('window_turnover'),
                       'Captured day volume': r.get('day_volume'), 'Burst': r.get('burst'),
                       'Why': r.get('reason'), 'Volume comparison': r.get('volume_confirmation'),
                       'Last trade': r.get('last_trade')} for r in rows], hide_index=True)
        st.caption('Every run is archived. Repeated updates within a continuing setup are not counted as separate trades. Performance is unvalidated; no intraday profit rate is claimed.')
