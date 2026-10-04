from datetime import date,timedelta
import copy
import unittest
import research_planner as p
import research_desk as d
from test_research_contract import NOW,fixture,technical,quote


def row(context=None,now=NOW):return d.build(context or fixture(),{'rows':[technical()]},{'prices':[quote()]},now)['rows'][0]

class PlannerTests(unittest.TestCase):
    def test_two_calendar_months_and_trade_horizon_stay_distinct(self):
        v=p.build(row(),fixture(),NOW)
        self.assertEqual(v['planning_start'],'2026-10-05');self.assertEqual(v['planning_end'],'2026-12-05')
        self.assertEqual(v['trade_holding_sessions'],30)
        self.assertEqual(p.add_months(date(2026,12,31)),date(2027,2,28))
        self.assertEqual(p.add_months(date(2027,12,31)),date(2028,2,29))
    def test_stale_or_blocked_plan_cannot_be_smuggled_in(self):
        c=fixture();c['market_context'][0]['bias']='adverse'
        fake=row();fake['plan']['target1']=999
        result=p.build(fake,c,NOW);self.assertIsNone(result['plan'])
        c=fixture();c['expires_at']=NOW.isoformat();self.assertIsNone(p.build(fake,c,NOW)['plan'])
    def test_events_keep_verified_dates_and_review_dates_are_not_catalysts(self):
        c=fixture();c['stocks'][0]['fundamentals']['events']=[{'kind':'agm','date':'2026-10-21','known_at':NOW.isoformat(),'source_ids':['s']}]
        v=p.build(row(c),c,NOW)
        self.assertEqual([x['date'] for x in v['events']],['2026-10-21'])
        self.assertEqual(v['events'][0]['sources'][0]['url'],'https://example.org/a')
        self.assertIn('date only',v['events'][0]['date_precision'])
        self.assertTrue(all(x['kind']=='suggested research review' for x in v['suggested_reviews']))
    def test_context_validation_blocks_future_event_knowledge(self):
        c=fixture();c['stocks'][0]['fundamentals']['events']=[{'kind':'agm','date':'2026-10-21','known_at':(NOW+timedelta(days=1)).isoformat(),'source_ids':['s']}]
        v=p.build(row(),c,NOW);self.assertFalse(v['research_current']);self.assertFalse(v['events']);self.assertIsNone(v['plan'])
    def test_checklist_links_match_component_evidence(self):
        c=fixture();c['stocks'][0]['news']['bias']='adverse'
        checks=p.checks(row(c),c,NOW)
        news=next(x for x in checks if x['check']=='News review')
        self.assertEqual(news['status'],'Fail');self.assertEqual(news['sources'][0]['id'],'s')
        event=next(x for x in checks if x['check']=='Listed event-date checks')
        self.assertEqual(event['status'],'Partial');self.assertIn('not a complete',event['detail'])
        public=next(x for x in checks if x['check']=='Independent public sentiment');self.assertEqual(public['status'],'Unavailable')
    def test_financial_freshness_is_distinct_from_adverse_risk(self):
        c=fixture();c['stocks'][0]['fundamentals']['bias']='adverse'
        values={x['check']:x for x in p.checks(row(c),c,NOW)}
        self.assertEqual(values['Financial review']['status'],'Pass')
        self.assertEqual(values['Fundamental and material-event risk']['status'],'Fail')
        self.assertEqual(values['Fundamental and material-event risk']['sources'][0]['id'],'s')

    def test_future_generated_context_cannot_leak_narrative_or_events(self):
        c=fixture();c['generated_at']=(NOW+timedelta(days=1)).isoformat();c['expires_at']=(NOW+timedelta(days=2)).isoformat()
        c['sources'][0]['verified_at']=c['sources'][0]['published_at']=c['generated_at']
        c['stocks'][0]['fundamentals']['reviewed_at']=c['generated_at']
        c['stocks'][0]['fundamentals']['events']=[{'kind':'agm','date':'2026-10-21','known_at':NOW.isoformat(),'source_ids':['s']}]
        p.contract.validate(c)
        v=p.build(row(),c,NOW)
        self.assertIsNone(v['plan']);self.assertFalse(v['events']);self.assertNotEqual(v['thesis'],c['stocks'][0]['thesis'])
    def test_numeric_plan_failure_is_not_labeled_signal_validation_pass(self):
        r=row();r['technical']['stop_loss']=1000
        values={x['check']:x for x in p.checks(r,fixture(),NOW)}
        self.assertEqual(values['Technical signal classification and data quality']['status'],'Pass')
        self.assertIn('fields only',values['Technical signal classification and data quality']['detail'])
        self.assertEqual(values['Guarded numeric technical plan']['status'],'Fail')

    def test_moving_artifact_links_disclose_observed_hashes(self):
        values=p.checks(row(),fixture(),NOW)
        source=next(v for v in values if v['check']=='Active technical rules')['sources'][0]
        self.assertIn('branch may advance',source['title'])
        self.assertEqual(source['observed_metadata']['snapshot_hash'],'hash')
        self.assertEqual(source['observed_metadata']['run_time'],NOW.isoformat())

    def test_unknown_calendar_end_is_explicit(self):
        now=NOW.replace(month=11,day=30)
        result=p.build(row(),fixture(),now);self.assertIn('beyond',result['calendar_warning'])
        self.assertIsNone(result['plan'])

if __name__=='__main__':unittest.main()
