"""Coverage, revision and immutability regressions for daily research checkpoints."""
from contextlib import closing
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('daily_research', Path(__file__).with_name('daily.py'))
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)
REPO = Path(__file__).resolve().parents[2]


def observation(day, symbol='PSO', status='unresolved', pattern='bullish_engulfing'):
    return {'symbol': symbol, 'pattern': pattern, 'decision_date': day,
            'partition': 'diagnostic_test', 'status': status,
            'gross_pct': 2.0 if status == 'priced_scenario' else None}


class DailyResearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='daily-research-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkpoints = self.root / 'checkpoints'
        import config
        stocks = mock.patch.object(config, 'STOCKS', ['PSO', 'EFERT'])
        stocks.start()
        self.addCleanup(stocks.stop)
        self.captured = []
        self.rows = None
        self.runner = mock.patch.object(daily, 'research', side_effect=self.research)
        self.research_mock = self.runner.start()
        self.addCleanup(self.runner.stop)

    def research(self, repo, frame, output, commit):
        self.captured.append(frame.copy())
        return {'observations': self.rows if self.rows is not None else [observation(frame.date.max())],
                'evidence': {'coverage': [{'symbol': 'PSO', 'bars': len(frame)}],
                             'pattern_diagnostics': [{'pattern': 'bullish_engulfing', 'setups': 1}],
                             'limits': ['Mocked price scenario; no profitability probability.']}}

    def database(self, name, days, extra=()):
        path = self.root / (name + '.db')
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE daily_ohlc (symbol TEXT,date TEXT,open REAL,high REAL,low REAL,'
                        'close REAL,volume REAL,source TEXT,PRIMARY KEY(symbol,date))')
            rows = [(symbol, day, 100., 102., 98., 101., 1000000., 'PSX historical official')
                    for day in days for symbol in ('PSO', 'EFERT')]
            con.executemany('INSERT INTO daily_ohlc VALUES (?,?,?,?,?,?,?,?)', [*rows, *extra])
        return path

    def run_daily(self, databases, expected, scheduled=None, suffix='report', frozen=None):
        stamp = scheduled or expected + 'T23:20:00+05:00'
        return daily.run(REPO, databases, expected, self.root / suffix, self.checkpoints,
                         stamp, clock=lambda: datetime.fromisoformat(frozen or stamp))

    def test_broad_coverage_beats_single_symbol_and_future_outlier(self):
        main = self.database('main', ['2026-09-30', '2026-10-01'],
                             [('PSO', '2099-01-01', 100., 102., 98., 101., 1000000., 'PSX historical official')])
        runtime = self.database('runtime', ['2026-09-30'],
                                [('PSO', '2026-10-01', 100., 102., 98., 101., 1000000., 'PSX historical official')])
        before = hashlib.sha256(main.read_bytes()).hexdigest()
        result = self.run_daily([('runtime-state', runtime, 'runtime-sha'), ('main', main, 'main-sha')], '2026-10-01')
        self.assertEqual((result['selected_snapshot'], result['cutoff'], result['status']),
                         ('main', '2026-10-01', 'available'))
        self.assertEqual(result['snapshots'][1]['future_rows_excluded'], 1)
        self.assertEqual(set(self.captured[0].date), {'2026-09-30', '2026-10-01'})
        self.assertEqual(hashlib.sha256(main.read_bytes()).hexdigest(), before)
        self.assertEqual(result['data_commit'], 'main-sha')

    def test_stale_snapshot_records_unavailable_without_freezing_proposals(self):
        main = self.database('main', ['2026-09-30'])
        result = self.run_daily([('main', main, 'sha')], '2026-10-01')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['cutoff'], '2026-09-30')
        self.assertEqual(result['proposals'], [])
        self.assertEqual(result['snapshots'][0]['expected_valid_stocks'], 0)
        self.assertTrue(Path(result['checkpoint']).exists())

    def test_rerun_preserves_checkpoint_and_does_not_repeat_research(self):
        main = self.database('main', ['2026-10-01'])
        inputs = [('main', main, 'sha')]
        first = self.run_daily(inputs, '2026-10-01')
        checkpoint = Path(first['checkpoint'])
        frozen = checkpoint.read_bytes()
        second = self.run_daily(inputs, '2026-10-01', scheduled='2026-10-02T01:00:00+05:00', suffix='repeat')
        self.assertTrue(second['duplicate'])
        self.assertEqual(checkpoint.read_bytes(), frozen)
        self.assertEqual(self.research_mock.call_count, 1)
        self.assertEqual(len(list(self.checkpoints.glob('*.json'))), 1)

    def test_prior_proposal_resolves_by_stable_identity(self):
        first = self.database('first', ['2026-09-30'])
        before = self.run_daily([('main', first, 'a')], '2026-09-30')
        key = before['proposals'][0]['id']
        second = self.database('second', ['2026-09-30', '2026-10-01'])
        self.rows = [observation('2026-09-30', status='priced_scenario'),
                     observation('2026-10-01', symbol='EFERT', pattern='trend_pullback')]
        after = self.run_daily([('main', second, 'b')], '2026-10-01', suffix='later')
        self.assertEqual(after['outcomes'][0]['id'], key)
        self.assertEqual(after['outcomes'][0]['status'], 'resolved')
        self.assertFalse(after['outcomes'][0]['prospective_eligible'])  # first checkpoint is baseline
        self.assertTrue(after['proposals'][0]['prospective_eligible'])
        self.assertIsNone(after['profit_probability'])

    def test_later_prospective_outcome_preserves_eligibility(self):
        baseline = self.database('baseline', ['2026-09-30'])
        self.run_daily([('main', baseline, 'a')], '2026-09-30')
        second = self.database('second', ['2026-09-30', '2026-10-01'])
        self.rows = [observation('2026-09-30'), observation('2026-10-01')]
        frozen = self.run_daily([('main', second, 'b')], '2026-10-01', suffix='second')
        self.assertTrue(frozen['proposals'][0]['prospective_eligible'])
        third = self.database('third', ['2026-09-30', '2026-10-01', '2026-10-02'])
        self.rows = [observation('2026-09-30'), observation('2026-10-01', status='priced_scenario')]
        result = self.run_daily([('main', third, 'c')], '2026-10-02', suffix='third')
        outcome = next(row for row in result['outcomes'] if row['id'] == frozen['proposals'][0]['id'])
        self.assertEqual(outcome['status'], 'resolved')
        self.assertTrue(outcome['prospective_eligible'])

    def test_missing_revised_observation_remains_unresolved_for_review(self):
        first = self.database('first', ['2026-09-30'])
        before = self.run_daily([('main', first, 'a')], '2026-09-30')
        second = self.database('second', ['2026-09-30', '2026-10-01'])
        self.rows = []
        after = self.run_daily([('main', second, 'b')], '2026-10-01', suffix='later')
        outcome = after['outcomes'][0]
        self.assertEqual(outcome['id'], before['proposals'][0]['id'])
        self.assertEqual(outcome['status'], 'unresolved')
        self.assertTrue(outcome['review_required'])
        self.assertIsNone(outcome['observed'])

    def test_unfilled_and_purged_outcomes_are_not_resolved_wins(self):
        previous = {'proposals': [{**observation('2026-09-30'), 'id': 'unfilled', 'prospective_eligible': True},
                                  {**observation('2026-09-30', symbol='EFERT'), 'id': 'purged', 'prospective_eligible': True}]}
        for proposal in previous['proposals']:
            proposal['id'] = daily.identity(proposal)
        rows = [observation('2026-09-30', status='unfilled'),
                observation('2026-09-30', symbol='EFERT', status='purged')]
        outcomes = daily.predecessor_outcomes(previous, rows)
        self.assertEqual({o['status'] for o in outcomes}, {'unfilled', 'purged'})

    def test_changed_specification_flags_incompatible_checkpoint(self):
        main = self.database('main', ['2026-09-30'])
        self.run_daily([('main', main, 'a')], '2026-09-30')
        with mock.patch.dict(daily.study.SPEC, {'horizon_sessions': 6}):
            changed = self.run_daily([('main', main, 'a')], '2026-09-30', suffix='changed')
        self.assertEqual(changed['incompatible_checkpoints'], 1)
        self.assertIsNone(changed['predecessor'])
        self.assertEqual(changed['outcomes'], [])
        self.assertEqual(len(changed['review_queue']), 1)
        self.assertEqual(changed['review_queue'][0]['reason'], 'old_contract')

    def test_helper_hash_change_carries_pending_under_same_contract(self):
        first = self.database('first', ['2026-09-30'])
        with mock.patch.object(daily, 'runner_hash', return_value='helper-version-one'):
            before = self.run_daily([('main', first, 'a')], '2026-09-30')
        second = self.database('second', ['2026-09-30', '2026-10-01'])
        self.rows = [observation('2026-09-30', status='priced_scenario')]
        with mock.patch.object(daily, 'runner_hash', return_value='helper-version-two'):
            after = self.run_daily([('main', second, 'b')], '2026-10-01', suffix='later')
        self.assertEqual(after['contract_hash'], before['contract_hash'])
        self.assertEqual(after['outcomes'][0]['id'], before['proposals'][0]['id'])
        self.assertEqual(after['outcomes'][0]['status'], 'resolved')
        self.assertEqual(after['incompatible_checkpoints'], 0)

    def test_extraction_failures_are_retained_with_usable_snapshot(self):
        main = self.database('main', ['2026-10-01'])
        errors = self.root / 'extraction-errors.json'
        errors.write_text(json.dumps([{'name': 'runtime-state', 'error': 'Blob missing',
                                       'data_commit': 'runtime-sha'}]), encoding='utf-8')
        result = daily.run(REPO, [('main', main, 'main-sha')], '2026-10-01',
                           self.root / 'report', self.checkpoints, '2026-10-01T23:20:00+05:00', errors)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['snapshots'][0]['error'], 'Blob missing')
        self.assertEqual(result['snapshots'][0]['data_commit'], 'runtime-sha')

    def test_workflow_extraction_error_mapping_is_supported(self):
        main = self.database('main', ['2026-10-01'])
        errors = self.root / 'extraction-errors.json'
        errors.write_text(json.dumps({'runtime-state': 'Snapshot fetch/extraction failed (exit 1)'}), encoding='utf-8')
        result = daily.run(REPO, [('main', main, 'main-sha')], '2026-10-01',
                           self.root / 'report', self.checkpoints, '2026-10-01T23:20:00+05:00', errors)
        self.assertEqual(result['snapshots'][0]['name'], 'runtime-state')
        self.assertIn('exit 1', result['snapshots'][0]['error'])

    def test_older_first_checkpoint_is_retrospective(self):
        main = self.database('main', ['2026-10-02'])
        result = self.run_daily([('main', main, 'a')], '2026-10-02', scheduled='2026-10-03T12:00:00+05:00')
        self.assertFalse(result['proposals'][0]['prospective_eligible'])

    def test_same_day_first_checkpoint_is_retrospective(self):
        main = self.database('main', ['2026-10-01'])
        result = self.run_daily([('main', main, 'a')], '2026-10-01')
        self.assertFalse(result['proposals'][0]['prospective_eligible'])
        self.assertIn('baseline', result['prospective_window']['reason'])

    def test_stale_checkpoint_does_not_establish_prospective_baseline(self):
        stale = self.database('stale', ['2026-09-30'])
        self.run_daily([('main', stale, 'a')], '2026-10-01')
        fresh = self.database('fresh', ['2026-09-30', '2026-10-01'])
        result = self.run_daily([('main', fresh, 'b')], '2026-10-01', suffix='fresh')
        self.assertFalse(result['proposals'][0]['prospective_eligible'])

    def test_after_midnight_before_next_open_is_prospective(self):
        first = self.database('first', ['2026-09-30'])
        self.run_daily([('main', first, 'a')], '2026-09-30')
        main = self.database('main', ['2026-09-30', '2026-10-01'])
        result = self.run_daily([('main', main, 'b')], '2026-10-01',
                                scheduled='2026-10-02T01:00:00+05:00', suffix='fresh')
        self.assertTrue(result['proposals'][0]['prospective_eligible'])
        self.assertEqual(result['prospective_window']['next_session_open'], '2026-10-02T09:17:00+05:00')

    def test_research_crossing_next_open_cannot_freeze_prospectively(self):
        first = self.database('first', ['2026-09-30'])
        self.run_daily([('main', first, 'a')], '2026-09-30')
        main = self.database('main', ['2026-09-30', '2026-10-01'])
        result = self.run_daily([('main', main, 'b')], '2026-10-01',
                                scheduled='2026-10-02T09:16:00+05:00',
                                frozen='2026-10-02T09:18:00+05:00', suffix='fresh')
        self.assertFalse(result['proposals'][0]['prospective_eligible'])
        self.assertEqual(result['proposals'][0]['frozen_at'], '2026-10-02T09:18:00+05:00')

    def test_weekend_holiday_and_open_boundary_use_configured_calendar(self):
        import config
        import session_calendar
        with mock.patch.object(config, 'EXCHANGE_HOLIDAYS', ['2026-10-05']):
            for stamp, eligible in [('2026-10-04T12:00:00+05:00', True),
                                    ('2026-10-06T09:31:59+05:00', True),
                                    ('2026-10-06T09:32:00+05:00', False)]:
                with self.subTest(stamp=stamp):
                    result = daily.prospective_window('2026-10-02', datetime.fromisoformat(stamp),
                                                      session_calendar, True, ['2026-10-02'])
                    self.assertEqual(result['eligible'], eligible)
                    self.assertEqual(result['next_session_open'], '2026-10-06T09:32:00+05:00')
        with mock.patch.object(config, 'SESSION_OVERRIDES', {'2026-10-03': [('11:00', '13:00')]}):
            result = daily.prospective_window('2026-10-02', datetime.fromisoformat('2026-10-03T11:00:00+05:00'),
                                              session_calendar, True, ['2026-10-02'])
            self.assertFalse(result['eligible'])
            self.assertEqual(result['next_session_open'], '2026-10-03T11:00:00+05:00')

    def test_next_session_rows_or_unknown_next_open_block_prospectivity(self):
        import session_calendar
        stamp = datetime.fromisoformat('2026-10-02T01:00:00+05:00')
        result = daily.prospective_window('2026-10-01', stamp, session_calendar, True,
                                          ['2026-10-01', '2026-10-02'])
        self.assertFalse(result['eligible'])
        calendar = mock.Mock()
        calendar.last_completed.return_value = '2026-10-01'
        calendar.intervals.return_value = []
        result = daily.prospective_window('2026-10-01', stamp, calendar, True, ['2026-10-01'])
        self.assertFalse(result['eligible'])
        self.assertIsNone(result['next_session_open'])

    def test_same_day_incomplete_session_is_rejected(self):
        main = self.database('main', ['2026-10-02'])
        with self.assertRaisesRegex(ValueError, 'not completed'):
            self.run_daily([('main', main, 'a')], '2026-10-02', scheduled='2026-10-02T10:00:00+05:00')
        self.research_mock.assert_not_called()
        self.assertFalse(self.checkpoints.exists())

    def test_corrected_same_cutoff_preserves_original_proposal(self):
        main = self.database('main', ['2026-10-01'])
        first = self.run_daily([('main', main, 'a')], '2026-10-01')
        original = first['proposals'][0]
        with closing(sqlite3.connect(main)) as con, con:
            con.execute("UPDATE daily_ohlc SET volume=2000000 WHERE symbol='PSO'")
        corrected = self.run_daily([('main', main, 'b')], '2026-10-01', suffix='corrected')
        self.assertTrue(corrected['data_correction'])
        self.assertEqual(corrected['proposals'], [])
        self.assertEqual(corrected['reused_setup_ids'], [original['id']])
        self.assertEqual(corrected['outcomes'][0]['proposal'], original)
        self.assertNotEqual(corrected['dataset_hash'], original['frozen_dataset_hash'])
        stored = json.loads(Path(corrected['checkpoint']).read_text(encoding='utf-8'))
        self.assertEqual(stored['pattern_diagnostics'][0]['setups'], 1)
        self.assertTrue(stored['coverage'])
        self.assertTrue(stored['study_limits'])

    def test_bad_current_candle_blocks_freshness(self):
        main = self.database('main', ['2026-09-30', '2026-10-01'])
        with closing(sqlite3.connect(main)) as con, con:
            con.execute("UPDATE daily_ohlc SET high=99 WHERE symbol='PSO' AND date='2026-10-01'")
        result = self.run_daily([('main', main, 'a')], '2026-10-01')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['cutoff'], '2026-09-30')
        self.assertEqual(result['proposals'], [])

    def test_missing_database_is_not_created(self):
        path = self.root / 'missing.db'
        result = self.run_daily([('main', path, 'a')], '2026-10-01')
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(path.exists())
        self.assertIsNone(result['checkpoint'])


if __name__ == '__main__':
    unittest.main()
