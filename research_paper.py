"""Prospective condition ledger, deliberately not an executable-fill simulator.

Only calls first recorded after installation are enrolled. Immutable events
rebuild every counter. Delayed point samples cannot resolve intervening paths;
therefore this version never reports a trade win, net P&L, or equity drawdown.
"""
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path

import research_contract as contract
import research_desk
import research_calendar
import session_calendar as cal

VERSION = 'prospective-conditions-v1'
TERMINAL = {'expired_unactivated','cancelled_before_entry','unresolved_level_touch',
            'research_invalidated_after_condition','observation_window_ended'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8');tmp.replace(path)


def append(root, event):
    event={**event,'version':VERSION};eid=digest(event);event['id']=eid
    path=Path(root)/'paper_events'/event['recorded_at'][:10]/(eid+'.json')
    if path.exists():
        if read(path)!=event:raise ValueError('Immutable paper event conflict')
    else:atomic(path,event)
    return event


def load(root):
    events=[]
    for path in (Path(root)/'paper_events').rglob('*.json'):
        event=read(path);original=dict(event);eid=original.pop('id')
        if digest(original)!=eid or event.get('version')!=VERSION:raise ValueError('Invalid immutable paper event')
        contract.stamp(event['recorded_at']);events.append(event)
    return sorted(events,key=lambda e:(contract.stamp(e['recorded_at']),e['sequence'],e['id']))


def fold(events):
    cases={}
    for e in events:
        cid=e['candidate_id']
        if e['kind']=='created':
            if cid in cases or e.get('sequence')!=0 or e.get('previous_event_id') is not None:
                raise ValueError('Duplicate or invalid prospective candidate')
            cases[cid]={**e,'status':'awaiting_later_observation','events':[e['id']]}
        else:
            if cid not in cases:raise ValueError('Orphan paper event')
            case=cases[cid]
            if (e.get('sequence')!=len(case['events']) or e.get('previous_event_id')!=case['events'][-1]
                    or case['status'] in TERMINAL):raise ValueError('Invalid paper lifecycle sequence')
            allowed=({'entry_condition_observed','expired_unactivated','cancelled_before_entry'}
                     if case['status']=='awaiting_later_observation' else
                     {'unresolved_level_touch','research_invalidated_after_condition','observation_window_ended'})
            if e['kind'] not in allowed:raise ValueError('Invalid paper lifecycle transition')
            case['events'].append(e['id']);case['status']=e['kind'];case['latest_event']=e
            if e['kind']=='entry_condition_observed':case['entry_observation']=e['observation']
    return cases


def deadline(now, sessions):
    day=cal.local_now(now).date();count=0
    while count<sessions:
        day+=timedelta(days=1)
        if day>research_calendar.THROUGH:raise ValueError('Holding window exceeds verified exchange calendar')
        if cal.intervals(day):count+=1
    return day.isoformat()


def expiry(context, now):
    local=cal.local_now(now)
    ends=[datetime.combine(local.date(),datetime.strptime(end,'%H:%M').time(),cal.PKT)
          for start,end in cal.intervals(local.date()) if start<=local.strftime('%H:%M')<end]
    if not ends:return now
    return min(now+timedelta(minutes=15),contract.stamp(context['expires_at']),
               contract.stamp(context['as_of'])+timedelta(minutes=60),ends[0])


def observation(q):
    return {k:q.get(k) for k in ('symbol','price','source_as_of','fetched_at','source_url','market','day_volume')}


def update(desk, checkpoint_id, root='.', now=None):
    """Append at most one lifecycle event per existing case per current checkpoint.

    A later entry condition requires source_as_of AND fetched_at after creation.
    No historical journal replay, reconstructed candle or execution price is used.
    """
    now=now or datetime.now(timezone.utc)
    events=load(root);cases=fold(events)
    if events and now<max(contract.stamp(e['recorded_at']) for e in events):raise ValueError('Paper clock regressed')
    rows={r['symbol']:r for r in desk['rows']}
    def emit(cid,kind,**extra):
        prior=[e for e in events if e['candidate_id']==cid]
        e=append(root,{'candidate_id':cid,'kind':kind,'recorded_at':now.isoformat(),'checkpoint_id':checkpoint_id,
                       'sequence':len(prior),'previous_event_id':prior[-1]['id'] if prior else None,**extra})
        events.append(e)
    for cid,case in cases.items():
        if case['status'] in TERMINAL:continue
        row=rows.get(case['symbol']);q=(row or {}).get('quote') or {}
        created=contract.stamp(case['recorded_at'])
        pending=case['status']=='awaiting_later_observation'
        if pending and now>=contract.stamp(case['entry_expires_at']):
            emit(cid,'expired_unactivated',reason='No verified later entry-condition observation before frozen expiry');continue
        if not pending and cal.local_now(now).date().isoformat()>case['holding_deadline_session']:
            emit(cid,'observation_window_ended',reason='Frozen observation window ended; execution and return remain unverified');continue
        if not row or row['blocked_reasons'] or row.get('missing') or not desk['research_current']:
            emit(cid,'cancelled_before_entry' if pending else 'research_invalidated_after_condition',
                 reason='Research withdrawn, unavailable or blocked; this is not an executed exit',
                 reasons=(row or {}).get('blocked_reasons',[])+(row or {}).get('missing',[]));continue
        if not research_desk.fresh_quote(q,now):continue
        at=contract.stamp(q['source_as_of']);fetched=contract.stamp(q['fetched_at'])
        if not at>created or not fetched>created:continue
        p=case['frozen_plan']
        if pending:
            if row['swing_state']!='Swing setup for review':continue
            if at>=contract.stamp(case['entry_expires_at']):continue
            if not (p['buy_zone_low']<=q['price']<=p['buy_zone_high'] and p['stop']<q['price']<p['target1']):continue
            if (p['target1']-q['price'])/(q['price']-p['stop'])<2:continue
            emit(cid,'entry_condition_observed',observation=observation(q),
                 reason='Frozen condition seen at a later delayed point; no fill or position assumed',execution_verified=False)
        else:
            entry=case.get('entry_observation')
            if not entry or at<=contract.stamp(entry['source_as_of']):continue
            if q['price']<=p['stop'] or q['price']>=p['target1']:
                emit(cid,'unresolved_level_touch',observation=observation(q),
                     observed_level='stop' if q['price']<=p['stop'] else 'target1',
                     reason='Level observed; intervening stop/target sequence and execution are unknown',execution_verified=False)
    cases=fold(events)
    occupied={c['symbol'] for c in cases.values() if c['status'] not in TERMINAL}
    seen={c['setup_key'] for c in cases.values()}
    enrollment_blockers=[]
    for row in desk['rows']:
        if row['swing_state']!='Swing setup for review' or not row['plan'] or row['symbol'] in occupied:continue
        t=row['technical'];p=row['plan'];context=desk['context']
        setup_key=digest({k:t.get(k) for k in ('symbol','decision_session','strategy_version','config_hash','snapshot_hash')})
        if setup_key in seen:continue
        if not all(research_desk.number(p.get(k)) for k in ('buy_zone_low','buy_zone_high','stop','target1')):continue
        import config
        holding=int(config.EXECUTION['holding_sessions'])
        try:end=deadline(now,holding)
        except ValueError as exc:
            enrollment_blockers.append({'symbol':row['symbol'],'reason':str(exc)});continue
        until=expiry(context,now)
        if until<=now:continue
        cid=digest({'setup_key':setup_key,'version':VERSION})
        emit(cid,'created',symbol=row['symbol'],setup_key=setup_key,decision_session=t['decision_session'],
             frozen_plan=dict(p),context_sha256=digest(context),technical_snapshot_hash=t['snapshot_hash'],
             decision_quote=observation(row['quote']),entry_expires_at=until.isoformat(),
             holding_sessions=holding,holding_deadline_session=end,
             entry_rule='First strictly later source update after candidate recording, within frozen zone and gross RR >=2, with current research and quote guards',
             execution_policy='Observed conditions only. No executable fill, stop ordering or position is inferred between samples.',
             cost_policy='No executed position: net return unavailable. Calculator assumptions never rewrite this ledger.')
        seen.add(setup_key);occupied.add(row['symbol'])
    cases=fold(events)
    counts={key:sum(c['status']==key for c in cases.values()) for key in ('awaiting_later_observation','entry_condition_observed',*sorted(TERMINAL))}
    summary={'schema_version':1,'version':VERSION,'checked_at':now.isoformat(),'checkpoint_id':checkpoint_id,
             'publication_verified':False,'candidate_count':len(cases),'counts':counts,'enrollment_blockers':enrollment_blockers,
             'observed_entry_count':sum('entry_observation' in c for c in cases.values()),
             'resolved_trades':0,'verified_fills':0,
             'win_rate':None,'average_net_gain':None,'average_net_loss':None,'drawdown':None,
             'performance_status':'Unavailable: point observations do not establish execution or intervening stop/target order',
             'coverage':'All prospectively enrolled candidates retained, including expired and cancelled; repeated checkpoints are not new calls',
             'cases':list(cases.values())}
    atomic(Path(root)/'research_paper_summary.json',summary)
    return summary
