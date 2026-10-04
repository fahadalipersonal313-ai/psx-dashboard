"""Plain-language action board and explicitly unverified prospective scorecard."""
from datetime import datetime,timezone
import research_actions as actions
import research_desk


def show_overview(st,desk,journal,collection,comparisons,paper,activity):
    st.markdown('### What can I do now?')
    rows=[actions.action(r,desk) for r in desk['rows']]
    counts={k:sum(r['status']==k for r in rows) for k in ('Ready for review','Watching','Blocked')}
    for col,(label,count) in zip(st.columns(3),counts.items()):col.metric(label,count)
    st.caption('Ready means the existing research and price checks pass for review. It does not establish an executable price, suitable quantity or assured result. Intraday remains watch-only.')
    tabs=st.tabs(['Actions','Morning brief','Paper scorecard','Compare stocks','Important changes'])
    with tabs[0]:
        selected=st.radio('Show setups',['All 15','Ready for review','Watching','Blocked'],horizontal=True,key='research_action_filter')
        order={'Ready for review':0,'Watching':1,'Blocked':2}
        shown=sorted((r for r in rows if selected=='All 15' or r['status']==selected),key=lambda r:(order[r['status']],r['symbol']))
        def explain_selected():
            state=st.session_state.get('research_action_table',{})
            symbol=selected_stock(shown,state)
            if symbol is not None:st.session_state['combined_research_symbol']=symbol
        st.dataframe([{'Stock':r['symbol'],'Status':r['status'],'Next condition / reason':r['condition'],
                       'Entry reference':r['entry'],'Stop':r['stop'],'Target 1':r['target1'],'Target 2':r['target2'],
                       'Max holding sessions':r['holding_sessions'],
                       'Recheck by (PKT)':research_desk.pkt(r['entry_review_due']) if r['entry_review_due'] else 'New session / new valid review required',
                       'Intraday':r['intraday'],'Long term':r['investment']} for r in shown],hide_index=True,width='stretch',
                     key='research_action_table',on_select=explain_selected,selection_mode='single-row')
        st.caption('Select a stock row to open its chart, two-month planner and source-linked checks below. Reference levels can be conditional while Watching. Only a new, valid quote in the frozen entry zone can support an entry review; all values are PKR. Detailed evidence is below.')
    with tabs[1]:
        import research_brief
        research_brief.show(st,research_brief.build(desk,activity,now=research_actions_time(desk)))
    with tabs[2]:show_paper(st,paper)
    with tabs[3]:
        show_comparisons(st,comparisons)
        show_concentration(st)
    with tabs[4]:
        for item in actions.health(desk,journal,collection):
            getattr(st,'warning' if item['severity']=='warning' else 'info')(item['title']+': '+item['detail'])
        recent=(activity or {}).get('items',[])
        if not recent:st.caption('No recorded research-condition changes yet. The initial state is a baseline, not a new alert.')
        for item in reversed(recent[-20:]):
            st.write(research_desk.pkt(item['recorded_at'])+' · '+item['title'])
            st.caption(item['detail'])
            with st.expander('Before and after · '+item['symbol']+' · '+item['id'][:8]):
                st.json({'before':item['before'],'after':item['after']})
        st.caption('These alerts stay inside the dashboard. A changed review is not necessarily a new company announcement; source evidence is shown in the stock detail.')


def show_paper(st,paper):
    st.markdown('#### Prospective paper scorecard')
    if not paper:
        st.info('The candidate ledger is installed. Waiting for its first runtime checkpoint. Historical signals will not be enrolled retrospectively.')
        return
    counts=paper.get('counts',{})
    for blocker in paper.get('enrollment_blockers',[]):
        st.warning(blocker['symbol']+': candidate enrollment withheld: '+blocker['reason'])
    columns=st.columns(4)
    for col,label,value in zip(columns,['Frozen candidates','Awaiting later quote','Entry condition observed','Verified fills'],
                              [paper['candidate_count'],counts.get('awaiting_later_observation',0),paper.get('observed_entry_count',0),paper['verified_fills']]):col.metric(label,value)
    st.caption('Ledger checked '+research_desk.pkt(paper['checked_at'])+' · '+paper['version'])
    st.warning('A later observed entry condition is not a filled trade. Point samples cannot establish stop-versus-target order between observations. Win rate, average net gain/loss and drawdown remain unavailable.')
    st.dataframe([{'Status':k.replace('_',' '),'Count':v} for k,v in counts.items()],hide_index=True,width='stretch')
    cases=paper.get('cases',[])
    if cases:
        st.dataframe([{'Stock':c['symbol'],'Recorded (PKT)':research_desk.pkt(c['recorded_at']),
                       'Status':c['status'].replace('_',' '),'Entry condition expires':research_desk.pkt(c['entry_expires_at']),
                       'Observation deadline':c['holding_deadline_session'],
                       'Last observed condition':(c.get('latest_event') or {}).get('observed_level',''),
                       'Candidate ID':c['candidate_id'][:12]} for c in cases[-100:]],hide_index=True,width='stretch')
    st.caption('Recording time is the engine timestamp, not a verified dashboard publication time. Repeated checkpoints do not create extra opportunities. Expired, cancelled and unresolved calls remain counted. Fee/slippage scenarios below do not change this ledger or create performance history.')


def show_comparisons(st,payload):
    st.markdown('#### Measured completed-session comparisons')
    if not payload:
        st.info('Waiting for a validated comparison snapshot. No sector-index coverage is assumed.');return
    import session_calendar as cal
    if payload.get('as_of_session')!=cal.last_completed():
        st.warning('Comparison data is older than the required completed session. Treat these figures as historical; current sizing is withheld.')
    # The adapter normalizes the independent comparison module without inventing missing metrics.
    st.caption('Comparison cutoff: '+str(payload.get('as_of_session','unavailable'))+
               ' · raw stock price changes; reported KSE100 index-level changes. Price changes exclude cash distributions.')
    data=[]
    for row in payload.get('rows',payload.get('stocks',[])):
        r={'Stock':row['symbol'],'Sector':row.get('sector'),'Status':row.get('status')}
        for n in ('5','20'):
            metric=row.get('returns',{}).get(n,{})
            peer=row.get('sector_peers',{}).get(n,{})
            r[n+' sessions stock %']=metric.get('stock_change_pct')
            r[n+' sessions vs KSE100 pp']=metric.get('difference_pp')
            r[n+' sessions vs tracked peers pp']=peer.get('stock_minus_peers_pp')
        data.append(r)
    st.dataframe(data,hide_index=True,width='stretch')
    st.caption('Peer means use only the selected 15-stock universe, exclude the stock itself, and require at least two other fully covered peers. This is not an official sector index or a total-return comparison.')
    with st.expander('Comparison coverage, membership and source basis'):st.json(payload)


def show_sizing(st,row,comparisons):
    import research_sizing
    st.markdown('#### Position-size and net-cost scenario')
    p=row.get('plan')
    if not p:
        st.caption('A currently valid conditional swing plan is required before risk sizing. The current combined call has no eligible levels.');return
    candidates=(comparisons or {}).get('rows',(comparisons or {}).get('stocks',[]))
    comparison=next((v for v in candidates if v['symbol']==row['symbol']),{})
    liquidity=comparison.get('liquidity',{})
    import session_calendar as cal
    if not actions.sizing_liquidity_current(comparisons,comparison,p):
        st.info('Quantity is withheld until validated recent volume coverage is available.');return
    st.caption('Session-local inputs only; amounts are PKR. Costs and slippage are assumptions you supply, not verified broker charges. Gaps and unavailable liquidity can cause losses above the modeled stop loss.')
    with st.form('research_sizing_'+row['symbol']):
        cols=st.columns(3)
        capital=cols[0].number_input('Capital',min_value=0.0,value=0.0,step=10000.0)
        cash=cols[1].number_input('Available cash',min_value=0.0,value=0.0,step=10000.0)
        budget=cols[2].number_input('Modeled loss budget',min_value=0.0,value=0.0,step=1000.0)
        cols=st.columns(3)
        fee=cols[0].number_input('Estimated fee per side (basis points)',min_value=0.0,max_value=9999.0,value=0.0,step=1.0)
        entry_slip=cols[1].number_input('Adverse entry slippage (basis points)',min_value=0.0,max_value=9999.0,value=0.0,step=1.0)
        exit_slip=cols[2].number_input('Adverse exit slippage (basis points)',min_value=0.0,max_value=9999.0,value=0.0,step=1.0)
        cols=st.columns(3)
        fixed=cols[0].number_input('Additional round-trip fixed costs',min_value=0.0,value=0.0,step=1.0)
        cap=cols[1].number_input('Position cap (% of capital)',min_value=0.0,max_value=100.0,value=10.0,step=1.0)
        participation=cols[2].number_input('Max share of median daily volume (%)',min_value=0.0,max_value=5.0,value=0.1,step=0.1)
        lot=st.number_input('Lot size assumption (verify with broker)',min_value=1,value=1,step=1)
        acknowledged=st.checkbox('I have entered the intended fee, tax, spread and slippage assumptions; zero values intentionally exclude them.')
        submitted=st.form_submit_button('Calculate scenario')
    if not submitted:return
    if not acknowledged:st.warning('Confirm the cost assumptions before calculating.');return
    entry=p.get('observed_entry',p['reference_entry'])
    try:
        result=research_sizing.size_scenario(entry,p['stop'],p['target1'],capital,cash,budget,fee,entry_slip,exit_slip,fixed,cap,
                                            liquidity.get('median20_volume_shares'),participation,lot)
    except ValueError as exc:st.warning(str(exc));return
    st.write('Scenario quantity: '+str(result.get('shares'))+' shares · reserved cash: '+str(result.get('cash_required'))+
             ' · modeled stop loss: '+str(result.get('modeled_stop_loss'))+' · target net gain: '+str(result.get('target_net_gain'))+
             ' · net reward/risk: '+str(result.get('net_reward_risk')))
    st.caption('Binding constraints: '+', '.join(result.get('binding_constraints',[]))+'. Daily-volume capacity is a conservative screen, not evidence of immediate available depth. No portfolio holdings or order is created.')
    with st.expander('Scenario assumptions and calculation'):st.json(result)


def show_concentration(st):
    import research_contract as contract
    import config
    import pandas as pd
    with st.expander('Check a proposed basket for sector concentration'):
        st.caption('Enter hypothetical position values only. This does not read, save or change your actual portfolio. Sector grouping follows the existing engine classification.')
        with st.form('research_concentration'):
            capital=st.number_input('Basket scenario capital (PKR)',min_value=0.0,value=0.0,step=10000.0)
            values=st.data_editor(pd.DataFrame([{'Stock':s,'Sector':config.SECTORS.get(s,'Unclassified'),'Proposed value PKR':0.0} for s in contract.UNIVERSE]),
                                 disabled=['Stock','Sector'],hide_index=True,key='research_basket_inputs',width='stretch')
            submitted=st.form_submit_button('Check concentration')
        if submitted:
            try:result=actions.concentration(dict(zip(values['Stock'],values['Proposed value PKR'])),capital)
            except ValueError as exc:st.warning(str(exc));return
            if result['over_capital']:st.warning('Proposed position values exceed scenario capital.')
            st.dataframe([{'Sector':r['sector'],'Stocks':', '.join(r['stocks']),'Value PKR':round(r['value'],2),'Capital %':round(r['capital_pct'],2)} for r in result['sectors']],hide_index=True,width='stretch')
            st.caption('Total proposed allocation: '+str(round(result['capital_pct'],2))+'% of capital. Sector concentration alone does not measure covariance, diversification or portfolio drawdown.')


def research_actions_time(desk):
    import research_contract
    return research_contract.stamp(desk['evaluated_at'])


def selected_stock(shown,state):
    """Map a selection against the actual displayed ordering, never all stocks."""
    try:
        rows=state['selection']['rows']
        index=rows[0]
        if type(index) is not int or not 0<=index<len(shown):return None
        return shown[index]['symbol']
    except (KeyError,TypeError,IndexError):return None
