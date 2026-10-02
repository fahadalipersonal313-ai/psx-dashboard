"""Offline provenance, failure isolation and atomic-file regression tests."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('public_snapshots', Path(__file__).with_name('fetch_snapshots.py'))
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)
SHA_MAIN, SHA_RUNTIME = '1' * 40, '2' * 40
NOW = datetime(2026, 10, 3, 23, 20, tzinfo=timezone.utc)


class Response:
    def __init__(self, *, payload=None, chunks=(), error=None, headers=None):
        self.payload, self.chunks, self.error = payload, chunks, error
        self.headers, self.closed = headers or {}, False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload

    def iter_content(self, size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def ref(branch, sha):
    return {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': sha}}


class FetchSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='snapshot-fetch-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        path = self.root / 'fixture.db'
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE daily_ohlc(symbol TEXT,date TEXT,open REAL,high REAL,low REAL,'
                        'close REAL,volume REAL,source TEXT)')
            con.execute("INSERT INTO daily_ohlc VALUES ('PSO','2026-10-02',100,102,98,101,1000000,'PSX historical official')")
        self.body = path.read_bytes()
        self.output = self.root / 'download'
        self.calls, self.responses = [], []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.routes[url]
        self.responses.append(response)
        return response

    def routes_for(self, branches=fetcher.BRANCHES):
        self.routes = {}
        for branch in branches:
            sha = SHA_MAIN if branch == 'main' else SHA_RUNTIME
            self.routes[fetcher.API.format(branch=branch)] = Response(payload=ref(branch, sha))
            self.routes[fetcher.RAW.format(commit=sha)] = Response(
                chunks=[self.body[:100], b'', self.body[100:]],
                headers={'Content-Length': str(len(self.body))})

    def fetch(self, **kwargs):
        return fetcher.fetch(self.output, get=self.get, utc_now=lambda: NOW, **kwargs)

    def test_resolves_each_ref_and_downloads_only_sha_pinned_database(self):
        self.routes_for()
        manifest = self.fetch()
        self.assertEqual(set(manifest['snapshots']), {'main', 'runtime-state'})
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(manifest['extraction_errors'], {})
        for branch, snapshot in manifest['snapshots'].items():
            sha = SHA_MAIN if branch == 'main' else SHA_RUNTIME
            self.assertEqual(snapshot['commit'], sha)
            self.assertTrue(snapshot['source_url'].endswith('/' + sha + '/psx_engine.db'))
            self.assertEqual(Path(snapshot['path']).read_bytes(), self.body)
            self.assertEqual(snapshot['file_sha256'], hashlib.sha256(self.body).hexdigest())
            self.assertEqual(snapshot['downloaded_at_utc'], '2026-10-03T23:20:00Z')
            self.assertEqual(snapshot['bytes'], len(self.body))
        self.assertTrue(all(response.closed for response in self.responses))
        self.assertEqual(json.loads(Path(manifest['manifest_path']).read_text()), manifest)
        self.assertFalse(list(self.output.rglob('*.part')))
        for _, args in self.calls:
            self.assertEqual(args['timeout'], (10, 60))
            self.assertNotIn('Authorization', args['headers'])

    def test_branch_failure_isolated_and_old_manifest_not_reused(self):
        self.routes_for()
        self.output.mkdir()
        stale = self.output / 'main.db'
        stale.write_bytes(self.body)
        (self.output / 'manifest.json').write_text(json.dumps({'snapshots': {'main': {'path': str(stale)}}}))
        self.routes[fetcher.API.format(branch='main')] = Response(error=RuntimeError('503 unavailable'))
        manifest = self.fetch()
        self.assertEqual(set(manifest['snapshots']), {'runtime-state'})
        self.assertIn('main', manifest['extraction_errors'])
        self.assertNotEqual(manifest['snapshots']['runtime-state']['path'], str(stale))
        self.assertEqual(stale.read_bytes(), self.body)

    def test_partial_stream_removed_and_stale_file_never_returned(self):
        self.routes_for(('main',))
        self.output.mkdir()
        stale = self.output / ('main-' + SHA_MAIN + '.db')
        stale.write_bytes(self.body)
        self.routes[fetcher.RAW.format(commit=SHA_MAIN)] = Response(chunks=[self.body[:100], OSError('Connection lost')])
        manifest = self.fetch(branches=('main',))
        self.assertEqual(manifest['snapshots'], {})
        self.assertIn('Connection lost', manifest['extraction_errors']['main'])
        self.assertFalse(list(self.output.rglob('*.part')))
        self.assertEqual(list(self.output.rglob('*.db')), [stale])

    def test_invalid_or_mutable_commit_rejected_before_download(self):
        for sha in ('main', '../main', 'g' * 40):
            with self.subTest(sha=sha):
                self.routes_for(('main',))
                self.calls.clear()
                self.routes[fetcher.API.format(branch='main')] = Response(payload=ref('main', sha))
                manifest = self.fetch(branches=('main',))
                self.assertEqual(manifest['snapshots'], {})
                self.assertEqual(len(self.calls), 1)

    def test_wrong_branch_ref_rejected(self):
        self.routes_for(('main',))
        self.routes[fetcher.API.format(branch='main')] = Response(payload=ref('runtime-state', SHA_MAIN))
        manifest = self.fetch(branches=('main',))
        self.assertEqual(manifest['snapshots'], {})
        self.assertEqual(len(self.calls), 1)

    def test_html_or_corrupt_database_is_not_published(self):
        self.routes_for(('main',))
        self.routes[fetcher.RAW.format(commit=SHA_MAIN)] = Response(chunks=[b'<html>not a database</html>'])
        manifest = self.fetch(branches=('main',))
        self.assertEqual(manifest['snapshots'], {})
        self.assertIn('not a SQLite', manifest['extraction_errors']['main'])
        self.assertFalse(list(self.output.rglob('*.db')))

    def test_truncated_length_is_rejected(self):
        self.routes_for(('main',))
        self.routes[fetcher.RAW.format(commit=SHA_MAIN)].headers['Content-Length'] = str(len(self.body) + 1)
        manifest = self.fetch(branches=('main',))
        self.assertEqual(manifest['snapshots'], {})
        self.assertIn('length', manifest['extraction_errors']['main'])

    def test_sqlite_without_daily_candles_is_rejected(self):
        path = self.root / 'wrong-schema.db'
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE runs(run_time TEXT)')
        self.routes_for(('main',))
        self.routes[fetcher.RAW.format(commit=SHA_MAIN)] = Response(chunks=[path.read_bytes()])
        manifest = self.fetch(branches=('main',))
        self.assertEqual(manifest['snapshots'], {})
        self.assertIn('daily_ohlc schema', manifest['extraction_errors']['main'])
        self.assertFalse(list(self.output.rglob('*.db')))

    def test_decoded_compression_length_is_not_compared_to_raw_header(self):
        self.routes_for(('main',))
        self.routes[fetcher.RAW.format(commit=SHA_MAIN)].headers = {'Content-Length': '200', 'Content-Encoding': 'gzip'}
        manifest = self.fetch(branches=('main',))
        self.assertIn('main', manifest['snapshots'])

    def test_size_bound_removes_download(self):
        self.routes_for(('main',))
        manifest = self.fetch(branches=('main',), max_bytes=100)
        self.assertEqual(manifest['snapshots'], {})
        self.assertIn('size bound', manifest['extraction_errors']['main'])
        self.assertFalse(list(self.output.rglob('*.part')))

    def test_elapsed_bound_stops_before_database_request(self):
        self.routes_for(('main',))
        elapsed = [0]

        def slow_ref(url, **kwargs):
            response = self.get(url, **kwargs)
            elapsed[0] = 2
            return response

        manifest = fetcher.fetch(self.output, get=slow_ref, branches=('main',), utc_now=lambda: NOW,
                                 max_seconds=1, clock=lambda: elapsed[0])
        self.assertEqual(manifest['snapshots'], {})
        self.assertEqual(len(self.calls), 1)
        self.assertIn('elapsed-time', manifest['extraction_errors']['main'])

    def test_unknown_branch_cannot_become_a_path(self):
        with self.assertRaises(ValueError):
            self.fetch(branches=('../main',))
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
