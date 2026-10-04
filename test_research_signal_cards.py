"""UI regressions for canonical combined signals, without a second data load."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import research_signal_cards as cards
from research_contract import UNIVERSE


def fixture():
    signals = []
    for index, symbol in enumerate(UNIVERSE):
        status = cards.STATUSES[index % 3]
        reasons = (['Current evidence allows a spread and cost review'] if status == 'Ready for review' else
                   ['Wait: price outside validated entry zone'] if status == 'Watching' else
                   ['Geopolitical risk: source-linked uncertainty', 'Research review expired', 'Sector review unavailable'])
        signals.append({
            'symbol': symbol, 'status': status, 'reason': '; '.join(reasons), 'reasons': reasons,
            'swing_state': 'Swing setup for review' if status == 'Ready for review' else 'Caution: research risk',
            'intraday_state': 'Confirmed delayed-data watch', 'investment_state': 'Long-term research: cautious',
            'plan': {'observed_entry': 123.45, 'reference_entry': 120.0, 'stop': 110.0, 'target1': 170.0,
                     'target2': 190.0, 'buy_zone_low': 122.0, 'buy_zone_high': 124.0} if index % 3 == 0 else None,
            'expires_at': '2026-10-05T05:20:00+00:00',
            'quote': {'price': 123.45, 'source_as_of': '2026-10-05T04:55:00+00:00',
                      'fetched_at': '2026-10-05T05:00:00+00:00', 'fresh': index % 3 != 2,
                      'source_url': 'https://dps.psx.com.pk/company/' + symbol},
            'technical': {'signal': 'Buy', 'strategy_version': 'technical-v8', 'config_hash': 'c' * 64,
                          'snapshot_hash': 'd' * 64, 'decision_session': '2026-10-02',
                          'run_time': '2026-10-02T14:00:00+00:00', 'reason': 'Original technical trend'},
            'source_links': [{'title': 'Company announcement', 'url': 'https://dps.psx.com.pk/company/' + symbol}],
            'research_as_of': '2026-10-05T04:45:00+00:00',
            'research_expires_at': '2026-10-05T06:00:00+00:00',
        })
    return {'version': cards.VERSION, 'evaluated_at': '2026-10-05T05:05:00+00:00',
            'market_open': True, 'research_current': True,
            'context_as_of': '2026-10-05T04:45:00+00:00', 'context_expires_at': '2026-10-05T06:00:00+00:00',
            'signals': signals, 'counts': {status: 5 for status in cards.STATUSES}}


def activity():
    return {'checked_at': '2026-10-05T05:04:00+00:00', 'items': [{
        'symbol': 'SYS', 'recorded_at': '2026-10-05T05:03:00+00:00', 'title': 'SYS conditions changed',
        'detail': 'Research expiry removed entry eligibility.',
        'before': {'primary_status': 'Ready for review', 'swing': 'Swing setup for review'},
        'after': {'primary_status': 'Blocked', 'swing': 'No current setup',
                  'blocked_reasons': ['Research review expired']},
    }]}


def app(payload=None, include_table=False, change_log=None):
    return ("import streamlit as st\nimport research_signal_cards as cards\n"
            'payload = ' + repr(payload or fixture()) + '\n'
            'cards.show(st, payload, activity=' + repr(change_log) + ', journal={"checked_at": payload.get("evaluated_at")},'
            ' collection={"checked_at": payload.get("evaluated_at"), "current_usable_points": 15})\n' +
            ('cards.show_table(st, payload)\n' if include_table else ''))


class SignalCardsTests(unittest.TestCase):
    def test_filters_preserve_canonical_order_counts_and_repeat_navigation(self):
        at = AppTest.from_string(app(include_table=True)).run(timeout=20)
        self.assertFalse(at.exception, [x.message for x in at.exception])
        self.assertEqual([metric.value for metric in at.metric], ['5', '5', '5'])
        self.assertEqual([x.value.split(' · ')[0] for x in at.subheader][1:],
                         [signal['symbol'] for signal in fixture()['signals']])
        self.assertEqual(len(at.dataframe[0].value), 15)
        for status, expected in [('Blocked', 5), ('Ready for review', 5), ('Watching', 5), ('All', 15),
                                 ('Blocked', 5), ('All', 15)]:
            at.radio(key=cards.FILTER_KEY).set_value(status).run()
            self.assertFalse(at.exception, [x.message for x in at.exception])
            self.assertEqual(len(at.subheader) - 1, expected)
            self.assertEqual([metric.value for metric in at.metric], ['5', '5', '5'])
            actual = [x.value for x in at.subheader][1:]
            desired = [signal['symbol'] + ' · ' + signal['status'] for signal in fixture()['signals']
                       if status == 'All' or signal['status'] == status]
            self.assertEqual(actual, desired)

    def test_nonready_and_malformed_plans_do_not_leak_levels(self):
        payload = fixture()
        for signal in payload['signals']:
            signal['status'] = 'Blocked'
            signal['plan'] = {'observed_entry': 8765.43, 'stop': 8000, 'target1': 9100, 'target2': 9800}
        payload['counts'] = {'Ready for review': 0, 'Watching': 0, 'Blocked': 15}
        at = AppTest.from_string(app(payload, include_table=True)).run(timeout=20)
        self.assertFalse(at.exception)
        rendered = ' '.join(x.value for x in [*at.markdown, *at.caption])
        self.assertNotIn('8,765', rendered)
        for column in ('Observed entry reference (PKR)', 'Stop reference (PKR)', 'Target 1 (PKR)', 'Target 2 (PKR)'):
            self.assertTrue(at.dataframe[0].value[column].isna().all())
        for plan in ({'reference_entry': 123, 'stop': 110, 'target1': 140},
                     {'observed_entry': float('nan'), 'stop': 110, 'target1': 140},
                     {'observed_entry': 123, 'stop': 124, 'target1': 140},
                     {'observed_entry': 123, 'stop': 110, 'target1': 140, 'target2': 130}):
            self.assertIsNone(cards._eligible_plan({'status': 'Ready for review', 'plan': plan}))

    def test_ready_entry_is_observed_not_reference_close(self):
        payload = fixture()
        payload['signals'][0]['plan']['reference_entry'] = 99999.0
        at = AppTest.from_string(app(payload)).run(timeout=20)
        self.assertFalse(at.exception)
        rendered = ' '.join(x.value for x in [*at.markdown, *at.caption])
        self.assertIn('Observed entry reference 123', rendered)
        self.assertNotIn('99,999', rendered)

    def test_technical_buy_only_appears_as_original_attribution(self):
        at = AppTest.from_string(app()).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertFalse(any('Buy' in element.value for element in at.subheader))
        attributed = [x for x in at.expander if x.label.startswith('Original technical attribution')]
        self.assertEqual(len(attributed), 15)
        self.assertTrue(all(any('Original technical signal: Buy' in element.value for element in expander.markdown)
                            for expander in attributed))
        self.assertTrue(any('Geopolitical risk:' in x.value for x in at.markdown))
        self.assertTrue(any('Research review expired' in x.value for x in at.markdown))
        self.assertTrue(any('Sector review unavailable' in x.value for x in at.markdown))

    def test_timestamps_delays_expiry_and_historical_quote_are_explicit(self):
        at = AppTest.from_string(app()).run(timeout=20)
        self.assertFalse(at.exception)
        captions = ' '.join(x.value for x in at.caption)
        self.assertIn('05 Oct 2026, 10:05:00 PKT', captions)
        self.assertIn('05 Oct 2026, 09:55:00 PKT', captions)
        self.assertIn('05 Oct 2026, 10:00:00 PKT', captions)
        self.assertIn('Review expires: 05 Oct 2026, 10:20:00 PKT', captions)
        self.assertIn('historical / stale observation', captions)
        self.assertIn('Nominal 5-minute source delay', captions)
        self.assertTrue(any('Intraday · watch-only' in x.value for x in at.markdown))
        self.assertEqual(cards.pkt('2026-10-05T05:05:00'), 'Unavailable')
        self.assertEqual(cards.pkt('not a time'), 'Unavailable')

    def test_external_text_and_links_are_safe(self):
        payload = fixture()
        attack = '<script>alert(1)</script> [click](javascript:alert(1)) **Buy now**'
        payload['signals'][0]['reason'] = attack
        payload['signals'][0]['reasons'] = [attack]
        payload['signals'][0]['source_links'] = [{'title': attack, 'url': 'javascript:alert(1)'},
                                                {'title': 'Credential link', 'url': 'https://owner:secret@example.com'},
                                                {'title': 'Safe source', 'url': 'https://example.com/research'}]
        payload['signals'][0]['technical']['reason'] = attack
        at = AppTest.from_string(app(payload)).run(timeout=20)
        self.assertFalse(at.exception)
        rendered = [element for element in at.markdown if '&lt;script&gt;' in element.value]
        self.assertTrue(rendered)
        self.assertTrue(all(not element.proto.allow_html for element in at.markdown))
        self.assertTrue(all(r'\[click\]' in element.value for element in rendered))
        urls = [element.proto.url for element in at.get('link_button')]
        self.assertIn('https://example.com/research', urls)
        self.assertFalse(any(url.startswith('javascript:') or 'owner:secret' in url for url in urls))
        for bad in ('javascript:alert(1)', 'data:text/html,<h1>test</h1>', '//example.com',
                    'https://example.com\nunsafe', 'https://owner:secret@example.com', 'https://[broken'):
            self.assertIsNone(cards._url(bad))

    def test_revocations_have_clear_log_through_time(self):
        at = AppTest.from_string(app(change_log=activity())).run(timeout=20)
        self.assertFalse(at.exception)
        changes = next(x for x in at.expander if x.label == 'Important changes and revocations')
        self.assertTrue(any('SYS: entry review revoked' in x.value for x in changes.markdown))
        self.assertTrue(any('Recorded changes through: 05 Oct 2026, 10:04:00 PKT' in x.value for x in changes.caption))
        self.assertTrue(any('Before: Ready for review · After: Blocked' in x.value for x in changes.caption))
        log = activity()
        log['checked_at'] = '2026-10-05T05:06:00+00:00'
        at = AppTest.from_string(app(change_log=log)).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(any('activity checkpoint clock' in x.value for x in at.warning))
        self.assertFalse(any('entry review revoked' in x.value for x in at.markdown))

    def test_refresh_replaces_ready_levels_with_revoked_status(self):
        before = fixture()
        after = copy.deepcopy(before)
        after['signals'][0].update(status='Blocked', plan=None, reason='New global risk veto', reasons=['New global risk veto'])
        after['counts'] = {'Ready for review': 4, 'Watching': 5, 'Blocked': 6}
        code = ('import streamlit as st\nimport research_signal_cards as cards\n'
                'if st.button("Load newer decision"): st.session_state["newer"] = True\n'
                'payload = ' + repr(after) + ' if st.session_state.get("newer") else ' + repr(before) + '\n'
                'cards.show(st, payload)\n')
        at = AppTest.from_string(code).run(timeout=20)
        at.radio(key=cards.FILTER_KEY).set_value('Ready for review').run()
        self.assertTrue(any(x.value == 'PRL · Ready for review' for x in at.subheader))
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertFalse(any(x.value.startswith('PRL') for x in at.subheader))
        self.assertEqual([x.value for x in at.metric], ['4', '5', '6'])
        at.radio(key=cards.FILTER_KEY).set_value('Blocked').run()
        self.assertTrue(any(x.value == 'PRL · Blocked' for x in at.subheader))
        self.assertTrue(any('New global risk veto' in x.value for x in at.markdown))

    def test_live_reevaluation_withdrawal_is_distinct_from_recorded_alerts(self):
        payload = fixture()
        payload.update(recorded_at='2026-10-05T05:00:00+00:00', recorded_checkpoint='checkpoint-123',
                       sourcehash_coherence=False, source_hashes={'context_sha256': 'abc123'},
                       current_changes=[{'symbol': 'SYS', 'previous_status': 'Ready for review',
                                         'current_status': 'Blocked', 'reason': 'Quote freshness expired'}])
        at = AppTest.from_string(app(payload)).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(any('SYS: Entry review withdrawn since recorded checkpoint' in x.value for x in at.warning))
        captions = ' '.join(x.value for x in at.caption)
        self.assertIn('not a sell instruction or a filled exit', captions)
        self.assertIn('not additional stored alerts', captions)
        self.assertIn('05 Oct 2026, 10:00:00 PKT', captions)
        self.assertIn('checkpoint', captions)
        self.assertIn('abc123', captions)
        self.assertTrue(any(x.value == 'SYS · Blocked' for x in at.subheader))

    def test_source_publication_date_only_and_verified_time_are_distinct(self):
        payload = fixture()
        payload['signals'][0]['source_links'] = [
            {'title': 'Date-only source', 'url': 'https://example.org/date',
             'publication_precision': 'date', 'published_date': '2026-10-04', 'published_at': None,
             'verified_at': '2026-10-05T04:30:00+00:00'},
            {'title': 'Timed source', 'url': 'https://example.org/timed',
             'published_at': '2026-10-05T04:15:00+00:00', 'verified_at': '2026-10-05T04:45:00+00:00'},
        ]
        at = AppTest.from_string(app(payload)).run(timeout=20)
        self.assertFalse(at.exception)
        captions = ' '.join(x.value for x in at.caption)
        self.assertIn('Published: 04 Oct 2026', captions)
        self.assertIn('date only; publication time unavailable', captions)
        self.assertNotIn('04 Oct 2026, 00:00:00', captions)
        self.assertIn('Source verified: 05 Oct 2026, 09:30:00 PKT', captions)
        self.assertIn('Published: 05 Oct 2026, 09:15:00 PKT', captions)

    def test_standalone_card_can_repeat_without_duplicate_widget_ids(self):
        payload = fixture()
        code = ('import streamlit as st\nimport research_signal_cards as cards\npayload = ' + repr(payload) + '\n'
                'cards.show(st, payload)\ncards.show_card(st, payload["signals"][0], payload)\n'
                'cards.show_table(st, payload)\n')
        at = AppTest.from_string(code).run(timeout=20)
        self.assertFalse(at.exception)
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.subheader), 17)

    def test_renderer_uses_passed_object_without_remote_reads_or_mutation(self):
        payload = fixture()
        original = copy.deepcopy(payload)
        self.assertEqual(len(cards.table_rows(payload)), 15)
        self.assertEqual(payload, original)
        source = Path(cards.__file__).read_text()
        self.assertNotIn('import remote_data', source)
        self.assertNotIn('fetch_json(', source)
        self.assertNotIn('unsafe_allow_html=True', source)
        with patch('remote_data.fetch_json', side_effect=AssertionError('A renderer may not fetch its own signals')):
            at = AppTest.from_string(app()).run(timeout=20)
        self.assertFalse(at.exception)

    def test_empty_filter_and_unsupported_payload(self):
        payload = fixture()
        for signal in payload['signals']:
            signal['status'] = 'Blocked'
            signal['plan'] = None
        payload['counts'] = {'Ready for review': 0, 'Watching': 0, 'Blocked': 15}
        at = AppTest.from_string(app(payload)).run(timeout=20)
        at.radio(key=cards.FILTER_KEY).set_value('Ready for review').run()
        self.assertFalse(at.exception)
        self.assertTrue(any('No stocks have this combined status' in x.value for x in at.info))
        at = AppTest.from_string(app({'version': 'old'})).run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(at.error)


if __name__ == '__main__':
    unittest.main()
