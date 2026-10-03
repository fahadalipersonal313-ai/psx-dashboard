"""Evidence-first 15-stock desk. Combines research with guarded engine observations.

No orders, personal portfolio information, calibrated probability, or model-authored
numeric execution levels are accepted. Historical v8 decisions remain unchanged.
"""
from datetime import date, datetime, timedelta, timezone
import math
import research_contract as contract
import session_calendar as cal

RESEARCH_URL = 'https://raw.githubusercontent.com/fahadalipersonal313-ai/psx-engine/research-state/research_context.json'


def number(value):
    return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value)


def source_time(quote):
    return quote.get('source_as_of') or quote.get('last_trade')


def fresh_quote(quote, now):
    try:
        at = contract.stamp(source_time(quote))
        if quote.get('source_as_of'):
            fetched=contract.stamp(quote['fetched_at'])
            if (quote.get('market') != 'REG' or quote.get('source_url') != 'https://dps.psx.com.pk/company/'+quote['symbol']
                    or not at <= fetched <= now or quote.get('volume_kind') != 'cumulative_session'):
                return False
        return (cal.is_live(now) and cal.is_live(at) and cal.local_now(at).date() == cal.local_now(now).date()
                and 0 <= (now-at).total_seconds() <= 1200 and number(quote['price'])
                and quote['price'] > 0 and not quote.get('note')
                and (quote.get('change_pct') is None or (number(quote['change_pct']) and abs(quote['change_pct']) <= 10.5)))
    except (ValueError, TypeError, KeyError):
        return False


def technical_plan(row, now):
    """Fail closed at viewing time. No stale level becomes a current entry."""
    if not row:
        return None, 'No completed-session technical observation'
    try:
        import config, decision_engine
        if row.get('strategy_version') != config.STRATEGY_VERSION or row.get('config_hash') != decision_engine.digest(decision_engine.contract()):
            return None, 'Technical rules or configuration are obsolete'
        if (row.get('research_guard') or {}).get('binding') != {k:row.get(k) for k in ('symbol','decision_session','strategy_version','config_hash','snapshot_hash')}:
            return None, 'Research input audit does not match the technical snapshot'
        at = contract.stamp(row['run_time'])
        if at > now or row['decision_session'] != cal.last_completed(now):
            return None, 'Completed-session prices are stale or future-dated'
        if not (row.get('research_guard') or {}).get('valid'):
            return None, 'Current source/action/session audit incomplete: '+('; '.join((row.get('research_guard') or {}).get('checks',[])))
        if not row.get('snapshot_hash') or not row.get('config_hash'):
            return None, 'Versioned source evidence is missing'
        if row.get('data_quality') != 'good' or row.get('signal') not in ('Buy','Strong Buy'):
            return None, row.get('main_reason') or 'Technical entry checks did not pass'
        entry, stop, target = row['price'], row['stop_loss'], row['target1']
        t2 = row.get('target2')
        if not all(number(x) and x > 0 for x in (entry,stop,target)) or not stop < entry < target:
            return None, 'Entry, stop and target are unavailable or inconsistent'
        if t2 is not None and (not number(t2) or t2 <= target):
            return None, 'Second target is inconsistent'
        return {'reference_entry':entry,'stop':stop,'target1':target,'target2':t2,
                'risk_pct':round((entry-stop)/entry*100,2),
                'reward_risk':round((target-entry)/(entry-stop),2),
                'buy_zone_low':row.get('buy_zone_low'),'buy_zone_high':row.get('buy_zone_high'),
                'decision_session':row['decision_session'],'strategy_version':row.get('strategy_version'),
                'source':'Versioned official completed-session OHLC; raw price levels'}, ''
    except (ValueError, TypeError, KeyError):
        return None, 'Technical observation failed validation'


def build(context, snapshot=None, intraday=None, now=None):
    now = now or datetime.now(timezone.utc)
    usable = contract.current(context,now)
    intraday_review_current = bool(usable and now-contract.stamp(context['as_of']) <= timedelta(minutes=60))
    try:
        contract.validate(context)
    except (ValueError,TypeError,KeyError,OverflowError):
        context = None
    rows = {x.get('symbol'):x for x in (snapshot or {}).get('rows',[]) if isinstance(x,dict)}
    quotes = {x.get('symbol'):x for x in (intraday or {}).get('prices',[]) if isinstance(x,dict)}
    observations = {x.get('symbol'):x for x in (intraday or {}).get('observations',[]) if isinstance(x,dict)}
    research = {x['symbol']:x for x in context['stocks']} if context else {}
    macro = context['market_context'] if context else []
    macro_missing = any(not any(x['category']==kind and x['status']=='available' for x in macro)
                        for kind in ('macro','geopolitical'))
    global_reasons=[x['category'].capitalize()+' risk: '+x['summary'] for x in macro
                    if x['category'] in ('macro','geopolitical') and x['status']=='available' and x['bias']=='adverse']
    global_adverse = any(x['category'] in ('macro','geopolitical') and x['status']=='available' and x['bias']=='adverse' for x in macro)
    result = []
    for symbol in contract.UNIVERSE:
        row, quote, obs, review = rows.get(symbol),quotes.get(symbol),observations.get(symbol),research.get(symbol)
        plan, reason = technical_plan(row,now)
        current_quote = fresh_quote(quote or {},now)
        missing = []
        if not usable: missing.append('Research review missing, invalid or expired')
        if not plan: missing.append(reason)
        if macro_missing: missing.append('Macro or geopolitical evidence unavailable')
        if review:
            for key in ('news','sector'):
                if review[key]['status']=='unavailable': missing.append(key.capitalize()+' review unavailable')
        blocked_reasons=list(global_reasons)
        if review:
            blocked_reasons.extend(k.capitalize()+' risk: '+review[k]['summary'] for k in ('news','sector','fundamentals')
                                   if review[k]['status']=='available' and review[k]['bias']=='adverse')
        adverse = bool(blocked_reasons)
        event_pending = bool(review and review['fundamentals']['event_review_required'])
        if event_pending:blocked_reasons.append('Material company event requires a new financial review')
        # Known dated events are additional gates; missing dates are never invented.
        if review:
            for event in review['fundamentals'].get('events',[]):
                days=(date.fromisoformat(event['date'])-cal.local_now(now).date()).days
                if event['kind']=='earnings' and 0 <= days <= 5:
                    event_pending=True
                    missing.append('Known earnings release within five calendar days')
                    blocked_reasons.append('Known earnings release within five calendar days')
                elif event['kind']=='corporate_action' and 0 <= days <= 1:
                    event_pending=True
                    missing.append('Imminent corporate action requires price-basis review')
                    blocked_reasons.append('Imminent corporate action requires price-basis review')
        stance = review['horizons']['swing']['stance'] if review else 'unavailable'
        if review and stance in ('avoid','cautious','unavailable'):
            blocked_reasons.append('Swing research '+stance+': '+review['horizons']['swing']['rationale'])
        blocked = adverse or event_pending or stance in ('avoid','cautious','unavailable')
        swing_state = 'No current setup'
        if usable and plan and not blocked and not macro_missing and not missing:
            swing_state = 'Conditional swing plan'
        elif blocked and usable:
            swing_state = 'Caution: material research risk'
        if not usable or blocked or missing:
            plan = None
        if plan and cal.is_live(now):
            # Reference-close levels alone cannot authorize today's entry.
            high = plan.get('buy_zone_high')
            low = plan.get('buy_zone_low')
            if not intraday_review_current:
                swing_state = 'Wait: current-session research review is due'
            elif not current_quote:
                swing_state = 'Wait for a fresh delayed quote'
            elif not number(low) or not number(high) or not low <= quote['price'] <= high or quote['price'] <= plan['stop']:
                swing_state = 'Wait: current price outside validated entry zone'
            elif quote['price'] >= plan['target1'] or (plan['target1']-quote['price'])/(quote['price']-plan['stop']) < 2:
                swing_state = 'Wait: current reward/risk below 2 before costs'
            else:
                plan['observed_entry'] = quote['price']
                plan['observed_reward_risk'] = round((plan['target1']-quote['price'])/(quote['price']-plan['stop']),2)
                plan['observed_risk_pct'] = round((quote['price']-plan['stop'])/quote['price']*100,2)
                swing_state = 'Swing setup for review'
        intraday_state = 'Unavailable: no fresh current-session observation'
        if not cal.is_live(now):
            intraday_state = 'Market closed'
        elif current_quote and intraday_review_current and review:
            view = review['horizons']['intraday']['stance']
            if view == 'unavailable':
                intraday_state = 'Unavailable: intraday research not reviewed'
            elif adverse or event_pending or view in ('avoid','cautious'):
                intraday_state = 'Caution: research risk'
            elif obs and obs.get('state') in ('Momentum confirmed · watch','Confirmed delayed-data watch') and obs.get('qualifies') and source_time(obs)==source_time(quote):
                intraday_state = 'Confirmed delayed-data watch'
            else:
                intraday_state = 'Watching: confirmation incomplete'
        fundamentals_ok = bool(review and contract.fundamentals_current(review['fundamentals'],now))
        investment = 'Unavailable: current financial review required'
        if usable and fundamentals_ok:
            investment = 'Long-term research: '+review['horizons']['investment']['stance']
        result.append({'symbol':symbol,'swing_state':swing_state,'intraday_state':intraday_state,
                       'investment_state':investment,'plan':plan,'missing':missing,
                       'blocked_reasons':blocked_reasons,'technical_reason':reason,
                       'research':review,'technical':row,'quote':quote,'fresh_quote':current_quote,
                       'observation':obs,'fundamentals_current':fundamentals_ok})
    return {'evaluated_at':now.isoformat(),'research_current':bool(usable),'context':context,
            'market_open':cal.is_live(now),'rows':result}


def pkt(value):
    try:return contract.stamp(value).astimezone(cal.PKT).strftime('%a %d %b %Y, %I:%M %p PKT')
    except (ValueError,TypeError,KeyError):return 'unavailable'


def show_collection(st, status):
    st.markdown('#### Intraday observation collection')
    if not status:
        st.info('Five-minute collection is configured for trading sessions. No capture has been published yet.')
        return
    st.write('Last capture: '+pkt(status.get('checked_at'))+' · '+str(status.get('current_usable_points',0))+
             '/15 usable new source points · target every '+str(status.get('target_minutes',5))+' minutes')
    st.caption('Scheduled poll windows elapsed: '+str(status.get('regular_poll_windows_elapsed',0))+
               ' · windows observed: '+str(status.get('regular_poll_windows_observed',0))+
               ' · missed windows: '+str(status.get('missed_poll_windows',0))+
               '. Counts are as of the last capture; the exchange clock and a 60-second grace are used.')
    st.caption('Partial delayed point observations only. Five-minute polling does not create complete 1-minute or 5-minute OHLCV candles. '
               'Intraday entry, stop and target rules and paper fills are not implemented yet.')
    with st.expander('Collection coverage, gaps and model prerequisites'):
        st.dataframe([{'Stock':x['symbol'],'Source update (PKT)':pkt(x.get('source_as_of')),
                       'Observed extra shares':x.get('observed_volume_delta'),
                       'Actual source interval (seconds)':x.get('volume_interval_seconds'),
                       'Quality':'; '.join(v.replace('_',' ') for v in x.get('quality_flags',[]))}
                      for x in status.get('rows',[])],hide_index=True,width='stretch')
        st.write(status.get('paper_strategy_status','Collection only'))
        for item in status.get('prerequisites',[]):st.write('• '+item)
        st.caption('Raw collection timestamp: '+str(status.get('checked_at')))


def show(st):
    import remote_data
    context = remote_data.fetch_json('research_context.json',branch='research-state',ttl=60,timeout=4)
    snapshot = remote_data.fetch_json('dashboard_snapshot.json',branch='runtime-state',ttl=60,timeout=4)
    intraday = remote_data.fetch_json('research_quotes.json',branch='runtime-state',ttl=60,timeout=4)
    desk = build(context,snapshot,intraday)
    journal = remote_data.fetch_json('research_status.json',branch='runtime-state',ttl=60,timeout=4)
    collection = remote_data.fetch_json('intraday_collection_status.json',branch='runtime-state',ttl=60,timeout=4)
    st.subheader('15-stock research desk')
    if journal:
        st.caption('Prospective decision journal: '+pkt(journal.get('checked_at'))+' · '+str(journal.get('outcomes'))+' · no assumed fills')
    st.caption('Company news, sector, macro and event risk are combined here with guarded technical evidence. '
               'Original technical strategy and its historical results remain separately attributable.')
    st.warning('Intraday is delayed-data watch only: numeric entry, exit and stop levels are withheld because a snapshot-based intraday execution model has not been validated. Long-term views are financial research, with numeric valuation targets withheld.')
    st.info('Research decision support only. DPS prices may be delayed by at least 5 minutes; exchange/source time '
            'is shown separately from retrieval time. No win probability or guaranteed return is established.')
    if not desk['research_current']:
        st.warning('Research is missing, invalid or expired. Current combined entry plans are withheld until a new review arrives.')
    if desk['context']:
        st.caption('Research as of '+pkt(desk['context']['as_of'])+' · artifact generated '+pkt(desk['context']['generated_at'])+
                   ' · expires '+pkt(desk['context']['expires_at']))
    st.caption('Engine snapshot retrieved/generated: '+pkt((snapshot or {}).get('generated_at'))+
               ' · intraday scan: '+pkt((intraday or {}).get('checked_at'))+
               ' · market '+('open' if desk['market_open'] else 'closed')+' · rechecked on each page refresh')
    show_collection(st,collection)
    st.dataframe([{'Stock':r['symbol'],'Intraday':r['intraday_state'],'Swing':r['swing_state'],
                   'Long term':r['investment_state'],'Financial review':'current' if r['fundamentals_current'] else 'due / unavailable'}
                  for r in desk['rows']],hide_index=True,width="stretch")
    selected = st.selectbox('Research stock',list(contract.UNIVERSE),key='combined_research_symbol')
    row = next(r for r in desk['rows'] if r['symbol']==selected)
    st.markdown('#### '+selected+' · evidence and plan')
    for key,label in (('intraday_state','Intraday'),('swing_state','Swing'),('investment_state','Long term')):
        st.write(label+': '+row[key])
    quote = row['quote'] or {}
    if quote:
        st.write('Last observed price: '+str(quote.get('price'))+' PKR · exchange/source update time: '+pkt(source_time(quote))+
                 ' · session volume: '+str(quote.get('day_volume'))+(' · fresh delayed observation' if row['fresh_quote'] else ' · stale / outside session'))
    technical = row['technical'] or {}
    st.caption('Completed-session technical evidence: '+str(technical.get('decision_session','unavailable'))+
               ' · rule '+str(technical.get('strategy_version','unavailable'))+' · '+str(technical.get('signal','No data')))
    if row['plan']:
        p = row['plan']
        st.write('Conditional reference entry PKR '+str(p['reference_entry'])+' · stop '+str(p['stop'])+
                 ' · target 1 '+str(p['target1'])+' · target 2 '+str(p['target2'])+
                 ' · initial risk '+str(p['risk_pct'])+'% · reward/risk '+str(p['reward_risk']))
        if p.get('observed_entry'):
            st.write('At observed quote '+str(p['observed_entry'])+' PKR: risk '+str(p['observed_risk_pct'])+'% · gross reward/risk '+str(p['observed_reward_risk'])+' before costs')
        st.caption('Entry zone '+str(p['buy_zone_low'])+' to '+str(p['buy_zone_high'])+
                   '. Recheck quote, spread, liquidity and event risk before any decision. Stop/target fills are not guaranteed; '
                   'gap, cost and circuit-limit risk remain. Exit review at stop, target, thesis invalidation or the strategy’s frozen holding deadline.')
    else:
        st.write('Entry / stop / targets are withheld for the combined call.')
        for reason in row['blocked_reasons']:st.write('Research block: '+reason)
        for reason in row['missing']:
            if reason != row['technical_reason']:st.write('Evidence check: '+reason)
    if row['technical_reason']:st.write('Separate technical screen: '+row['technical_reason'])
    review = row['research']
    if review:
        st.write('Thesis: '+review['thesis'])
        st.write('Countercase: '+review['countercase'])
        for name,title in (('news','Company news'),('sector','Sector'),('fundamentals','Fundamentals'),('public_sentiment','Public sentiment')):
            item=review[name]
            st.write(title+' · '+item['status']+' · '+item['bias']+': '+item['summary'])
        f=review['fundamentals']
        st.caption('Financial period '+str(f.get('report_period'))+' · reviewed '+pkt(f.get('reviewed_at'))+
                   ' · next monthly review '+pkt(f.get('next_review_at'))+' · event review '+('required' if f['event_review_required'] else 'not flagged'))
        if 'events' not in f:
            st.caption('Dated event coverage is unknown; no complete earnings calendar is connected.')
        elif f['events']:
            st.write('Verified listed dates: '+'; '.join(e['kind'].replace('_',' ')+' '+e['date'] for e in f['events']))
            st.caption('Only the listed verified dates are guarded; other future dates can still be unknown.')
        else:
            st.caption('No verified dated events are listed in this review. This is not proof that no events are scheduled.')
        if f['event_triggers']: st.write('Re-review triggers: '+'; '.join(f['event_triggers']))
        st.caption('Long-term numeric valuation target is withheld until a separately validated financial valuation model exists. '
                   'News sentiment is not counted as independent public/social sentiment. Sector evidence here is qualitative; a verified sector-index performance series is not yet connected.')
        for horizon in ('intraday','swing','investment'):
            st.write(horizon.capitalize()+' view: '+review['horizons'][horizon]['rationale'])
        with st.expander('Sources and publication times'):
            st.caption('Raw source update: '+str(source_time(quote))+' · fetched: '+str(quote.get('fetched_at'))+
                       ' · research as-of: '+str(desk['context']['as_of']))
            ids=set()
            for key in ('news','sector','fundamentals','public_sentiment'): ids.update(review[key]['source_ids'])
            for item in desk['context']['market_context']: ids.update(item['source_ids'])
            for source in desk['context']['sources']:
                if source['id'] in ids:
                    st.write(source['title'])
                    st.write(source['url'])
                    st.caption('Published '+str(source.get('published_at') or ((source.get('published_date')+' (date only; time unknown)') if source.get('published_date') else 'time not verified'))+' · checked '+source['verified_at']+' · '+source['kind'])
    if desk['context']:
        with st.expander('Market-wide evidence'):
            for item in desk['context']['market_context']:
                st.write(item['category'].capitalize()+' · '+item['status']+' · '+item['bias']+': '+item['summary'])
    st.caption('Configured refresh targets (subject to scheduler health): point observations every 5 minutes and technical analysis every 15 minutes during exchange sessions; research every 30 minutes; '
               'fundamentals monthly and on material events. Schedulers are best-effort, so freshness is checked here instead of assumed.')
