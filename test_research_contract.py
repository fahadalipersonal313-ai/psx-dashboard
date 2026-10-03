import copy
from datetime import datetime,timedelta,timezone
import unittest
import research_contract as c
import research_desk as d

NOW=datetime(2026,10,5,6,tzinfo=timezone.utc)

def fixture():
    ev={'status':'available','summary':'Sourced evidence','bias':'supportive','source_ids':['s']}
    stocks=[]
    for symbol in c.UNIVERSE:
        stocks.append({'symbol':symbol,'thesis':'Test thesis','countercase':'Test risk','news':dict(ev),'sector':dict(ev),
          'fundamentals':{**ev,'report_period':'FY2026','reviewed_at':NOW.isoformat(),'next_review_at':(NOW+timedelta(days=30)).isoformat(),'event_review_required':False,'event_triggers':['results']},
          'public_sentiment':{'status':'unavailable','summary':'No independent social sample','bias':'unknown','source_ids':[]},
          'horizons':{k:{'stance':'supportive','rationale':'Sourced test view','source_ids':['s']} for k in ('intraday','swing','investment')}})
    return {'schema_version':1,'generated_at':NOW.isoformat(),'as_of':NOW.isoformat(),'expires_at':(NOW+timedelta(hours=2)).isoformat(),'universe':list(c.UNIVERSE),
            'sources':[{'id':'s','title':'Source','url':'https://example.org/a','kind':'issuer_filing','published_at':NOW.isoformat(),'verified_at':NOW.isoformat()}],
            'market_context':[{**ev,'category':k} for k in ('macro','geopolitical')],'stocks':stocks}


def technical():
    import config,decision_engine
    row = {'symbol':'PRL','run_time':NOW.isoformat(),'decision_session':'2026-10-02','snapshot_hash':'hash','config_hash':decision_engine.digest(decision_engine.contract()),'strategy_version':config.STRATEGY_VERSION,'data_quality':'good','signal':'Buy','price':100.,'stop_loss':95.,'target1':112.,'target2':115.,'buy_zone_low':99.,'buy_zone_high':103.,'research_guard':{'valid':True}}
    row['research_guard']['binding']={k:row.get(k) for k in ('symbol','decision_session','strategy_version','config_hash','snapshot_hash')}
    return row


def quote():
    return {'symbol':'PRL','price':100.,'source_as_of':(NOW-timedelta(minutes=5)).isoformat(),'day_volume':300000,'note':None,'market':'REG','source_url':'https://dps.psx.com.pk/company/PRL','fetched_at':NOW.isoformat(),'volume_kind':'cumulative_session'}


class ContractTests(unittest.TestCase):
    def test_valid_and_expiry(self):
        v=fixture();self.assertTrue(c.current(v,NOW));self.assertFalse(c.current(v,NOW+timedelta(hours=2)))
    def test_future_naive_missing_symbols_or_source_rejected(self):
        for change in (lambda v:v.update(generated_at=(NOW+timedelta(minutes=1)).isoformat()),
                       lambda v:v.update(as_of='2026-10-05T06:00:00'),lambda v:v['stocks'].pop(),lambda v:v['sources'].clear()):
            v=fixture();change(v);self.assertFalse(c.current(v,NOW))
    def test_monthly_fundamental_expiry_and_event(self):
        f=fixture()['stocks'][0]['fundamentals'];self.assertTrue(c.fundamentals_current(f,NOW));f['event_review_required']=True;self.assertFalse(c.fundamentals_current(f,NOW))
    def test_old_evidence_cannot_be_restamped(self):
        v=fixture();v['sources'][0]['verified_at']='2000-01-01T00:00:00+00:00';self.assertFalse(c.current(v,NOW))

    def test_no_news_requires_coverage_and_is_not_positive_or_other_component(self):
        for key,bias,sources in [('sector','unknown',['s']),('news','supportive',['s']),('news','unknown',[])]:
            v=fixture();v['stocks'][0][key].update(status='no_material_news',bias=bias,source_ids=sources);self.assertFalse(c.current(v,NOW))
        v=fixture();v['stocks'][0]['news'].update(status='no_material_news',bias='unknown');self.assertTrue(c.current(v,NOW))

    def test_monthly_cached_fundamental_evidence_is_preserved(self):
        v=fixture();v['sources'].append({**v['sources'][0],'id':'financial','verified_at':(NOW-timedelta(days=20)).isoformat(),'published_at':(NOW-timedelta(days=30)).isoformat()})
        f=v['stocks'][0]['fundamentals'];f.update(source_ids=['financial'],reviewed_at=(NOW-timedelta(days=20)).isoformat(),next_review_at=(NOW+timedelta(days=10)).isoformat());self.assertTrue(c.current(v,NOW))

    def test_unsafe_url_rejected(self):
        v=fixture();v['sources'][0]['url']='https://user:password@example.com';self.assertFalse(c.current(v,NOW))

class CombinedTests(unittest.TestCase):
    def row(self,context=None,tech=None,q=None,obs=None,now=NOW):
        return d.build(context or fixture(),{'rows':[tech or technical()]},{'prices':[q or quote()],'observations':[obs] if obs else []},now)['rows'][0]
    def test_current_setup_reference_and_quote_risk(self):
        r=self.row();self.assertEqual(r['swing_state'],'Swing setup for review');self.assertEqual(r['plan']['observed_reward_risk'],2.4)
    def test_stale_quote_no_entry_and_high_quote_rr_blocks(self):
        q=quote();q['source_as_of']=(NOW-timedelta(minutes=21)).isoformat();self.assertIn('Wait',self.row(q=q)['swing_state'])
        q=quote();q['price']=102;self.assertIn('reward/risk',self.row(q=q)['swing_state'])
    def test_stale_technical_or_context_withholds_levels(self):
        t=technical();t['decision_session']='2026-10-01';self.assertIsNone(self.row(tech=t)['plan'])
        v=fixture();v['expires_at']=NOW.isoformat();self.assertIsNone(self.row(context=v)['plan'])
    def test_obsolete_rules_hash_or_guard_binding_block(self):
        for field,value in [('strategy_version','obsolete-v1'),('config_hash','wrong'),('snapshot_hash','wrong')]:
            t=technical();t[field]=value;self.assertIsNone(self.row(tech=t)['plan'])

    def test_missing_action_audit_blocks(self):
        t=technical();t.pop('research_guard');self.assertIsNone(self.row(tech=t)['plan'])
    def test_sector_global_veto_is_not_cross_sector(self):
        v=fixture();v['market_context'].append({'category':'sector','status':'available','summary':'Other sector risk','bias':'adverse','source_ids':['s']});self.assertIsNotNone(self.row(context=v)['plan'])
    def test_adverse_fundamentals_and_pending_event_block(self):
        for field,value in [('bias','adverse'),('event_review_required',True)]:
            v=fixture();v['stocks'][0]['fundamentals'][field]=value;self.assertIsNone(self.row(context=v)['plan'])
    def test_intraday_requires_fresh_research_and_review(self):
        q=quote();obs={**q,'state':'Confirmed delayed-data watch','qualifies':True}
        self.assertEqual(self.row(q=q,obs=obs)['intraday_state'],'Confirmed delayed-data watch')
        v=fixture();v['as_of']=(NOW-timedelta(hours=2)).isoformat();self.assertIn('Unavailable',self.row(context=v,q=q,obs=obs)['intraday_state'])
        v=fixture();v['stocks'][0]['horizons']['intraday']['stance']='unavailable';self.assertIn('Unavailable',self.row(context=v,q=q,obs=obs)['intraday_state'])
    def test_closed_or_friday_break_never_intraday(self):
        self.assertEqual(self.row(now=datetime(2026,10,3,6,tzinfo=timezone.utc))['intraday_state'],'Market closed')
        self.assertEqual(self.row(now=datetime(2026,10,2,8,tzinfo=timezone.utc))['intraday_state'],'Market closed')
    def test_preopen_source_snapshot_cannot_be_live_entry(self):
        q=quote();now=NOW.replace(hour=4,minute=32);q['source_as_of']=now.replace(minute=27).isoformat();q['fetched_at']=now.isoformat()
        self.assertFalse(d.fresh_quote(q,now))

    def test_future_quote_and_naive_source_time_fail_closed(self):
        for value in ('2026-10-05T06:01:00+00:00','2026-10-05T05:55:00'):
            q=quote();q['source_as_of']=value;self.assertFalse(d.fresh_quote(q,NOW))

if __name__=='__main__':unittest.main()
