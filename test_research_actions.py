from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import research_actions as a
from test_research_paper import desk
from test_research_contract import NOW

class ActionTests(unittest.TestCase):
    def test_action_states_and_no_blocked_levels(self):
        current=desk();r=a.action(current['rows'][0],current);self.assertEqual(r['status'],'Ready for review')
        blocked=desk(blocked=True);r=a.action(blocked['rows'][0],blocked)
        self.assertEqual(r['status'],'Blocked');self.assertIsNone(r['entry']);self.assertIn('Macro risk',r['condition'])
    def test_expiry_bound_and_health_closed_vs_live(self):
        current=desk();r=a.action(current['rows'][0],current)
        self.assertEqual(r['entry_review_due'],(NOW+timedelta(minutes=15)).isoformat())
        self.assertTrue(any(x['id']=='collection-stale' for x in a.health(current,now=NOW)))
        current['market_open']=False
        self.assertFalse(any(x['id']=='collection-stale' for x in a.health(current,now=NOW)))
    def test_activity_baseline_repeat_change_and_idempotency(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(a.update_activity(desk(),'a',root,NOW)['items'])
            self.assertFalse(a.update_activity(desk(),'b',root,NOW)['items'])
            now=NOW+timedelta(minutes=5)
            v=a.update_activity(desk(now,blocked=True),'c',root,now)
            self.assertEqual(len(v['items']),15)
            w=a.update_activity(desk(now,blocked=True),'c',root,now);self.assertEqual(v,w)
            self.assertEqual(w['items'][0]['before']['swing'],'Swing setup for review')
            self.assertIn('Caution',w['items'][0]['after']['swing'])
    def test_corrupt_activity_refuses_silent_reset(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'research_activity.json').write_text('{')
            with self.assertRaises(ValueError):a.update_activity(desk(),'a',root,NOW)

if __name__=='__main__':unittest.main()

class CalculatorIntegrationTests(unittest.TestCase):
    def test_current_liquidity_requires_all_dates_guard_and_clock(self):
        import copy
        payload={'as_of_session':'2026-10-02','generated_at':NOW.isoformat()}
        row={'as_of_session':'2026-10-02','research_guard':{'valid':True},'liquidity':{'status':'available','end_session':'2026-10-02'}}
        plan={'decision_session':'2026-10-02'}
        self.assertTrue(a.sizing_liquidity_current(payload,row,plan,NOW))
        for key in ('as_of_session','generated_at'):
            bad=dict(payload);bad[key]='2026-10-01' if key=='as_of_session' else (NOW+timedelta(minutes=1)).isoformat()
            self.assertFalse(a.sizing_liquidity_current(bad,row,plan,NOW))
        bad=copy.deepcopy(row);bad['liquidity']['end_session']='2026-10-01'
        self.assertFalse(a.sizing_liquidity_current(payload,bad,plan,NOW))
        self.assertFalse(a.sizing_liquidity_current(payload,row,{'decision_session':'2026-10-01'},NOW))
    def test_sector_concentration_does_not_infer_holdings(self):
        v=a.concentration({'PRL':100,'NRL':200,'SYS':100},1000)
        self.assertEqual(v['capital_pct'],40);self.assertFalse(v['over_capital'])
        self.assertEqual(v['sectors'][0]['value'],300)
        self.assertTrue(a.concentration({'PRL':1100},1000)['over_capital'])
        for value in (True,float('inf'),-1):
            with self.assertRaises(ValueError):a.concentration({'PRL':value},1000)

    def test_concentration_overflow_and_valid_large_ratio(self):
        self.assertEqual(a.concentration({'PRL':1e308},1e308)['capital_pct'],100)
        with self.assertRaises(ValueError):a.concentration({'PRL':1e308,'NRL':1e308},1e308)
        with self.assertRaises(ValueError):a.concentration({'PRL':1e308},1e-308)
