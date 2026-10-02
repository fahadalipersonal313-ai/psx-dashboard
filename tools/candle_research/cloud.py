"""Cloud diagnostics with one immutable evidence checkpoint per completed session."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily


def fetch_sources(downloads):
    import fetch_snapshots
    return fetch_snapshots.fetch(downloads)


def ledger_records(directory):
    records = []
    if directory.exists() and not directory.is_dir():
        raise ValueError('Checkpoint ledger must be a directory')
    for path in sorted(directory.glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        cutoff = record.get('cutoff')
        if not cutoff or date.fromisoformat(cutoff).isoformat() != cutoff:
            raise ValueError(f'Invalid checkpoint cutoff: {path.name}')
        records.append((path, record))
    cutoffs = [record['cutoff'] for _, record in records]
    if len(cutoffs) != len(set(cutoffs)):
        raise ValueError('Durable ledger contains more than one checkpoint for a session')
    return records


def write_status(output, status, notice):
    (output / 'status.json').write_text(json.dumps(status, indent=2, allow_nan=False) + '\n',
                                      encoding='utf-8')
    summary = output / 'summary.md'
    summary.write_text(summary.read_text(encoding='utf-8') + '\n' + notice + '\n', encoding='utf-8')
    return status


def run(repo, downloads, output, checkpoints, *, clock=None, fetch=None):
    """Fetch pinned sources, run diagnostics, and create at most one fresh ledger entry."""
    repo, downloads, output, checkpoints = (Path(p).resolve() for p in
                                           (repo, downloads, output, checkpoints))
    now = clock or (lambda: datetime.now(timezone.utc))
    records = ledger_records(checkpoints)
    manifest = (fetch or fetch_sources)(downloads)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'source_manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n',
                                               encoding='utf-8')
    errors = output / 'extraction_errors.json'
    errors.write_text(json.dumps(manifest.get('extraction_errors', {}), indent=2) + '\n', encoding='utf-8')
    databases = [(name, Path(snapshot['path']), snapshot['commit'])
                 for name, snapshot in manifest.get('snapshots', {}).items()]
    sys.path.insert(0, str(repo))
    import session_calendar
    started = now()
    if started.tzinfo is None:
        raise ValueError('Cloud clock must have timezone')
    expected = session_calendar.last_completed(started)
    existing = next(((path, record) for path, record in records if record['cutoff'] == expected), None)
    # Always compute current artifacts. Renamed copies preserve previous identities but bypass
    # daily.py's identical-dataset shortcut; none of these temporary records are published.
    with tempfile.TemporaryDirectory(prefix='psx-candle-cloud-') as temporary:
        working = Path(temporary) / 'checkpoints'
        working.mkdir()
        for path, _ in records:
            (working / ('prior-' + path.name)).write_bytes(path.read_bytes())
        result = daily.run(repo, databases, expected, output, working, started.isoformat(),
                           extraction_errors=errors, clock=now)
    status = {**result, 'source_manifest': manifest, 'checkpoint': None,
              'diagnostic_checkpoint_key': result.get('checkpoint_key'),
              'checkpoint_created': False, 'diagnostic_only': True,
              'session_already_checkpointed': existing is not None}
    publication_time = now()
    if publication_time.tzinfo is None:
        raise ValueError('Publication clock must have timezone')
    publication_expected = session_calendar.last_completed(publication_time)
    status['publication_checked_at'] = publication_time.isoformat()
    if publication_expected != expected:
        status.update(status='unavailable', requested_expected_session=expected,
                      expected_session=publication_expected, proposals=[],
                      freshness_reason='A newer completed session became available during research')
    if status['status'] != 'available':
        return write_status(output, status, 'No fresh completed-session checkpoint was added to the durable ledger.')
    if existing:
        path, frozen = existing
        status.update(checkpoint=str(path), durable_checkpoint_key=path.name, duplicate=True,
                      data_correction=frozen['dataset_hash'] != result['dataset_hash'],
                      method_changed=frozen.get('contract_hash') != result.get('contract_hash'))
        # A corrected/new setup in an artifact-only rerun is not a new durable prediction.
        for proposal in status.get('proposals', []):
            proposal['prospective_eligible'] = False
            proposal['diagnostic_only'] = True
        return write_status(output, status,
                            'The session checkpoint is unchanged. Current corrections and diagnostics are artifact-only.')
    checkpoints.mkdir(parents=True, exist_ok=True)
    destination = checkpoints / (expected + '.json')
    status.update(checkpoint=str(destination), checkpoint_key=destination.name,
                  durable_checkpoint_key=destination.name, checkpoint_created=True,
                  diagnostic_only=False, duplicate=False)
    # Source provenance is present in the first write; an existing checkpoint is never amended.
    with destination.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(status, indent=2, allow_nan=False) + '\n')
    return write_status(output, status, 'One fresh immutable session checkpoint was added to the durable ledger.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checkpoints', type=Path, required=True)
    args = parser.parse_args()
    status = run(args.repo, args.downloads, args.output, args.checkpoints)
    print(json.dumps({key: status.get(key) for key in
                      ('status', 'cutoff', 'expected_session', 'checkpoint_created', 'diagnostic_only')}))
    return 0 if status['status'] == 'available' else 2


if __name__ == '__main__':
    raise SystemExit(main())
