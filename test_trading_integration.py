"""Exercise actual Trading desk/header/watchlist/detail wiring against one evaluation."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).parent
CODE='''
import streamlit as st
import remote_data
from unittest.mock import patch
from test_research_contract import fixture,technical,quote,NOW
from pathlib import Path
context=fixture()
SNAPSHOT={'rows':[technical()]}
DATA={'research_context.json':context,'research_quotes.json':{'prices':[quote()]}}
source=Path('dashboard.py').read_text()
header=source[source.index('# One uncached evaluation'):source.index('# Staleness banner')]
header=header.replace('snapshot=({} if _snapshot_fallback else _snapshot))','snapshot=({} if _snapshot_fallback else _snapshot), now=NOW)')
_snapshot=SNAPSHOT;_snapshot_fallback=False
regime='risk-on';regime_pill=lambda x:x;_last_updated_html='source age preserved'
with patch.object(remote_data,'fetch_json',side_effect=lambda path,**kw:DATA.get(path)):
    exec(header,globals())
(tab_desk,tab_watch,tab_stock)=st.tabs(['Trading desk','Watchlist','Stock detail'])
exec(source[source.index('with tab_desk:'):source.index('with tab_edge:')],globals())
exec(source[source.index('with tab_stock:'):source.index('with tab_hist:')],globals())
'''

class TradingIntegrationTests(unittest.TestCase):
    def test_header_cards_watchlist_and_detail_agree_and_repeat(self):
        at=AppTest.from_string(CODE).run(timeout=20)
        self.assertFalse(at.exception,[x.message for x in at.exception])
        self.assertTrue(any('1 ready for review' in c.value for c in at.caption))
        self.assertTrue(any('Ready for review' in m.value for m in at.subheader))
        at.radio(key='combined_signal_filter').set_value('Blocked').run()
        self.assertFalse(at.exception)
        at.selectbox(key='trading_detail_symbol').select('SYS').run()
        self.assertFalse(at.exception)
        at.button(key='refresh_trading_desk').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('1 ready for review' in c.value for c in at.caption))
    def test_legacy_buy_is_blocked_everywhere_after_research_veto(self):
        code=CODE.replace('context=fixture()',"context=fixture(); context['market_context'][0].update(bias='adverse',summary='Verified macro risk')")
        at=AppTest.from_string(code).run(timeout=20)
        self.assertFalse(at.exception,[x.message for x in at.exception])
        self.assertTrue(any('0 ready for review' in c.value for c in at.caption))
        self.assertFalse(any('Observed entry' in m.label for m in at.metric))
        text=' '.join(m.value for m in at.markdown)
        self.assertIn('Verified macro risk',text)
    def test_no_legacy_card_path_and_one_bundle_passed_to_research(self):
        source=(ROOT/'dashboard.py').read_text()
        desk=source[source.index('with tab_desk:'):source.index('with tab_edge:')]
        self.assertNotIn('show_swing',desk);self.assertNotIn('intraday_momentum.show',desk)
        self.assertIn('research_desk.show(st, bundle=_research_bundle)',source)
        detail=source[source.index('with tab_stock:'):source.index('with tab_hist:')]
        self.assertNotIn('db.last_run',detail);self.assertIn("_combined['signals']",detail)
        self.assertIn('min(60, int(getattr',source)

if __name__=='__main__':unittest.main()
