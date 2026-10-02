"""Focused tests for reading research without presenting stale or corrupt data as current."""
import base64
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import research_panel as panel


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def raise_for_status(self):
        pass
    def iter_content(self, size):
        yield self.data


class ResearchPanelTests(unittest.TestCase):
    def setUp(self):
        seed = json.loads(panel.BASELINE.read_text(encoding='utf-8'))
        self.record = {**seed, 'cutoff': '2026-10-02', 'status': 'available'}
        self.raw = json.dumps(self.record).encode()
        self.sha = hashlib.sha1(b'blob ' + str(len(self.raw)).encode() + b'\0' + self.raw).hexdigest()
        self.entry = {'name': '2026-10-02.json', 'type': 'file', 'size': len(self.raw), 'sha': self.sha}
        self.blob = {'sha': self.sha, 'encoding': 'base64', 'size': len(self.raw),
                     'content': base64.b64encode(self.raw).decode()}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return Response([{'name': '2026-02-30.json', 'type': 'file'}, self.entry,
                         {**self.entry, 'name': '2026-10-01.json'}] if '/contents/' in url else self.blob)

    def test_latest_file_is_unsorted_and_blob_is_pinned(self):
        record, meta = panel.load_latest(get=self.get)
        self.assertEqual(meta['source'], 'cloud')
        self.assertEqual(record['cutoff'], '2026-10-02')
        self.assertEqual(self.calls[-1], panel.API + '/git/blobs/' + self.sha)
        self.assertEqual(len(self.calls), 2)

    def test_missing_cloud_is_always_labelled_baseline(self):
        def failure(*args, **kwargs):
            raise TimeoutError('offline')
        record, meta = panel.load_latest(get=failure)
        self.assertEqual(panel.current_status(record, meta, '2026-10-02'), 'offline baseline')

    def test_missing_cloud_and_baseline_is_unavailable(self):
        record, meta = panel.load_latest(get=lambda *a, **k: Response([]), baseline=Path('missing-evidence.json'))
        self.assertIsNone(record)
        self.assertEqual(meta['source'], 'unavailable')

    def test_cached_checkpoint_becomes_stale_without_refetch(self):
        meta = {'source': 'cloud'}
        self.assertEqual(panel.current_status(self.record, meta, '2026-10-02'), 'current')
        self.assertEqual(panel.current_status(self.record, meta, '2026-10-05'), 'waiting for newer data')
        self.assertEqual(panel.current_status(self.record, meta, '2026-10-01'), 'future-dated')

    def test_blob_sha_and_content_corruption_fall_back(self):
        for field, value in [('sha', '0' * 40), ('content', '!not-base64'), ('size', 1)]:
            with self.subTest(field=field):
                before = copy.deepcopy(self.blob)
                self.blob[field] = value
                _, meta = panel.load_latest(get=self.get)
                self.assertEqual(meta['source'], 'bundled baseline')
                self.blob = before
        self.blob['content'] = base64.b64encode(b'{}').decode()
        self.blob['size'] = 2
        _, meta = panel.load_latest(get=self.get)
        self.assertEqual(meta['source'], 'bundled baseline')

    def test_cutoff_and_nested_shape_mismatch_are_rejected(self):
        for change in ({'cutoff': '2026-10-01'}, {'coverage': [{'symbol': 'PSO'}]},
                       {'coverage': self.record['coverage'] * 2}):
            with self.subTest(change=list(change)):
                _, meta = panel.load_latest(get=self.get, blob_loader=lambda sha: {**self.record, **change})
                self.assertEqual(meta['source'], 'bundled baseline')

    def test_result_table_uses_one_period_and_declared_cost(self):
        rows = panel.result_rows(self.record)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['Results with prices'], 249)
        self.assertEqual(rows[0]['Positive results'], '32.5%')
        self.assertEqual(rows[0]['Average return'], '-1.68%')
        self.assertNotIn('confidence', str(rows).lower())

    def test_chart_does_not_create_db_or_include_future_invalid_bars(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'prices.db'
            self.assertEqual(panel.candles(path, 'PSO', '2026-10-02'), [])
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as con:
                con.execute('CREATE TABLE daily_ohlc(symbol,date,open,high,low,close,volume)')
                con.executemany('INSERT INTO daily_ohlc VALUES(?,?,?,?,?,?,?)',
                                [('PSO', '2026-10-01', 100, 105, 98, 103, 10000),
                                 ('PSO', '2026-10-02', 100, 99, 98, 103, 10000),
                                 ('PSO', '2026-10-05', 100, 110, 98, 108, 10000)])
                con.commit()
            self.assertEqual([r['date'] for r in panel.candles(path, 'PSO', '2026-10-02')], ['2026-10-01'])


if __name__ == '__main__':
    unittest.main()
