"""Canonical primary 15-stock decisions, recomputed from original evidence at view time.

This adapter never changes v8 technical history. Only Ready-for-review decisions
carry current numerical plans; delayed observations are not executable fills.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import research_contract as contract
import research_desk
import research_actions

VERSION='combined-signal-v1'
STATUSES=('Ready for review','Watching','Blocked')


def digest(value):
    try:return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (ValueError,TypeError):return None


def evidence_links(review,context):
    ids=set()
    if review:
        for key in ('news','sector','fundamentals','public_sentiment'):
            ids.update(review[key].get('source_ids',[]))
        for value in review['horizons'].values():ids.update(value.get('source_ids',[]))
        for event in review['fundamentals'].get('events',[]):ids.update(event.get('source_ids',[]))
    for item in (context or {}).get('market_context',[]):
        if item['category'] in ('macro','geopolitical'):ids.update(item.get('source_ids',[]))
    return [{'title':s['title'],'url':s['url'],'source_id':s['id'],'verified_at':s['verified_at'],
             **{k:s.get(k) for k in ('published_at','published_date','publication_precision')}}
            for s in (context or {}).get('sources',[]) if s['id'] in ids]


def from_desk(desk,source_hashes=None):
    """Serialize one canonical evaluation. Call evaluate for untrusted raw inputs."""
    context=desk.get('context');signals=[]
    for row in desk['rows']:
        a=research_actions.action(row,desk);status=a['status']
        reasons=list(dict.fromkeys(row['blocked_reasons']+row['missing']))
        technical=row.get('technical') or {}
        if technical.get('signal') not in ('Buy','Strong Buy') and row.get('technical_reason') in reasons:
            reasons=[('Original technical entry screen: '+str(technical.get('signal') or 'No data'))
                     if item==row['technical_reason'] else item for item in reasons]
        if status!='Ready for review':
            if row['swing_state'] not in reasons:reasons.append(row['swing_state'])
            if not desk['market_open']:reasons.insert(0,'Market closed; a new session review is required')
        else:reasons=['Research, technical and delayed-price entry checks pass; verify executable price, costs and sizing']
        # Reference-only, stale, out-of-zone and closed-session plans stay out of
        # the primary signal. They remain attributable in the raw frozen inputs.
        plan=dict(row['plan']) if status=='Ready for review' and row.get('plan') else None
        if plan and not research_desk.number(plan.get('observed_entry')):
            status='Blocked';plan=None;reasons=['Validated observed entry is missing']
        tech=row.get('technical') or {};quote=row.get('quote')
        quote_summary=None
        if quote:
            quote_summary={k:quote.get(k) for k in ('price','source_as_of','fetched_at','source_url','market','quality_flags')}
            quote_summary['fresh']=row['fresh_quote']
        signals.append({'symbol':row['symbol'],'status':status,'reason':'; '.join(reasons),'reasons':reasons,
                        'swing_state':row['swing_state'],'intraday_state':row['intraday_state'],
                        'investment_state':row['investment_state'],'plan':plan,
                        'expires_at':a['entry_review_due'] if plan else None,'quote':quote_summary,
                        'technical':{**{k:tech.get(k) for k in ('signal','strategy_version','config_hash','snapshot_hash','decision_session','run_time')},
                                     'reason':tech.get('main_reason') or row.get('technical_reason')},
                        'research_as_of':(context or {}).get('as_of'),'research_expires_at':(context or {}).get('expires_at'),
                        'source_links':evidence_links(row.get('research'),context)})
    return {'schema_version':1,'version':VERSION,'evaluated_at':desk['evaluated_at'],
            'market_open':desk['market_open'],'research_current':desk['research_current'],
            'context_as_of':(context or {}).get('as_of'),'context_expires_at':(context or {}).get('expires_at'),
            'counts':{status:sum(s['status']==status for s in signals) for status in STATUSES},
            'signals':signals,'errors':desk.get('input_errors',[]),'source_hashes':source_hashes or {},'desk':desk}


def evaluate(context,snapshot=None,quotes=None,now=None):
    now=now or datetime.now(timezone.utc)
    return from_desk(research_desk.build(context,snapshot,quotes,now),
                     {'context_sha256':digest(context),'technical_sha256':digest(snapshot),'quotes_sha256':digest(quotes)})


def load(snapshot=None,now=None):
    """Fetch once per app rerun. Evaluations themselves are never cached."""
    import remote_data
    def get(path,branch='runtime-state'):
        return remote_data.fetch_json(path,branch=branch,ttl=60,timeout=4)
    context=get('research_context.json','research-state')
    if snapshot is None:snapshot=get('dashboard_snapshot.json')
    quotes=get('research_quotes.json')
    extras={key:get(path) for key,path in [('journal','research_status.json'),('collection','intraday_collection_status.json'),
              ('comparisons','research_comparisons.json'),('paper','research_paper_summary.json'),('activity','research_activity.json')]}
    recorded=get('research_signals.json')
    # Evaluate after reads finish, not before potentially slow network requests.
    combined=evaluate(context,snapshot,quotes,now)
    attach_recorded(combined,recorded)
    return {'recorded':recorded,'context':context,'snapshot':snapshot,'intraday':quotes,
            'combined':combined,'desk':combined['desk'],**extras}



def attach_recorded(payload,recorded):
    """Compare recorded attribution to current checks; never activate from stored status."""
    payload['current_changes']=[]
    if not isinstance(recorded,dict) or recorded.get('version')!=VERSION:return payload
    try:
        if contract.stamp(recorded['evaluated_at'])>contract.stamp(payload['evaluated_at']):return payload
        old=recorded['signals']
        if len(old)!=15 or {x['symbol'] for x in old}!=set(contract.UNIVERSE):return payload
        before={x['symbol']:x for x in old}
        if any(x['status'] not in STATUSES for x in old):return payload
    except (KeyError,TypeError,ValueError):return payload
    payload['recorded_at']=recorded['evaluated_at']
    payload['recorded_checkpoint']=recorded.get('checkpoint_id')
    payload['sourcehash_coherence']=recorded.get('source_hashes')==payload.get('source_hashes')
    for current in payload['signals']:
        previous=before[current['symbol']]
        if previous['status']!=current['status']:
            payload['current_changes'].append({'symbol':current['symbol'],'previous_status':previous['status'],
                'current_status':current['status'],'reason':current['reason']})
    return payload
