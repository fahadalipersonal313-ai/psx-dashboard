"""Source-linked two-calendar-month scenarios, never a forecast or trade journal."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import config
import decision_engine
import research_contract as contract
import research_desk as desk
import research_calendar
import session_calendar as cal

SNAPSHOT_URL='https://github.com/fahadalipersonal313-ai/psx-engine/blob/runtime-state/dashboard_snapshot.json'
CONTEXT_URL='https://github.com/fahadalipersonal313-ai/psx-engine/blob/research-state/research_context.json'


def add_months(day,months=2):
    year,month=divmod(day.year*12+day.month-1+months,12);month+=1
    return date(year,month,min(day.day,monthrange(year,month)[1]))


def canonical(row,context,now):
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Planner evaluation time needs a timezone')
    try:
        contract.validate(context)
        if any(contract.stamp(context[k])>now for k in ('as_of','generated_at')):
            context=None
        elif any(contract.stamp(source['verified_at'])>now or (source.get('published_at') and contract.stamp(source['published_at'])>now) for source in context['sources']):
            context=None
    except (ValueError,TypeError,KeyError,OverflowError):context=None
    symbol=row.get('symbol')
    if symbol not in contract.UNIVERSE:raise ValueError('Stock is outside the approved research universe')
    result=desk.build(context,{'rows':[row['technical']] if row.get('technical') else []},
                      {'prices':[row['quote']] if row.get('quote') else [],
                       'observations':[row['observation']] if row.get('observation') else []},now)
    return next(r for r in result['rows'] if r['symbol']==symbol),result


def _links(context,ids):
    lookup={s['id']:s for s in (context or {}).get('sources',[])}
    return [dict(lookup[s]) for s in dict.fromkeys(ids) if s in lookup]


def _artifact(title,url,**metadata):return {'title':title,'url':url,'kind':'published_research_artifact','observed_metadata':metadata}


def checks(row,context,now):
    """Explain actual existing gates without silently changing their weights."""
    row,state=canonical(row,context,now);context=state['context'];out=[]
    t=row.get('technical') or {};review=row.get('research') or {};q=row.get('quote') or {}
    techsource=[_artifact('Latest published technical snapshot (branch may advance)',SNAPSHOT_URL,
                          run_time=t.get('run_time'),decision_session=t.get('decision_session'),
                          strategy_version=t.get('strategy_version'),snapshot_hash=t.get('snapshot_hash'),config_hash=t.get('config_hash'))]
    ctxsource=[_artifact('Latest published research context (branch may advance)',CONTEXT_URL,
                         as_of=(context or {}).get('as_of'),generated_at=(context or {}).get('generated_at'),expires_at=(context or {}).get('expires_at'))]
    quote_source=[_artifact('Official REG quote page','https://dps.psx.com.pk/company/'+row['symbol'])]
    def add(name,status,detail,horizon='Swing',sources=None):
        out.append({'check':name,'status':status,'detail':detail,'horizon':horizon,'sources':sources or []})
    add('Research review and expiry','Pass' if state['research_current'] else 'Fail',
        ('Research cutoff '+str((context or {}).get('as_of'))+'; expires '+str((context or {}).get('expires_at'))),
        'Combined research',ctxsource)
    expected=decision_engine.digest(decision_engine.contract())
    rules=t.get('strategy_version')==config.STRATEGY_VERSION and t.get('config_hash')==expected
    add('Active technical rules','Pass' if rules else 'Fail',
        'Observed '+str(t.get('strategy_version'))+'; required '+config.STRATEGY_VERSION+' with exact configuration hash.',sources=techsource)
    try:session_ok=t['decision_session']==cal.last_completed(now) and contract.stamp(t['run_time'])<=now
    except (ValueError,TypeError,KeyError):session_ok=False
    add('Completed-session cutoff','Pass' if session_ok else 'Fail',
        'Observed '+str(t.get('decision_session'))+'; required '+cal.last_completed(now)+'. Source time is not the dashboard fetch time.',sources=techsource)
    guard=t.get('research_guard') or {}
    binding={k:t.get(k) for k in ('symbol','decision_session','strategy_version','config_hash','snapshot_hash')}
    audit=guard.get('valid') is True and guard.get('binding')==binding
    add('History, corporate actions and input binding','Pass' if audit else 'Fail',
        '; '.join(guard.get('checks',[])) or ('Current audit matches the technical snapshot.' if audit else 'Bound input audit is unavailable.'),
        sources=techsource+[_artifact('Exchange-calendar source',u) for u in guard.get('calendar_sources',[])])
    technical=t.get('signal') in ('Buy','Strong Buy') and t.get('data_quality')=='good'
    add('Technical signal classification and data quality','Pass' if technical else 'Fail',
        str(t.get('signal','Unavailable'))+'; quality '+str(t.get('data_quality','unavailable'))+'. This checks the stored classification and quality fields only.',sources=techsource)
    numeric,why=desk.technical_plan(t,now)
    add('Guarded numeric technical plan','Pass' if numeric else 'Fail',why or 'Current bound technical plan and ordered price levels pass validation.',sources=techsource)
    for category in ('macro','geopolitical'):
        items=[v for v in (context or {}).get('market_context',[]) if v['category']==category]
        available=any(v['status']=='available' for v in items)
        adverse=any(v['status']=='available' and v['bias']=='adverse' for v in items)
        add(category.capitalize()+' evidence','Fail' if adverse else 'Pass' if available else 'Unavailable',
            '; '.join(v['summary'] for v in items) or 'No sourced review available.',
            sources=_links(context,[sid for v in items for sid in v['source_ids']]))
    for key in ('news','sector'):
        item=review.get(key,{})
        status='Unavailable' if not item or item.get('status')=='unavailable' else 'Fail' if item.get('bias')=='adverse' else 'Pass'
        add(key.capitalize()+' review',status,item.get('summary','No sourced review available.'),sources=_links(context,item.get('source_ids',[])))
    reviewed=(contract.stamp(context['as_of']) if context else None)
    active_current=bool(reviewed and state['research_current'] and now-reviewed<=timedelta(minutes=60))
    add('Current-session research freshness','Pass' if active_current and state['market_open'] else 'Wait',
        'Active entries require a sourced review within 60 minutes. '+('Market closed: only conditional reference plans can be reviewed.' if not state['market_open'] else 'Research cutoff '+str(reviewed)),
        sources=ctxsource)
    add('Current delayed quote','Pass' if row['fresh_quote'] else 'Wait',
        'Source update '+str(q.get('source_as_of'))+'; fetched '+str(q.get('fetched_at'))+'. '+
        ('Market is closed; no current entry is implied.' if not state['market_open'] else 'Valid REG provenance, source age and collection-quality checks are required.'),sources=quote_source)
    add('Combined entry condition','Pass' if row['swing_state']=='Swing setup for review' else 'Wait' if row['plan'] else 'Fail',
        row['swing_state']+'. '+('; '.join(dict.fromkeys(row['blocked_reasons']+row['missing']))),
        sources=ctxsource+techsource+quote_source)
    f=review.get('fundamentals',{})
    add('Financial review','Pass' if row['fundamentals_current'] else 'Unavailable',
        'Period '+str(f.get('report_period'))+'; reviewed '+str(f.get('reviewed_at'))+'; next review '+str(f.get('next_review_at'))+
        '. Material-event flag '+str(f.get('event_review_required'))+'. '+f.get('summary',''),
        'Investment review freshness',_links(context,f.get('source_ids',[])))
    risk=f.get('event_review_required') or (f.get('status')=='available' and f.get('bias')=='adverse')
    add('Fundamental and material-event risk','Fail' if risk else 'Pass' if f.get('status')=='available' else 'Unavailable',
        f.get('summary','No financial risk review.')+' Material-event review required: '+str(bool(f.get('event_review_required'))),
        'Combined material-risk screen',_links(context,f.get('source_ids',[])))
    swing=review.get('horizons',{}).get('swing',{})
    add('Swing research stance','Pass' if swing.get('stance') in ('watch','supportive') else 'Fail',
        str(swing.get('stance','unavailable'))+': '+swing.get('rationale','No reviewed swing stance.'),
        sources=_links(context,swing.get('source_ids',[])))
    events=f.get('events')
    blackout=[e for e in events or [] if (e['kind']=='earnings' and 0<=(date.fromisoformat(e['date'])-cal.local_now(now).date()).days<=5)
              or (e['kind']=='corporate_action' and 0<=(date.fromisoformat(e['date'])-cal.local_now(now).date()).days<=1)]
    add('Listed event-date checks','Fail' if blackout else 'Partial',
        ('Known entry blackout: '+', '.join(e['kind']+' '+e['date'] for e in blackout)+'. ' if blackout else '')+
        ('No dated-event array in the review.' if events is None else str(len(events))+' verified listed dates checked.')+
        ' This is not a complete issuer-event calendar.',
        sources=_links(context,[sid for e in events or [] for sid in e['source_ids']]))
    public=review.get('public_sentiment',{})
    add('Independent public sentiment','Available' if public.get('status')=='available' else 'Unavailable',
        public.get('summary','No independent public-post evidence.')+' This coverage item is not a positive signal or an additional mandatory swing gate.',
        'Coverage only',_links(context,public.get('source_ids',[])))
    return out


def build(row,context,now=None):
    now=now or datetime.now(timezone.utc);row,state=canonical(row,context,now);context=state['context']
    start=cal.local_now(now).date();end=add_months(start,2);review=row.get('research') or {};f=review.get('fundamentals') or {}
    events=[];event_keys=set()
    for e in f.get('events',[]):
        day=date.fromisoformat(e['date'])
        if start<=day<=end and contract.stamp(e['known_at'])<=now and (e['kind'],e['date']) not in event_keys:
            event_keys.add((e['kind'],e['date']))
            events.append({**e,'sources':_links(context,e['source_ids']),
                           'date_precision':'date only; event time is not represented in this record',
                           'review_current':state['research_current'],'calendar_covered':research_calendar.FROM<=day<=research_calendar.THROUGH})
    events.sort(key=lambda e:(e['date'],e['kind']))
    p=row['plan'];reasons=list(dict.fromkeys(row['blocked_reasons']+row['missing']))
    suggestions=[];day=start+timedelta(days=7)
    while day<=end:
        suggestions.append({'date':day.isoformat(),'kind':'suggested research review','reason':'Recheck thesis, source quality, price basis and newly verified company events; not a scheduled automation or issuer catalyst.'})
        day+=timedelta(days=7)
    return {'symbol':row['symbol'],'planning_start':start.isoformat(),'planning_end':end.isoformat(),
            'planning_basis':'Two calendar months of scenario review; not a forecast or a 60-session trade',
            'trade_holding_sessions':config.EXECUTION['holding_sessions'],
            'calendar_warning':None if end<=research_calendar.THROUGH else 'Planning range extends beyond the verified exchange calendar; future trading-session deadlines are not inferred.',
            'evaluated_at':now.isoformat(),'research_as_of':(context or {}).get('as_of'),
            'research_expires_at':(context or {}).get('expires_at'),'research_current':state['research_current'],
            'state':row['swing_state'],'plan':p,'thesis':review.get('thesis','No validated research thesis available.'),
            'countercase':review.get('countercase','No validated countercase available.'),
            'constructive_scenario':'Revisit only while the sourced thesis remains supported, required risk checks pass, and a fresh delayed quote meets the existing technical entry conditions.',
            'invalidation':reasons or ['Thesis deterioration, new material information or failure of the original technical/data checks requires a fresh review.'],
            'events':events,'event_coverage':'Only verified listed dates; no event listed does not mean no event is scheduled.',
            'fundamentals':{'period':f.get('report_period'),'reviewed_at':f.get('reviewed_at'),'next_review_at':f.get('next_review_at'),
                            'event_review_required':f.get('event_review_required'),'triggers':f.get('event_triggers',[])},
            'suggested_reviews':suggestions,'checks':checks(row,context,now)}


def show_checks(st,items):
    st.dataframe([{'Check':x['check'],'Scope':x['horizon'],'Result':x['status'],'Explanation':x['detail']} for x in items],hide_index=True,width='stretch')
    for item in items:
        with st.expander(item['status']+' · '+item['check']):
            st.write(item['detail'])
            if not item['sources']:st.caption('No attributable source is available for this check.')
            seen=set()
            for source in item['sources']:
                if source['url'] in seen:continue
                seen.add(source['url']);st.link_button(source['title'],source['url'])
                if source.get('observed_metadata'):
                    st.caption('Observed evidence identifiers below. The linked branch can advance after this view; it is not an immutable copy of these inputs.')
                    st.json(source['observed_metadata'])
                if source.get('verified_at'):st.caption('Source verified '+desk.pkt(source['verified_at'])+' · published '+str(source.get('published_at') or source.get('published_date') or 'time unknown'))


def show(st,row,context,database=None,now=None):
    now=now or datetime.now(timezone.utc);plan=build(row,context,now)
    st.markdown('### '+plan['symbol']+' · chart and two-month scenarios')
    st.caption('This rolling research window starts today; original published decisions retain their original dates and limits.')
    st.caption(plan['planning_start']+' to '+plan['planning_end']+' · two calendar months. The separate swing rule still holds for at most '+str(plan['trade_holding_sessions'])+' sessions; this page does not extend a trade or predict a return.')
    tabs=st.tabs(['Annotated daily chart','Two-month planner','Why this status?'])
    with tabs[0]:
        import research_charts
        history=research_charts.read_history(database,row['symbol'],cal.last_completed(now))
        prepared=research_charts.prepare(row,context,history,now)
        research_charts.show(st,prepared)
    with tabs[1]:
        if not plan['research_current']:st.warning('This is a dated planning record. A current sourced review is required before a combined entry plan can be shown.')
        st.caption('Research cutoff '+desk.pkt(plan['research_as_of'])+' · expires '+desk.pkt(plan['research_expires_at']))
        if plan['calendar_warning']:st.warning(plan['calendar_warning'])
        cols=st.columns(2)
        cols[0].markdown('**Thesis and constructive scenario**');cols[0].write(plan['thesis']);cols[0].write(plan['constructive_scenario'])
        cols[1].markdown('**Countercase and invalidation**');cols[1].write(plan['countercase'])
        for text in plan['invalidation']:cols[1].write('• '+text)
        st.write('Current entry condition: '+plan['state'])
        p=plan['plan']
        if p:
            st.write('Guarded conditional zone PKR '+str(p['buy_zone_low'])+'–'+str(p['buy_zone_high'])+
                     ' · stop '+str(p['stop'])+' · target 1 '+str(p['target1'])+' · target 2 '+str(p['target2']))
            st.caption('These are the current technical plan’s conditional levels, not two-month forecasts. Quote, costs, liquidity and event-risk checks still apply; execution is unverified.')
        else:st.info('Numeric entry, stop and targets are withheld for this combined call.')
        st.markdown('**Verified listed issuer dates**' if plan['research_current'] else '**Issuer dates in the saved review: recheck before use**')
        if not plan['events']:st.caption('No verified issuer dates are listed inside this planning window. Unknown dates are not filled in.')
        for event in plan['events']:
            st.write(event['date']+' · '+event['kind'].replace('_',' '))
            st.caption('Known by '+desk.pkt(event['known_at'])+' · '+event['date_precision'])
            if not event['review_current']:st.caption('Dated evidence from an expired review; later changes have not been verified.')
            for source in event['sources']:st.link_button(source['title'],source['url'])
        st.caption(plan['event_coverage'])
        f=plan['fundamentals']
        st.markdown('**Research review dates, not company catalysts**')
        st.write('Financial period: '+str(f['period'])+' · financial review due '+desk.pkt(f['next_review_at']))
        st.write('Refresh after: '+('; '.join(f['triggers']) if f['triggers'] else 'new verified material information'))
        st.caption('Suggested weekly review dates: '+', '.join(x['date'] for x in plan['suggested_reviews'])+'. These are planning suggestions; no new reminders or schedules were created.')
    with tabs[2]:show_checks(st,plan['checks'])
    return plan
