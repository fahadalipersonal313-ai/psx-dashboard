"""Fetch only public engine databases pinned to independently resolved branch SHAs."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import quote
import uuid

REPOSITORY = 'fahadalipersonal313-ai/psx-engine'
BRANCHES = ('main', 'runtime-state')
API = 'https://api.github.com/repos/' + REPOSITORY + '/git/ref/heads/{branch}'
RAW = 'https://raw.githubusercontent.com/' + REPOSITORY + '/{commit}/psx_engine.db'
MAX_BYTES = 100 * 1024 * 1024


def _utc(now):
    value = now()
    if value.tzinfo is None:
        raise ValueError('Download clock must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _validate(path):
    with path.open('rb') as handle:
        if handle.read(16) != b'SQLite format 3\x00':
            raise ValueError('Download is not a SQLite database')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('Downloaded database failed SQLite quick_check')
        columns = {row[1] for row in con.execute('PRAGMA table_info(daily_ohlc)')}
        if not {'symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'source'} <= columns:
            raise ValueError('Downloaded database has no usable daily_ohlc schema')


def fetch(output, *, get=None, branches=BRANCHES, timeout=(10, 60), max_seconds=180,
          max_bytes=MAX_BYTES, utc_now=None, clock=time.monotonic):
    """Four public GETs for two snapshots; failed branches never return an old path.

    timeouts bound connect/read inactivity, max_seconds bounds each full branch attempt,
    and max_bytes bounds streamed file size. Inject get/clocks for offline tests.
    """
    if (not branches or len(set(branches)) != len(branches) or any(b not in BRANCHES for b in branches)
            or len(timeout) != 2 or min(timeout) <= 0 or max_seconds <= 0 or max_bytes <= 0):
        raise ValueError('Invalid branch selection or download bounds')
    if get is None:
        import requests
        get = requests.get
    now = utc_now or (lambda: datetime.now(timezone.utc))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory = output / ('snapshots-' + uuid.uuid4().hex)
    directory.mkdir()
    manifest = {'repository': REPOSITORY, 'attempted_at_utc': _utc(now), 'snapshots': {},
                'extraction_errors': {}, 'manifest_path': str(output / 'manifest.json'),
                'limits': ['Download time is not market freshness; validate completed-session stock coverage.',
                           'Public branch SHA and file hash identify provenance, not independently reconciled prices.']}
    for branch in branches:
        temporary = None
        deadline = clock() + max_seconds

        def bounded_timeout():
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('Snapshot attempt exceeded its elapsed-time bound')
            return tuple(min(value, remaining) for value in timeout)

        try:
            api_url = API.format(branch=quote(branch, safe=''))
            with get(api_url, timeout=bounded_timeout(),
                     headers={'Accept': 'application/vnd.github+json', 'Cache-Control': 'no-cache'}) as response:
                response.raise_for_status()
                ref = response.json()
            commit = ref.get('object', {}).get('sha', '')
            if (ref.get('ref') != 'refs/heads/' + branch or ref.get('object', {}).get('type') != 'commit'
                    or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)):
                raise ValueError('GitHub did not return the requested branch with an immutable commit SHA')
            source_url = RAW.format(commit=commit)
            checksum, size = hashlib.sha256(), 0
            with get(source_url, timeout=bounded_timeout(), stream=True,
                     headers={'Cache-Control': 'no-cache'}) as response:
                response.raise_for_status()
                content_length = response.headers.get('Content-Length')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    content_length = None  # requests yields decoded bytes; compressed length is different.
                with tempfile.NamedTemporaryFile(dir=directory, suffix='.part', delete=False) as handle:
                    temporary = Path(handle.name)
                    for chunk in response.iter_content(1 << 20):
                        bounded_timeout()
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError('Snapshot exceeds the configured download-size bound')
                        checksum.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            if content_length is not None and size != int(content_length):
                raise ValueError('Snapshot download length is incomplete or inconsistent')
            bounded_timeout()
            _validate(temporary)
            bounded_timeout()
            final = directory / (branch + '-' + commit + '.db')
            os.replace(temporary, final)
            temporary = None
            manifest['snapshots'][branch] = {'path': str(final), 'commit': commit,
                'downloaded_at_utc': _utc(now), 'file_sha256': checksum.hexdigest(), 'bytes': size,
                'ref_api_url': api_url, 'source_url': source_url}
        except Exception as exc:
            manifest['extraction_errors'][branch] = type(exc).__name__ + ': ' + str(exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(dir=output, mode='w', encoding='utf-8', suffix='.json.part',
                                     delete=False) as handle:
        manifest_temp = Path(handle.name)
        json.dump(manifest, handle, indent=2, allow_nan=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(manifest_temp, Path(manifest['manifest_path']))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Runner temporary output directory')
    parser.add_argument('--branch', action='append', choices=BRANCHES, help='Default: both main and runtime-state')
    parser.add_argument('--read-timeout', type=float, default=60)
    parser.add_argument('--max-seconds', type=float, default=180, help='Elapsed-time bound per branch')
    args = parser.parse_args()
    manifest = fetch(args.output, branches=tuple(args.branch or BRANCHES),
                     timeout=(10, args.read_timeout), max_seconds=args.max_seconds)
    print(json.dumps(manifest, indent=2))
    return 0 if manifest['snapshots'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
