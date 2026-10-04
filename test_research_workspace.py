"""Interactive dashboard smoke with fixed dates and injected public evidence."""
import unittest
from streamlit.testing.v1 import AppTest

CODE = '''
import streamlit as st
from unittest.mock import patch
from datetime import datetime
import research_desk as d
import remote_data
from test_research_contract import NOW,fixture,technical,quote
class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return NOW
context=fixture()
comparison={'as_of_session':'2026-10-02','generated_at':NOW.isoformat(),'stocks':[
    {'symbol':'PRL','sector':'Refinery','status':'available','as_of_session':'2026-10-02',
     'research_guard':{'valid':True},'returns':{},'sector_peers':{},
     'liquidity':{'status':'available','end_session':'2026-10-02','median20_volume_shares':1000000.0}}]}
mapping={'research_context.json':context,'dashboard_snapshot.json':{'rows':[technical()]},
         'research_quotes.json':{'prices':[quote()]},'research_comparisons.json':comparison}
with patch.object(remote_data,'fetch_json',side_effect=lambda path,**kw:mapping.get(path)), \\
     patch('research_desk.datetime',Clock), patch('research_actions.datetime',Clock), \\
     patch('session_calendar.last_completed',return_value='2026-10-02'):
    d.show(st)
'''

class WorkspaceTests(unittest.TestCase):
    def test_filter_calculator_and_repeat_navigation(self):
        at=AppTest.from_string(CODE).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertEqual([x.value for x in at.metric][:3],['1','0','14'])
        at.button(key='refresh_research_data').click().run()
        self.assertFalse(at.exception)
        self.assertEqual([x.value for x in at.metric][:3],['1','0','14'])
        at.radio(key='research_action_filter').set_value('Ready for review').run()
        self.assertFalse(at.exception)
        for label,value in [('Capital',1000000.0),('Available cash',100000.0),('Modeled loss budget',1000.0),
                            ('Estimated fee per side (basis points)',15.0),('Adverse entry slippage (basis points)',20.0),
                            ('Adverse exit slippage (basis points)',20.0)]:
            next(x for x in at.number_input if x.label==label).set_value(value)
        next(x for x in at.checkbox if 'fee, tax' in x.label).check()
        next(x for x in at.button if x.label=='Calculate scenario').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('Scenario quantity:' in x.value for x in at.markdown))
        at.selectbox(key='combined_research_symbol').select('SYS').run()
        self.assertFalse(at.exception)
        self.assertFalse(any(x.label=='Calculate scenario' for x in at.button))
        at.selectbox(key='combined_research_symbol').select('PRL').run()
        self.assertFalse(at.exception)
    def test_missing_context_and_missing_cost_acknowledgement(self):
        at=AppTest.from_string(CODE.replace('context=fixture()','context=None')).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertFalse(any(x.label=='Calculate scenario' for x in at.button))
        at=AppTest.from_string(CODE).run(timeout=20)
        next(x for x in at.button if x.label=='Calculate scenario').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('Confirm the cost assumptions' in x.value for x in at.warning))

    def test_new_tabs_available_chart_and_evidence_navigation(self):
        extra="""from test_research_charts import history
"""
        code=CODE.replace('context=fixture()',extra+'context=fixture()')
        code=code.replace('    d.show(st)',"    with patch('research_charts.read_history',side_effect=lambda database,symbol,cutoff: history() if symbol=='PRL' else {'symbol':symbol,'cutoff':cutoff,'limit':42,'raw_rows':[],'actions':[],'action_schema_complete':False,'read_errors':['Fixture no data']}):\n        d.show(st)")
        at=AppTest.from_string(code).run(timeout=20)
        self.assertFalse(at.exception,[x.message for x in at.exception])
        labels=[tab.label for tab in at.tabs]
        for label in ('Morning brief','Annotated daily chart','Two-month planner','Why this status?'):
            self.assertIn(label,labels)
        self.assertTrue(at.get('plotly_chart'))
        self.assertTrue(any('two calendar months' in x.value.lower() for x in at.caption))
        at.selectbox(key='combined_research_symbol').select('SYS').run()
        self.assertFalse(at.exception)
        self.assertTrue(any('No validated full daily candles' in x.value for x in at.info))
        at.selectbox(key='combined_research_symbol').select('PRL').run()
        self.assertFalse(at.exception)
        self.assertTrue(at.get('plotly_chart'))

    def test_row_selection_uses_filtered_display_order(self):
        import research_workspace as w
        shown=[{'symbol':'NRL'},{'symbol':'PRL'}]
        self.assertEqual(w.selected_stock(shown,{'selection':{'rows':[0]}}),'NRL')
        self.assertEqual(w.selected_stock(shown,{'selection':{'rows':[1]}}),'PRL')
        for value in ([],[-1],[2],[True],['0']):
            self.assertIsNone(w.selected_stock(shown,{'selection':{'rows':value}}))

if __name__=='__main__':unittest.main()
