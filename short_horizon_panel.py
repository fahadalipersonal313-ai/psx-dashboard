"""Plain-language research panel; freshness is checked on every render."""
from datetime import datetime

import pandas as pd
from session_calendar import PKT, last_completed
from short_horizon import ROOT, stamp
from short_horizon_refresh import csv_report, read_json


def show(st):
    with st.expander('Next 1–5 trading days · research watchlist', expanded=False):
        report = read_json(ROOT / 'short_horizon_latest.json', {})
        status = read_json(ROOT / 'short_horizon_status.json', {})
        if not report:
            st.info('The research watchlist has not refreshed yet. Existing engine signals remain available below.')
            return
        now = datetime.now(PKT)
        try:
            age = (now - stamp(report['as_of'])).total_seconds() / 3600
            stale = age < 0 or age > report['config']['universe_max_age_hours'] or report['market_session'] != last_completed(now)
        except (KeyError, ValueError):
            stale = True
        failed = status.get('ok') is False or status.get('input_sha256') != report.get('input_sha256')
        if stale or failed:
            st.error('Historical view only: refresh is overdue or failed. Do not treat these rows as current candidates.')
            if status.get('error'):
                st.caption(status['error'])
        st.caption(f"Prices: completed session {report['market_session']} · Review: {report['as_of']} · Pakistan time · {report['method']}")
        st.info('A research shortlist, not a buy instruction. Scores are comparison points, not chances of profit. Live spread, current price and event outcomes still need checking.')
        cols = st.columns(3)
        cols[0].metric('Stocks screened', report['eligible_count'])
        cols[1].metric('Shown', len(report['rows']))
        cols[2].metric('Momentum candidates', 0 if stale or failed else sum(r['classification'] == 'Momentum candidate' for r in report['rows']))
        for warning in report.get('warnings', []):
            st.warning(warning)
        choices = ['All'] + sorted({r['classification'] for r in report['rows']})
        choice = st.selectbox('Show', choices, key='short_horizon_group')
        selected = [r for r in report['rows'] if choice == 'All' or r['classification'] == choice]
        labels = {'rank': 'Rank', 'symbol': 'Stock', 'company': 'Company', 'price': 'Close (PKR)',
                  'change': 'Daily change (%)', 'volume': 'Shares traded', 'market_cap': 'Company value (PKR)',
                  'pe': 'P/E (period unconfirmed)', 'sector': 'Sector', 'classification': 'Research status', 'score': 'Points / 100'}
        table = pd.DataFrame([{labels[k]: r.get(k) for k in labels} for r in selected])
        st.dataframe(table, use_container_width=True, hide_index=True)
        if selected:
            symbol = st.selectbox('Read a stock’s evidence', [r['symbol'] for r in selected], key='short_horizon_stock')
            row = next(r for r in selected if r['symbol'] == symbol)
            st.write(' · '.join(row['reasons']))
            st.write('Confirmation: ' + row['confirmation'])
            st.write('Review again: ' + row['failure'])
            st.caption(f"Five-session high: {row.get('confirmation_reference', 'Unavailable')} PKR · Five-session low: {row.get('failure_reference', 'Unavailable')} PKR. These are reference levels, not targets or guaranteed stops.")
            st.write(row['news']['status'])
            for item in row['news']['items'] + row['news']['not_used']:
                st.write(f"{item.get('assessment', 'Unrated')}: {item.get('rationale', '')}")
                st.caption(f"Published: {item.get('published_at')} · Event: {item.get('event_date')} · {item.get('unusable_reason', item.get('quality'))}")
                st.write(item.get('source', 'Source unavailable'))
            f = row['financials']
            st.caption(f"Earnings context: {f['status']}. Annual period: {f.get('annual_period', 'Unknown')}; annual growth: {f.get('annual_growth_percent', 'Unknown')}%; trailing growth: {f.get('ttm_growth_percent', 'Unknown')}%. Trailing period end unverified; no earnings points added.")
        st.write('Changes since the previous snapshot')
        st.write(report['changes'])
        st.download_button('Download this watchlist (CSV)', csv_report(report),
                           file_name=f"psx-research-{report['market_session']}.csv", mime='text/csv')
        for note in report['limitations']:
            st.caption(note)
