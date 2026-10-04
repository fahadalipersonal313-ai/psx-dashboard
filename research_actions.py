"""Presentation and important-change signals for guarded research, not new buys."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import research_contract as contract
import research_desk
import session_calendar as cal


def action(row, desk):
    state=row['swing_state'];plan=row.get('plan')
    if state=='Swing setup for review':status='Ready for review'
    elif plan or state.startswith('Wait:') or state.startswith('Wait for'):status='Watching'
    else:status='Blocked'
    if not desk['market_open'] and status=='Ready for review':status='Watching'
    reasons=row.get('blocked_reasons',[])+row.get('missing',[])
    if status=='Ready for review':condition='Recheck spread, costs, quantity and source delay; no order or fill implied'
    elif reasons:condition='; '.join(dict.fromkeys(reasons))
    else:condition=state
    import config
    expiration=None
    if plan and desk['market_open'] and desk.get('context'):
        times=[contract.stamp(desk['context']['expires_at']),contract.stamp(desk['context']['as_of'])+timedelta(minutes=60)]
        if row.get('quote') and row['fresh_quote']:times.append(contract.stamp(row['quote']['source_as_of'])+timedelta(minutes=20))
        local=cal.local_now(contract.stamp(desk['evaluated_at']))
        times.extend(datetime.combine(local.date(),datetime.strptime(end,'%H:%M').time(),cal.PKT)
                     for start,end in cal.intervals(local.date()) if start<=local.strftime('%H:%M')<end)
        expiration=min(times).isoformat()
    return {'symbol':row['symbol'],'status':status,'condition':condition,'plan':plan,
            'entry':(plan or {}).get('observed_entry',(plan or {}).get('reference_entry')),
            'stop':(plan or {}).get('stop'),'target1':(plan or {}).get('target1'),'target2':(plan or {}).get('target2'),
            'holding_sessions':config.EXECUTION['holding_sessions'] if plan else None,
            'entry_review_due':expiration,'quote_as_of':(row.get('quote') or {}).get('source_as_of'),
            'intraday':row['intraday_state'],'investment':row['investment_state']}


def health(desk,journal=None,collection=None,now=None):
    now=now or datetime.now(timezone.utc);out=[]
    def add(key,severity,title,detail):out.append({'id':key,'severity':severity,'title':title,'detail':detail})
    if not desk['research_current']:add('research-expired','warning','Research needs a new review','Combined entry plans are withheld.')
    elif desk['market_open'] and now-contract.stamp(desk['context']['as_of'])>timedelta(minutes=60):
        add('research-session-stale','warning','Current-session research is over one hour old','Ready entries are withheld until a new sourced review.')
    def age(payload):
        try:return (now-contract.stamp((payload or {})['checked_at'])).total_seconds()
        except (KeyError,TypeError,ValueError):return None
    if desk['market_open']:
        for key,payload,limit,title in [('collection',collection,660,'Observation collection is late or unavailable'),('journal',journal,1200,'Decision checkpoint is late or unavailable')]:
            seconds=age(payload)
            if seconds is None or seconds<0 or seconds>limit:add(key+'-stale','warning',title,'Check the latest timestamp and workflow health; cadence is best-effort.')
        if collection and collection.get('current_usable_points',0)==0:
            add('no-new-quotes','warning','No usable new source points in the latest capture','Repeated or stale source updates do not qualify as fresh observations.')
    due=[r['symbol'] for r in desk['rows'] if not r['fundamentals_current']]
    if due:add('financial-due','info','Financial review due or unavailable',', '.join(due))
    missed=(collection or {}).get('missed_poll_windows',0)
    if missed:add('missed-polls','info','Observation windows were missed',str(missed)+' missed windows through the last published capture; no historical samples were invented.')
    if cal.local_now(now).date().isoformat()>='2026-11-01':
        add('calendar-maintenance','warning','Exchange-calendar maintenance is due','The verified manifest ends 31 December 2026. Thirty-session paper windows become uncovered after 18 November; extend the manifest before then.')
    for r in desk['rows']:
        review=r.get('research') or {};f=review.get('fundamentals',{})
        if f.get('event_review_required'):add(r['symbol']+'-event','warning',r['symbol']+': material-event review required',f.get('summary',''))
        for e in f.get('events',[]):
            days=(datetime.fromisoformat(e['date']).date()-cal.local_now(now).date()).days
            if 0<=days<=5:add(r['symbol']+'-'+e['kind']+'-'+e['date'],'info',r['symbol']+': '+e['kind'].replace('_',' ')+' on '+e['date'],
                            'Verified listed date. Only the stated event type is guarded; this is not a complete event calendar.')
    return out


def fingerprints(desk):
    result={}
    for r in desk['rows']:
        review=r.get('research') or {};news=review.get('news') or {};f=review.get('fundamentals') or {}
        result[r['symbol']]={'swing':r['swing_state'],'investment':r['investment_state'],
                             'company_review':news.get('summary'),'company_bias':news.get('bias'),
                             'financial_period':f.get('report_period'),'event_review_required':f.get('event_review_required'),
                             'events':f.get('events',[]),'blocked_reasons':r.get('blocked_reasons',[])}
    return result


def update_activity(desk,checkpoint_id,root='.',now=None):
    """Single writer; first observation is a baseline, not a batch of new alerts."""
    import research_paper
    now=now or datetime.now(timezone.utc);path=Path(root)/'research_activity.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    if old and old.get('schema_version')!=1:raise ValueError('Unknown research activity schema')
    if old and contract.stamp(old['checked_at'])>now:raise ValueError('Activity clock regressed')
    state=fingerprints(desk);items=list(old.get('items',[]))
    for symbol,current in state.items():
        before=old.get('state',{}).get(symbol)
        if before is None:continue
        changes=[k for k in current if before.get(k)!=current[k]]
        if not changes:continue
        item={'symbol':symbol,'recorded_at':now.isoformat(),'checkpoint_id':checkpoint_id,
              'changed_fields':changes,'before':before,'after':current,
              'title':symbol+': research conditions changed',
              'detail':'Changed '+', '.join(k.replace('_',' ') for k in changes)+'. Review the evidence before acting.'}
        item['id']=research_paper.digest(item)
        if item['id'] not in {x['id'] for x in items}:items.append(item)
    payload={'schema_version':1,'checked_at':now.isoformat(),'checkpoint_id':checkpoint_id,'state':state,'items':items[-200:],
             'retention':'Latest 200 changes. Original research decisions remain immutable in research_decisions.'}
    research_paper.atomic(path,payload);return payload


def sizing_liquidity_current(payload,row,plan,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        cutoff=cal.last_completed(now)
        return (row['liquidity']['status']=='available'
                and payload['as_of_session']==row['as_of_session']==row['liquidity']['end_session']==plan['decision_session']==cutoff
                and contract.stamp(payload['generated_at'])<=now
                and (row.get('research_guard') or {}).get('valid') is True)
    except (ValueError,TypeError,KeyError):return False


def concentration(values,capital):
    """User-entered hypothetical position values, never the actual portfolio."""
    import config
    if not research_desk.number(capital) or capital<=0:raise ValueError('Positive scenario capital is required')
    groups={}
    for symbol,value in values.items():
        if symbol not in contract.UNIVERSE or not research_desk.number(value) or value<0:
            raise ValueError('Use nonnegative finite values for the selected 15 stocks')
        group=config.SECTORS.get(symbol,'Unclassified')
        groups.setdefault(group,{'sector':group,'stocks':[],'value':0.0})
        if value:groups[group]['stocks'].append(symbol)
        groups[group]['value']+=value
        if not research_desk.number(groups[group]['value']):raise ValueError('Scenario values overflow supported precision')
    result=[]
    for group in groups.values():
        if group['value']:
            group['capital_pct']=(group['value']/capital)*100
            if not research_desk.number(group['capital_pct']):raise ValueError('Scenario percentage overflows supported precision')
            result.append(group)
    total=sum(values.values());percentage=(total/capital)*100
    if not research_desk.number(total) or not research_desk.number(percentage):raise ValueError('Scenario totals overflow supported precision')
    return {'sectors':sorted(result,key=lambda x:-x['value']),
            'total_value':total,'capital_pct':percentage,'over_capital':total>capital}
