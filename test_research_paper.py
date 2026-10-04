import copy
from datetime import timedelta
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import research_paper as p
import research_desk as d
from test_research_contract import NOW,fixture,technical,quote


def desk(now=NOW, price=100, source=None, blocked=False):
    c=fixture();c['generated_at']=now.isoformat();c['as_of']=now.isoformat();c['expires_at']=(now+timedelta(hours=1)).isoformat()
    for item in c['sources']:item['verified_at']=now.isoformat()
    q=quote();q.update(price=price,source_as_of=(source or now-timedelta(minutes=5)).isoformat(),fetched_at=now.isoformat())
    if blocked:c['market_context'][0]['bias']='adverse'
    return d.build(c,{'rows':[technical()]},{'prices':[q]},now)

class PaperTests(unittest.TestCase):
    def test_candidate_idempotency_and_strict_future_observation(self):
        with tempfile.TemporaryDirectory() as root:
            a=p.update(desk(),'a',root,NOW);p.update(desk(),'a',root,NOW)
            self.assertEqual(a['candidate_count'],1);self.assertEqual(len(p.load(root)),1)
            for minutes,source in [(4,NOW-timedelta(minutes=1)),(5,NOW)]:
                at=NOW+timedelta(minutes=minutes)
                v=p.update(desk(at,source=source),'b'+str(minutes),root,at)
                self.assertEqual(v['counts']['awaiting_later_observation'],1)
            at=NOW+timedelta(minutes=10);v=p.update(desk(at),'c',root,at)
            self.assertEqual(v['counts']['entry_condition_observed'],1)
            self.assertEqual(v['verified_fills'],0);self.assertIsNone(v['win_rate'])
    def test_expiry_and_cancellation_retained_never_recreated(self):
        for cancel in (True,False):
            with tempfile.TemporaryDirectory() as root:
                p.update(desk(),'a',root,NOW);at=NOW+timedelta(minutes=10 if cancel else 15)
                v=p.update(desk(at,blocked=cancel),'b',root,at)
                key='cancelled_before_entry' if cancel else 'expired_unactivated'
                self.assertEqual(v['counts'][key],1)
                v=p.update(desk(at+timedelta(minutes=1)),'c',root,at+timedelta(minutes=1))
                self.assertEqual(v['candidate_count'],1)
    def test_touch_unresolved_with_causal_same_time_reload(self):
        with tempfile.TemporaryDirectory() as root:
            p.update(desk(),'a',root,NOW);at=NOW+timedelta(minutes=10)
            p.update(desk(at),'entry1',root,at)
            v=p.update(desk(at,price=94,source=NOW+timedelta(minutes=6)),'exit1',root,at)
            self.assertEqual(v['counts']['unresolved_level_touch'],1)
            cases=p.fold(p.load(root));self.assertEqual(next(iter(cases.values()))['status'],'unresolved_level_touch')
            self.assertIsNone(v['average_net_loss']);self.assertIsNone(v['drawdown'])
    def test_invalid_collector_observation_cannot_activate(self):
        for flag in ('conflicting_source_timestamp','non_monotonic_source_timestamp','invalid_source_fields'):
            with tempfile.TemporaryDirectory() as root:
                p.update(desk(),'a',root,NOW);at=NOW+timedelta(minutes=10);v=desk(at)
                v['rows'][0]['quote']['quality_flags']=[flag]
                result=p.update(v,'b',root,at)
                self.assertEqual(result['counts']['awaiting_later_observation'],1)
    def test_crash_after_event_replays_summary_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            original=p.atomic
            def crash(path,value):
                if str(path).endswith('research_paper_summary.json'):raise OSError('crash')
                return original(path,value)
            with patch.object(p,'atomic',side_effect=crash):
                with self.assertRaises(OSError):p.update(desk(),'a',root,NOW)
            result=p.update(desk(),'a',root,NOW)
            self.assertEqual(result['candidate_count'],1);self.assertEqual(len(p.load(root)),1)
    def test_corrupted_event_and_invalid_transition_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            p.update(desk(),'a',root,NOW)
            path=next(Path(root).rglob('paper_events/*/*.json'));path.write_text('{}')
            with self.assertRaises((ValueError,KeyError)):p.update(desk(),'b',root,NOW)
    def test_no_historical_backfill_or_closed_creation(self):
        with tempfile.TemporaryDirectory() as root:
            at=NOW-timedelta(days=1);v=desk(at)
            result=p.update(v,'a',root,at);self.assertEqual(result['candidate_count'],0)
    def test_future_calendar_blocker_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(p,'deadline',side_effect=ValueError('Holding window exceeds verified exchange calendar')):
                v=p.update(desk(),'a',root,NOW)
            self.assertEqual(v['candidate_count'],0);self.assertEqual(v['enrollment_blockers'][0]['symbol'],'PRL')
        with self.assertRaises(ValueError):p.deadline(NOW.replace(month=11,day=19),30)

    def test_friday_session_expiry_never_crosses_lunch(self):
        friday=NOW+timedelta(days=4);friday=friday.replace(hour=6,minute=55)
        c=fixture();c['as_of']=friday.isoformat();c['expires_at']=(friday+timedelta(hours=1)).isoformat()
        self.assertEqual(p.expiry(c,friday),friday.replace(hour=7,minute=0))

if __name__=='__main__':unittest.main()
