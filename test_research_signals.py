import copy
from datetime import timedelta
import unittest
from unittest.mock import patch
import research_signals as signals
from test_research_contract import NOW,fixture,technical,quote

class PrimarySignalsTests(unittest.TestCase):
    def build(self,c=None,s=None,q=None,now=NOW):
        return signals.evaluate(fixture() if c is None else c,{'rows':[technical()]} if s is None else s,{'prices':[quote()]} if q is None else q,now)
    def test_exact_scope_attribution_and_counts(self):
        tech=technical();extra=copy.deepcopy(tech);extra['symbol']='UNREVIEWED'
        result=self.build(s={'rows':[tech,extra]})
        self.assertEqual(len(result['signals']),15);self.assertEqual(sum(result['counts'].values()),15)
        self.assertEqual(result['counts']['Ready for review'],1)
        row=result['signals'][0];self.assertEqual(row['status'],'Ready for review')
        self.assertEqual(row['technical']['signal'],'Buy');self.assertEqual(row['technical']['snapshot_hash'],tech['snapshot_hash'])
        self.assertEqual(row['plan']['observed_entry'],100);self.assertEqual(result['version'],'combined-signal-v1')
        self.assertIsNotNone(row['expires_at']);self.assertTrue(row['source_links'])
        self.assertNotIn('UNREVIEWED',[r['symbol'] for r in result['signals']])
    def test_all_research_vetoes_remove_primary_plan(self):
        changes=[lambda c:c['market_context'][0].update(bias='adverse'),lambda c:c['market_context'][1].update(bias='adverse'),
                 lambda c:c['stocks'][0]['news'].update(bias='adverse'),lambda c:c['stocks'][0]['sector'].update(bias='adverse'),
                 lambda c:c['stocks'][0]['fundamentals'].update(bias='adverse'),lambda c:c['stocks'][0]['fundamentals'].update(event_review_required=True),
                 lambda c:c['stocks'][0]['horizons']['swing'].update(stance='cautious'),
                 lambda c:c['stocks'][0]['news'].update(status='unavailable',source_ids=[],bias='unknown')]
        for change in changes:
            with self.subTest(change=change):
                c=fixture();change(c);out=self.build(c=c);row=out['signals'][0]
                self.assertEqual(out['counts']['Ready for review'],0);self.assertIsNone(row['plan']);self.assertIsNone(row['expires_at'])
                self.assertEqual(row['technical']['signal'],'Buy');self.assertTrue(row['reasons'])
    def test_stale_quotes_context_and_rerun_fail_closed(self):
        for delta in (timedelta(minutes=21),timedelta(hours=3),timedelta(days=1)):
            out=self.build(now=NOW+delta);self.assertEqual(out['counts']['Ready for review'],0);self.assertIsNone(out['signals'][0]['plan'])
        c=fixture();c['generated_at']=(NOW+timedelta(minutes=1)).isoformat()
        self.assertFalse(self.build(c=c)['research_current'])
    def test_duplicate_conflicting_inputs_never_choose_last(self):
        for key,item in [('rows',technical()),('prices',quote())]:
            for values in ([item,dict(item,price=101)],[dict(item,price=101),item]):
                out=self.build(**({'s':{key:values}} if key=='rows' else {'q':{key:values}}))
                self.assertEqual(out['counts']['Ready for review'],0);self.assertIsNone(out['signals'][0]['plan']);self.assertTrue(out['errors'])
    def test_future_malformed_envelopes_and_conflicting_quotes(self):
        for key in ('generated_at','checked_at','fetched_at'):
            for stamp in ('invalid',(NOW+timedelta(seconds=1)).isoformat()):
                out=self.build(q={'prices':[quote()],key:stamp});self.assertEqual(out['counts']['Ready for review'],0)
        for flag in ('conflicting_source_timestamp','non_monotonic_source_timestamp'):
            q=quote();q['quality_flags']=[flag];self.assertIsNone(self.build(q={'prices':[q]})['signals'][0]['plan'])
    def test_symbol_provenance_mismatch_and_missing_technical_never_upgrade(self):
        q=quote();q['source_url']='https://dps.psx.com.pk/company/SYS'
        self.assertEqual(self.build(q={'prices':[q]})['counts']['Ready for review'],0)
        self.assertEqual(self.build(s={'rows':[]})['counts']['Ready for review'],0)
        for label in ('Watch','Exit','Avoid'):
            t=technical();t['signal']=label;self.assertEqual(self.build(s={'rows':[t]})['counts']['Ready for review'],0)
    def test_source_inputs_not_mutated_and_deterministic(self):
        c,s,q=fixture(),{'rows':[technical()]},{'prices':[quote()]};before=copy.deepcopy((c,s,q))
        first=signals.evaluate(c,s,q,NOW);second=signals.evaluate(c,s,q,NOW)
        self.assertEqual(first,second);self.assertEqual((c,s,q),before)
    def test_closed_plan_is_reference_only_and_not_primary(self):
        with patch('session_calendar.is_live',return_value=False):out=self.build()
        self.assertEqual(out['signals'][0]['status'],'Watching');self.assertIsNone(out['signals'][0]['plan'])
    def test_review_older_than_hour_never_ready_even_if_ttl_valid(self):
        later=NOW+timedelta(minutes=61);q=quote();q['source_as_of']=(later-timedelta(minutes=5)).isoformat();q['fetched_at']=later.isoformat()
        out=self.build(q={'prices':[q]},now=later)
        self.assertEqual(out['signals'][0]['status'],'Watching');self.assertIsNone(out['signals'][0]['plan'])

if __name__=='__main__':unittest.main()

class SignalEdgeTests(PrimarySignalsTests):
    def test_deadline_equality_and_contradictory_envelopes_withhold(self):
        q=quote();q['source_as_of']=(NOW-timedelta(minutes=20)).isoformat()
        self.assertIsNone(self.build(q={'prices':[q]})['signals'][0]['plan'])
        later=NOW+timedelta(hours=1);q=quote();q['source_as_of']=(later-timedelta(minutes=5)).isoformat();q['fetched_at']=later.isoformat()
        self.assertIsNone(self.build(q={'prices':[q]},now=later)['signals'][0]['plan'])
        old=(NOW-timedelta(days=1)).isoformat()
        for kwargs in ({'s':{'generated_at':old,'rows':[technical()]}},{'q':{'checked_at':old,'prices':[quote()]}}):
            self.assertIsNone(self.build(**kwargs)['signals'][0]['plan'])
    def test_malformed_guard_fails_closed(self):
        for guard in ([],['invalid'],'invalid',True,{'valid':'true'}):
            t=technical();t['research_guard']=guard
            self.assertIsNone(self.build(s={'rows':[t]})['signals'][0]['plan'])
    def test_recorded_ready_is_never_reused_after_expiry(self):
        before=self.build();later=self.build(now=NOW+timedelta(minutes=21))
        signals.attach_recorded(later,before)
        self.assertEqual(later['counts']['Ready for review'],0)
        change=later['current_changes'][0];self.assertEqual(change['previous_status'],'Ready for review')
        self.assertEqual(change['current_status'],'Watching');self.assertIsNone(later['signals'][0]['plan'])
        self.assertEqual(before['signals'][0]['status'],'Ready for review')
    def test_date_only_source_precision_is_preserved(self):
        c=fixture();c['sources'][0].update(published_at=None,published_date='2026-10-04',publication_precision='date')
        link=self.build(c=c)['signals'][0]['source_links'][0]
        self.assertIsNone(link['published_at']);self.assertEqual(link['published_date'],'2026-10-04')
