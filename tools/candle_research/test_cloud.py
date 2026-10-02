"""Offline cloud orchestration tests; no engine requests or live strategy evaluation."""
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

spec = importlib.util.spec_from_file_location('cloud_research', Path(__file__).with_name('cloud.py'))
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)
REPO = Path(__file__).resolve().parents[2]


class CloudResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cloud-research-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / 'checkpoints'
        self.downloads = self.root / 'downloads'
        self.now = datetime.fromisoformat('2026-09-30T23:20:00+05:00')
        import config
        patch = mock.patch.object(config, 'STOCKS', ['PSO', 'EFERT'])
        patch.start()
        self.addCleanup(patch.stop)
        self.pattern = 'bullish_engulfing'
        patch = mock.patch.object(cloud.daily, 'research', side_effect=self.research)
        self.research_mock = patch.start()
        self.addCleanup(patch.stop)

    def research(self, repo, frame, output, commit):
        return {'observations': [{'symbol': 'PSO', 'pattern': self.pattern,
                                  'decision_date': frame.date.max(), 'partition': 'diagnostic_test',
                                  'status': 'unresolved', 'gross_pct': None}],
                'evidence': {'coverage': [{'symbol': 'PSO', 'bars': len(frame)}],
                             'pattern_diagnostics': [], 'limits': ['Synthetic test data.']}}

    def snapshot(self, name, days):
        path = self.root / (name + '.db')
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE daily_ohlc (symbol TEXT,date TEXT,open REAL,high REAL,low REAL,'
                        'close REAL,volume REAL,source TEXT,PRIMARY KEY(symbol,date))')
            con.executemany('INSERT INTO daily_ohlc VALUES (?,?,?,?,?,?,?,?)',
                            [(symbol, day, 100., 102., 98., 101., 1000000., 'PSX historical official')
                             for day in days for symbol in ('PSO', 'EFERT')])
        metadata = {'path': str(path), 'commit': 'a' * 40, 'downloaded_at_utc': self.now.isoformat(),
                    'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size,
                    'ref_api_url': 'https://api.github.com/repos/example/engine/git/ref/heads/main',
                    'source_url': 'https://raw.githubusercontent.com/example/engine/' + 'a' * 40 + '/psx_engine.db'}
        return {'repository': 'example/engine', 'snapshots': {'main': metadata}, 'extraction_errors': {}}

    def run_cloud(self, manifest, name='report'):
        return cloud.run(REPO, self.downloads, self.root / name, self.ledger,
                         clock=lambda: self.now, fetch=lambda _: manifest)

    def test_first_checkpoint_has_complete_provenance_and_is_retrospective(self):
        manifest = self.snapshot('first', ['2026-09-30'])
        source = Path(manifest['snapshots']['main']['path'])
        unchanged = source.read_bytes()
        result = self.run_cloud(manifest)
        self.assertTrue(result['checkpoint_created'])
        self.assertFalse(result['proposals'][0]['prospective_eligible'])
        self.assertIsNone(result['profit_probability'])
        self.assertEqual([p.name for p in self.ledger.glob('*.json')], ['2026-09-30.json'])
        stored = json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
        self.assertEqual(stored['source_manifest'], manifest)
        self.assertEqual(stored['checkpoint_key'], '2026-09-30.json')
        self.assertEqual(source.read_bytes(), unchanged)

    def test_rerun_and_source_correction_recompute_only_artifacts(self):
        manifest = self.snapshot('first', ['2026-09-30'])
        first = self.run_cloud(manifest)
        path = Path(first['checkpoint'])
        immutable = path.read_bytes()
        rerun = self.run_cloud(manifest, 'rerun')
        self.assertTrue(rerun['duplicate'])
        self.assertFalse(rerun['checkpoint_created'])
        self.assertTrue(rerun['diagnostic_only'])
        self.assertEqual(self.research_mock.call_count, 2)
        with closing(sqlite3.connect(manifest['snapshots']['main']['path'])) as con, con:
            con.execute("UPDATE daily_ohlc SET volume=2000000 WHERE symbol='PSO'")
        self.pattern = 'trend_pullback'
        corrected = self.run_cloud(manifest, 'corrected')
        self.assertTrue(corrected['data_correction'])
        self.assertFalse(corrected['checkpoint_created'])
        self.assertFalse(corrected['proposals'][0]['prospective_eligible'])
        self.assertTrue(corrected['proposals'][0]['diagnostic_only'])
        self.assertEqual(path.read_bytes(), immutable)
        self.assertEqual(len(list(self.ledger.glob('*.json'))), 1)

    def test_stale_snapshot_leaves_no_checkpoint_and_does_not_seed_baseline(self):
        stale = self.snapshot('stale', ['2026-09-29'])
        result = self.run_cloud(stale)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['checkpoint'])
        self.assertFalse(self.ledger.exists())
        fresh = self.snapshot('fresh', ['2026-09-29', '2026-09-30'])
        result = self.run_cloud(fresh, 'fresh')
        self.assertTrue(result['checkpoint_created'])
        self.assertFalse(result['proposals'][0]['prospective_eligible'])

    def test_missing_sources_preserve_error_artifact_without_ledger_writes(self):
        manifest = {'repository': 'example/engine', 'snapshots': {},
                    'extraction_errors': {'main': 'Network unavailable', 'runtime-state': 'Snapshot missing'}}
        result = self.run_cloud(manifest)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['source_manifest'], manifest)
        self.assertEqual(len(result['snapshots']), 2)
        self.research_mock.assert_not_called()
        self.assertFalse(self.ledger.exists())
        self.assertTrue((self.root / 'report' / 'status.json').exists())

    def test_after_midnight_later_checkpoint_is_prospective_and_stale_retry_preserves_ledger(self):
        baseline = self.snapshot('baseline', ['2026-09-30'])
        self.run_cloud(baseline)
        self.now = datetime.fromisoformat('2026-10-02T01:00:00+05:00')
        fresh = self.snapshot('fresh', ['2026-09-30', '2026-10-01'])
        result = self.run_cloud(fresh, 'fresh')
        self.assertEqual(result['expected_session'], '2026-10-01')
        self.assertTrue(result['proposals'][0]['prospective_eligible'])
        before = {path.name: path.read_bytes() for path in self.ledger.glob('*.json')}
        stale = self.run_cloud(baseline, 'stale-retry')
        self.assertEqual(stale['status'], 'unavailable')
        self.assertFalse(stale['checkpoint_created'])
        self.assertEqual({path.name: path.read_bytes() for path in self.ledger.glob('*.json')}, before)

    def test_duplicate_session_ledger_fails_before_fetch_or_replacement(self):
        result = self.run_cloud(self.snapshot('first', ['2026-09-30']))
        original = Path(result['checkpoint'])
        (self.ledger / 'duplicate.json').write_bytes(original.read_bytes())
        fetch = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'more than one checkpoint'):
            cloud.run(REPO, self.downloads, self.root / 'invalid', self.ledger, fetch=fetch)
        fetch.assert_not_called()

    def test_crossing_new_session_publication_keeps_diagnostics_without_checkpoint(self):
        manifest = self.snapshot('previous-session', ['2026-10-01'])
        clock = mock.Mock(side_effect=[datetime.fromisoformat(stamp) for stamp in
                                      ('2026-10-02T16:59:00+05:00',
                                       '2026-10-02T17:00:01+05:00',
                                       '2026-10-02T17:00:02+05:00')])
        result = cloud.run(REPO, self.downloads, self.root / 'crossed-publication', self.ledger,
                           clock=clock, fetch=lambda _: manifest)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['requested_expected_session'], '2026-10-01')
        self.assertEqual(result['expected_session'], '2026-10-02')
        self.assertEqual(result['cutoff'], '2026-10-01')
        self.assertFalse(result['checkpoint_created'])
        self.assertIsNone(result['checkpoint'])
        self.assertEqual(result['proposals'], [])
        self.assertFalse(self.ledger.exists())
        artifact = json.loads((self.root / 'crossed-publication' / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(artifact['source_manifest'], manifest)
        self.assertEqual(artifact['status'], 'unavailable')
        self.research_mock.assert_called_once()


if __name__ == '__main__':
    unittest.main()
