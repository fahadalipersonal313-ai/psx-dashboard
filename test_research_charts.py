"""Read-only daily-chart tests. Fixtures use isolated temporary databases."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

import research_calendar
import research_charts as charts
import research_desk
from test_research_contract import NOW, fixture, quote, technical

SOURCE = 'PSX DPS historical (official)'
CUTOFF = '2026-10-02'


def bars():
    return [{'symbol': 'PRL', 'date': day, 'open': 99., 'high': 101., 'low': 98.,
             'close': 100., 'volume': 500000., 'source': SOURCE}
            for day in research_calendar.expected(CUTOFF, 42)]


def history():
    return {'symbol': 'PRL', 'cutoff': CUTOFF, 'limit': 42, 'raw_rows': bars(),
            'actions': [], 'action_schema_complete': True, 'read_errors': []}


def row(context=None, tech=None):
    return research_desk.build(context or fixture(), {'rows': [tech or technical()]},
                               {'prices': [quote()]}, NOW)['rows'][0]


def action(**changes):
    result = {'symbol': 'PRL', 'ex_date': '2026-09-29', 'kind': 'dividend',
              'factor': 1., 'volume_factor': 1., 'verified': 1,
              'source': 'https://dps.psx.com.pk/download/company/PRL.pdf',
              'known_at': '2026-09-20T00:00:00+00:00'}
    result.update(changes)
    return result


class PrepareTests(unittest.TestCase):
    def chart(self, r=None, context=None, h=None, now=NOW):
        return charts.prepare(r or row(), context or fixture(), h or history(), now)

    def assert_withheld(self, h=None, r=None, context=None, term=None, now=NOW):
        result = self.chart(r=r, context=context, h=h, now=now)
        self.assertIsNone(result['overlay'])
        if term:
            self.assertIn(term, '; '.join(result['overlay_reasons']))
        return result

    def test_eligible_levels_begin_at_evaluation_not_historical_start(self):
        result = self.chart()
        self.assertEqual(result['overlay']['reference_entry'], 100.)
        self.assertEqual(result['overlay']['start'], NOW.isoformat())
        self.assertGreater(result['overlay']['start'][:10], result['rows'][-1]['date'])
        plot = charts.figure(result)
        self.assertEqual(len(plot.data[0].x), 42)
        self.assertEqual(plot.data[0].type, 'candlestick')
        self.assertEqual(plot.data[1].type, 'bar')
        self.assertTrue(all(shape.x0 == NOW.isoformat() for shape in plot.layout.shapes))
        self.assertFalse(plot.layout.xaxis.rangebreaks)
        self.assertEqual(plot.layout.xaxis.type, 'date')
        self.assertIsNone(plot.layout.title.text)
        self.assertEqual(plot.layout.legend.yanchor,'bottom')
        self.assertEqual(plot.layout.legend.y,1.02)

    def test_inputs_are_not_mutated(self):
        r, c, h = row(), fixture(), history()
        before = copy.deepcopy((r, c, h))
        charts.prepare(r, c, h, NOW)
        self.assertEqual(before, (r, c, h))

    def test_both_exact_official_sources_accepted(self):
        h = history(); h['raw_rows'][0]['source'] = 'PSX mkt_summary historical (official download)'
        self.assertIsNotNone(self.chart(h=h)['overlay'])

    def test_source_lookalike_missing_and_partial_intraday_are_rejected(self):
        for source in ('unofficial PSX historical', 'official intraday', 'PSX DPS end-of-day', None):
            with self.subTest(source=source):
                h = history(); h['raw_rows'][0]['source'] = source
                result = self.assert_withheld(h=h, term='untrusted')
                self.assertEqual(len(result['rows']), 41)
                self.assertTrue(result['rejected_rows'])

    def test_malformed_full_bar_is_not_repaired_or_used(self):
        for key, value in [('low', None), ('close', 102), ('open', float('nan')),
                           ('high', True), ('volume', -1), ('volume', '100'), ('volume', 99.5)]:
            with self.subTest(key=key, value=value):
                h = history(); h['raw_rows'][8][key] = value
                result = self.assert_withheld(h=h)
                self.assertEqual(len(result['rows']), 41)
                self.assertEqual(result['rejected_rows'][0]['date'], h['raw_rows'][8]['date'])

    def test_missing_malformed_and_duplicate_dates_block(self):
        for day in (None, 'not-a-date', '9999-99-99', '2026-09-25T00:00:00'):
            with self.subTest(day=day):
                h = history(); h['raw_rows'][8]['date'] = day
                self.assert_withheld(h=h, term='malformed')
        h = history(); h['raw_rows'].append(dict(h['raw_rows'][4]))
        self.assert_withheld(h=h, term='malformed')

    def test_sparse_missing_session_stays_gap_and_does_not_borrow_older_bar(self):
        h = history(); removed = h['raw_rows'].pop(12)['date']
        h['raw_rows'].insert(0, {**bars()[0], 'date': '2026-08-03'})
        result = self.assert_withheld(h=h, term='missing')
        self.assertIn(removed, result['missing_dates'])
        self.assertEqual(len(result['rows']), 41)
        plot = charts.figure(result)
        self.assertNotIn(removed, plot.data[0].x)
        self.assertNotIn('2026-08-03', plot.data[0].x)
        self.assertEqual(len(plot.layout.shapes), 0)

    def test_zero_volume_is_not_invented_volume(self):
        h = history(); h['raw_rows'][2]['volume'] = 0
        self.assertEqual(self.chart(h=h)['rows'][2]['volume'], 0)

    def test_history_symbol_and_reference_close_must_match(self):
        h = history(); h['symbol'] = 'MEBL'
        self.assert_withheld(h=h, term='different stock')
        h = history(); h['raw_rows'][-1]['close'] = 99
        self.assert_withheld(h=h, term='reference price differs')

    def test_stale_and_future_history_withhold_levels_and_future_candles(self):
        h = history(); h['cutoff'] = '2026-10-01'; h['raw_rows'].pop()
        self.assert_withheld(h=h, term='must align')
        h = history(); h['cutoff'] = '2026-10-05'
        h['raw_rows'].append({**bars()[-1], 'date': '2026-10-05'})
        result = self.assert_withheld(h=h, term='future session')
        self.assertTrue(all(b['date'] <= CUTOFF for b in result['rows']))

    def test_cannot_trust_supplied_plan_or_research_guard(self):
        r = row(); r['plan']['target1'] = 999
        self.assert_withheld(r=r, term='differs')
        for key, value in [('snapshot_hash', 'wrong'), ('config_hash', 'wrong'), ('strategy_version', 'old')]:
            r = row(); r['technical'][key] = value
            self.assert_withheld(r=r)
        r = row(); r['technical']['research_guard']['valid'] = False
        self.assert_withheld(r=r, term='audit incomplete')

    def test_missing_blocked_or_removed_plan_never_overlays(self):
        for field, value in [('missing', ['No source']), ('blocked_reasons', ['Material risk']), ('plan', None)]:
            r = row(); r[field] = value
            self.assert_withheld(r=r)
        c = fixture(); c['market_context'][0]['bias'] = 'adverse'
        self.assert_withheld(context=c, term='independent current-plan')
        c = fixture(); c['expires_at'] = NOW.isoformat()
        self.assert_withheld(context=c, term='independent current-plan')

    def test_technical_run_cannot_precede_bar_completion_or_be_future(self):
        for value in ('2026-10-02T00:00:00+00:00', (NOW + timedelta(minutes=1)).isoformat()):
            r = row(); r['technical']['run_time'] = value
            self.assert_withheld(r=r)

    def test_short_and_uncovered_calendar_are_explicitly_blocked(self):
        h = history(); h['limit'] = 10
        self.assert_withheld(h=h, term='42-session')
        h = history(); h['cutoff'] = '2025-10-02'
        self.assert_withheld(h=h, term='Independent session coverage')
        h = history(); h['cutoff'] = '2026-10-03'
        self.assert_withheld(h=h, term='not an independently expected')

    def test_unresolved_action_blocks_even_without_beyond_circuit_gap(self):
        h = history(); h['actions'] = [action(verified=0)]
        result = self.assert_withheld(h=h, term='Unresolved')
        self.assertEqual(result['actions'][0]['ex_date'], '2026-09-29')
        self.assertTrue(any('remain raw' in w for w in result['warnings']))

    def test_action_schema_missing_and_malformed_action_block(self):
        h = history(); h['action_schema_complete'] = False
        self.assert_withheld(h=h, term='verification fields unavailable')
        for changes in ({'known_at': None}, {'ex_date': None}, {'symbol': 'MEBL'}, {'source': None}, {'factor': None}):
            h = history(); h['actions'] = [action(**changes)]
            self.assert_withheld(h=h)

    def test_future_known_actions_are_withheld_without_leaking_action_metadata(self):
        for known in ((NOW + timedelta(days=1)).isoformat(), '2026-10-06', '2026-10-05'):
            with self.subTest(known=known):
                h = history(); h['actions'] = [action(ex_date='2026-10-21', known_at=known)]
                result = self.assert_withheld(h=h, term='knowledge is not established')
                self.assertEqual(result['actions'], [])
                self.assertNotIn('2026-10-21', '; '.join(result['warnings']))
                self.assertFalse(any(trace.name == 'Banked action record' for trace in charts.figure(result).data))

    def test_action_factor_mismatch_duplicate_and_post_decision_block(self):
        h = history(); h['actions'] = [action(factor=.5)]
        self.assert_withheld(h=h, term='does not reconcile')
        h = history(); h['actions'] = [action(), action()]
        self.assert_withheld(h=h, term='Duplicate')
        h = history(); h['actions'] = [action(ex_date='2026-10-05')]
        self.assert_withheld(h=h, term='after the technical decision')

    def test_verified_action_warns_raw_prices_and_never_adjusts_rows(self):
        h = history(); h['actions'] = [action()]
        result = self.chart(h=h)
        self.assertIsNotNone(result['overlay'])
        self.assertEqual([b['close'] for b in result['rows']], [100.] * 42)
        self.assertTrue(any('raw and unadjusted' in w for w in result['warnings']))

    def test_raw_close_or_open_discontinuity_blocks_even_with_verified_action(self):
        for key in ('close', 'open'):
            h = history(); h['raw_rows'][20].update({key: 112., 'high': 113.})
            self.assert_withheld(h=h, term='Raw price discontinuity')

    def test_source_date_only_is_not_a_fake_midnight_timestamp(self):
        c = fixture(); c['sources'][0].update(published_at=None, published_date='2026-09-29', publication_precision='date')
        result = self.chart(context=c)
        event = result['events'][0]
        self.assertEqual(event['type'], 'announcement')
        self.assertEqual(event['date'], '2026-09-29')
        self.assertEqual(event['precision'], 'date')
        self.assertIsNone(event['published_at'])
        self.assertIsNone(event['known_at'])
        self.assertEqual(event['verified_at'], NOW.isoformat())
        plot = charts.figure(result)
        self.assertTrue(any('date only' in t for trace in plot.data for t in (trace.text or []) if isinstance(t, str)))

    def test_future_scheduled_event_is_distinct_from_publication_and_known_at(self):
        c = fixture(); c['stocks'][0]['fundamentals']['events'] = [
            {'kind': 'agm', 'date': '2026-11-02', 'known_at': NOW.isoformat(), 'source_ids': ['s']}]
        result = self.chart(context=c)
        event = next(e for e in result['events'] if e['type'] == 'event')
        self.assertEqual(event['timing'], 'scheduled_future')
        self.assertEqual(event['precision'], 'date')
        self.assertEqual(event['known_at'], NOW.isoformat())
        self.assertNotIn('2026-11-02', charts.figure(result).data[0].x)
        self.assertEqual(len(result['events']), 2)

    def test_undated_announcement_is_retained_without_invented_date(self):
        c = fixture(); c['sources'][0].pop('published_at')
        event = self.chart(context=c)['events'][0]
        self.assertIsNone(event['date'])
        self.assertEqual(event['precision'], 'unknown')

    def test_future_context_never_leaks_event_or_publication_metadata(self):
        c = fixture()
        tomorrow = NOW + timedelta(days=1)
        c.update(generated_at=tomorrow.isoformat(), expires_at=(NOW + timedelta(days=2)).isoformat())
        c['sources'][0].update(published_at=tomorrow.isoformat(), verified_at=tomorrow.isoformat())
        c['stocks'][0]['fundamentals']['events'] = [
            {'kind': 'agm', 'date': '2026-10-21', 'known_at': NOW.isoformat(), 'source_ids': ['s']}]
        import research_contract
        research_contract.validate(c)  # Valid schema does not imply point-in-time availability.
        result = self.assert_withheld(context=c)
        self.assertEqual(result['events'], [])
        self.assertTrue(any('future-dated' in text for text in result['warnings']))
        c['as_of'] = tomorrow.isoformat()
        result = self.assert_withheld(context=c)
        self.assertEqual(result['events'], [])

    def test_future_reference_clocks_cannot_be_overridden_by_event_known_at(self):
        # Independent source checks remain effective even if a schema reader is
        # relaxed in the future; no referenced clock may exceed evaluation.
        from unittest.mock import patch
        for field, value in [('verified_at', (NOW + timedelta(minutes=1)).isoformat()),
                             ('published_at', (NOW + timedelta(minutes=1)).isoformat()),
                             ('published_date', '2026-10-06')]:
            with self.subTest(field=field):
                c = fixture(); c['sources'][0][field] = value
                c['stocks'][0]['fundamentals']['events'] = [
                    {'kind': 'agm', 'date': '2026-10-21', 'known_at': NOW.isoformat(), 'source_ids': ['s']}]
                with patch.object(charts.contract, 'validate', return_value=c):
                    events, warnings = charts._events(c, 'PRL', NOW)
                self.assertEqual(events, [])
                self.assertTrue(any('future-dated' in text for text in warnings))

    def test_expired_event_metadata_is_explicitly_historical_and_dated(self):
        c = fixture(); c['expires_at'] = (NOW + timedelta(hours=1)).isoformat()
        c['stocks'][0]['fundamentals']['events'] = [
            {'kind': 'agm', 'date': '2026-10-21', 'known_at': NOW.isoformat(), 'source_ids': ['s']}]
        events, warnings = charts._events(c, 'PRL', NOW + timedelta(hours=2))
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e['context_status'] == 'expired' for e in events))
        self.assertTrue(all(e['context_as_of'] == NOW.isoformat() for e in events))
        self.assertTrue(all('expired research context' in e['note'] for e in events))
        self.assertTrue(any('not a current review' in text for text in warnings))

    def test_invalid_event_context_never_creates_annotations(self):
        c = fixture(); c['stocks'][0]['fundamentals']['events'] = [
            {'kind': 'agm', 'date': '2026-11-02', 'known_at': NOW.isoformat(), 'source_ids': ['unknown']}]
        result = self.assert_withheld(context=c)
        self.assertEqual(result['events'], [])

    def test_invalid_zone_cannot_be_drawn(self):
        t = technical(); t['buy_zone_low'] = 105
        r = row(tech=t)
        result = self.chart(r=r)
        self.assertIsNotNone(result['overlay'])
        self.assertIsNone(result['overlay']['buy_zone_low'])

    def test_malformed_action_collection_and_guard_are_fail_closed(self):
        h = history(); h['actions'] = None
        self.assert_withheld(h=h, term='missing or malformed')
        r = row(); r['technical']['research_guard'] = 'not a mapping'
        self.assert_withheld(r=r, term='failed validation')

    def test_streamlit_adapter_renders_without_exceptions(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("""
import streamlit as st
import research_charts as charts
from test_research_charts import row, history, fixture, NOW
charts.show(st, charts.prepare(row(), fixture(), history(), NOW))
""")
        app.run(timeout=15)
        self.assertFalse(app.exception)
        self.assertTrue(any('not a target date' in item.value for item in app.caption))
        self.assertTrue(any('publication' in item.value for item in app.markdown))

    def test_unapproved_symbol_and_naive_evaluation_rejected(self):
        r = row(); r['symbol'] = 'UNKNOWN'
        with self.assertRaises(ValueError): self.chart(r=r)
        with self.assertRaises(ValueError): self.chart(now=NOW.replace(tzinfo=None))


class ReadHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'isolated.db'

    def create(self, actions=True, action_columns=True, rows=None):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE daily_ohlc(symbol TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)')
            db.executemany('INSERT INTO daily_ohlc VALUES(?,?,?,?,?,?,?,?)',
                           [(b.get('symbol'), b.get('date'), *[b.get(k) for k in charts.DAILY_FIELDS], b.get('source')) for b in (rows or bars())])
            if actions:
                db.execute('CREATE TABLE corporate_actions(symbol TEXT,ex_date TEXT,kind TEXT,factor REAL' +
                           (',volume_factor REAL,verified INT,source TEXT,known_at TEXT)' if action_columns else ')'))

    def test_missing_path_is_not_created(self):
        result = charts.read_history(self.path, 'PRL', CUTOFF)
        self.assertFalse(self.path.exists())
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue(result['read_errors'])

    def test_none_path_is_unavailable_and_ui_still_renders(self):
        h = charts.read_history(None, 'PRL', CUTOFF)
        self.assertEqual(h['status'], 'unavailable')
        prepared = charts.prepare(row(), fixture(), h, NOW)
        self.assertEqual(prepared['rows'], [])
        self.assertIsNone(prepared['overlay'])
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("""
import streamlit as st
import research_charts as charts
from test_research_charts import row, fixture, NOW, CUTOFF
h = charts.read_history(None, 'PRL', CUTOFF)
charts.show(st, charts.prepare(row(), fixture(), h, NOW))
""")
        app.run(timeout=15)
        self.assertFalse(app.exception)
        self.assertTrue(any('No validated' in item.value for item in app.info))

    def test_successful_read_is_byte_for_byte_read_only(self):
        self.create()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = charts.read_history(self.path, 'PRL', CUTOFF)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(len(result['rows']), 42)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_old_action_schema_and_absent_table_are_explicit(self):
        for exists, columns in ((True, False), (False, False)):
            with self.subTest(exists=exists):
                self.create(actions=exists, action_columns=columns)
                result = charts.read_history(self.path, 'PRL', CUTOFF)
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['action_schema_complete'])
                self.assertEqual(len(result['rows']), 42)
                self.path.unlink()

    def test_malformed_date_cannot_hide_behind_sql_cutoff_or_prepare(self):
        rows = bars(); rows.append({**rows[0], 'date': 'zzzz-zz-zz'})
        self.create(rows=rows)
        h = charts.read_history(self.path, 'PRL', CUTOFF)
        self.assertEqual(h['status'], 'partial')
        prepared = charts.prepare(row(), fixture(), h, NOW)
        self.assertIsNone(prepared['overlay'])
        self.assertEqual(len(prepared['rejected_rows']), 1)

    def test_action_metadata_read_without_migration_or_repair(self):
        self.create()
        with sqlite3.connect(self.path) as db:
            a = action(verified=0)
            db.execute('INSERT INTO corporate_actions VALUES(?,?,?,?,?,?,?,?)', tuple(a[k] for k in
                       ('symbol', 'ex_date', 'kind', 'factor', 'volume_factor', 'verified', 'source', 'known_at')))
        h = charts.read_history(self.path, 'PRL', CUTOFF)
        self.assertEqual(h['actions'][0]['verified'], 0)
        self.assertIsNone(charts.prepare(row(), fixture(), h, NOW)['overlay'])

    def test_daily_eod_without_full_ohlc_is_not_substituted(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE daily_eod(symbol TEXT,date TEXT,open REAL,close REAL,volume REAL,source TEXT)')
        h = charts.read_history(self.path, 'PRL', CUTOFF)
        self.assertEqual(h['status'], 'unavailable')
        self.assertEqual(h['rows'], [])

    def test_invalid_symbol_cutoff_limit_rejected(self):
        for symbol, cutoff, limit in [('OTHER', CUTOFF, 42), ('PRL', '', 42), ('PRL', CUTOFF, True), ('PRL', CUTOFF, 0)]:
            with self.subTest(symbol=symbol, cutoff=cutoff, limit=limit), self.assertRaises(ValueError):
                charts.read_history(self.path, symbol, cutoff, limit)


if __name__ == '__main__':
    unittest.main()
