import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config
import remote_data


def make_db(path, run_time, broken=False):
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("CREATE TABLE runs (run_time TEXT)")
        if run_time:
            c.execute("INSERT INTO runs VALUES (?)", (run_time,))
    if broken:
        with open(path, "r+b") as fh:
            fh.seek(0)
            fh.write(b"garbage-not-sqlite")


class Resp:
    def __init__(self, body=b"", ok=True):
        self.body, self.ok = body, ok

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("404")

    def iter_content(self, n):
        yield self.body


class RefreshDbTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='remote-data-test-')
        self.addCleanup(self.temporary.cleanup)
        self.tmp = self.temporary.name
        self.bundled = os.path.join(self.tmp, "bundled.db")
        make_db(self.bundled, "2026-09-28T10:00:00+05:00")
        patcher = mock.patch.object(config, "DB_PATH", self.bundled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def body(self, run_time, broken=False):
        p = os.path.join(self.tmp, "src.db")
        if os.path.exists(p):
            os.remove(p)
        make_db(p, run_time, broken)
        with open(p, "rb") as handle:
            return handle.read()

    def test_newer_valid_download_is_used(self):
        fresh = self.body("2026-10-02T11:00:00+05:00")
        path, branch = remote_data.refresh_db(("runtime-state",), get=lambda *a, **k: Resp(fresh),
                                              dest_dir=self.tmp)
        self.assertEqual(branch, "runtime-state")
        self.assertEqual(config.DB_PATH, path)

    def test_older_download_keeps_bundled(self):
        old = self.body("2026-09-01T10:00:00+05:00")
        path, why = remote_data.refresh_db(("main",), get=lambda *a, **k: Resp(old), dest_dir=self.tmp)
        self.assertIsNone(path)
        self.assertEqual(config.DB_PATH, self.bundled)

    def test_corrupt_download_is_refused(self):
        bad = self.body("2026-10-02T11:00:00+05:00", broken=True)
        path, _ = remote_data.refresh_db(("main",), get=lambda *a, **k: Resp(bad), dest_dir=self.tmp)
        self.assertIsNone(path)
        self.assertEqual(config.DB_PATH, self.bundled)

    def test_network_failure_keeps_bundled(self):
        path, _ = remote_data.refresh_db(("main",), get=lambda *a, **k: Resp(ok=False), dest_dir=self.tmp)
        self.assertIsNone(path)
        self.assertEqual(config.DB_PATH, self.bundled)


class JsonTests(unittest.TestCase):
    def test_newer_picks_later_as_of(self):
        a, b = {"as_of": "2026-10-02T08:00Z"}, {"as_of": "2026-09-27T08:00Z"}
        self.assertIs(remote_data.newer(a, b), a)
        self.assertIs(remote_data.newer(b, a), a)
        self.assertIs(remote_data.newer(None, b), b)

    def test_fetch_json_failure_returns_none(self):
        remote_data._cache.clear()
        self.assertIsNone(remote_data.fetch_json("x.json", get=lambda *a, **k: Resp(ok=False)))


if __name__ == "__main__":
    unittest.main()
