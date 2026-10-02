"""Read-only daily candle diagnostics and immutable, sampled-setup checkpoints."""
import argparse
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd

PKT = ZoneInfo('Asia/Karachi')
STUDY = Path(__file__).with_name('study.py')
POLICY = {'setup_identity': 'symbol-pattern-decision-date-v1',
          'data': 'completed-broad-official-coverage-v1',
          'fill': 'next-observed-open-fifth-close-raw-diagnostic-v2',
          'prospective': 'baseline-then-completed-cutoff-before-next-open-v1'}
spec = importlib.util.spec_from_file_location('daily_candle_study', STUDY)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
LIMITS = ['Research scenarios, not live calls, calibrated probabilities or guaranteed fills.',
          'Expected session supplied by caller; exchange holidays are not inferred.',
          'Close/publication timing uses session_calendar, whose holiday list may be incomplete.',
          'First available checkpoint is retrospective baseline, not prospective evidence.',
          'Current selected universe and observed-date session proxy retain selection/calendar bias.',
          'Final outcomes stay in older immutable checkpoints; unresolved setups are carried forward.',
          'Source labels and price checks do not independently reconcile actions or source accuracy.']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def identity(row):
    return digest([row['symbol'], row['pattern'], row['decision_date']])


def runner_hash():
    return digest({'helper': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'study': hashlib.sha256(STUDY.read_bytes()).hexdigest()})


def prospective_window(cutoff, frozen_at, calendar, has_baseline, included_dates):
    """A freeze is prospective only before the first opening after its completed cutoff."""
    if frozen_at.tzinfo is None:
        raise ValueError('Freeze time must have timezone')
    result = {'eligible': False, 'frozen_at': frozen_at.isoformat(),
              'next_session_open': None, 'reason': 'First available checkpoint is retrospective baseline'}
    if not has_baseline:
        return result
    if cutoff != calendar.last_completed(frozen_at):
        result['reason'] = 'Cutoff is not the latest completed session at freeze time'
        return result
    if any(str(day) > cutoff for day in included_dates):
        result['reason'] = 'Research input includes a session after the decision cutoff'
        return result
    day = date.fromisoformat(cutoff)
    for _ in range(370):
        day += timedelta(days=1)
        spans = calendar.intervals(day)
        if spans:
            opening = datetime.combine(day, min(time.fromisoformat(a) for a, _ in spans), PKT)
            result['next_session_open'] = opening.isoformat()
            result['eligible'] = frozen_at.astimezone(PKT) < opening
            result['reason'] = ('Frozen before the next configured session open' if result['eligible']
                                else 'Next configured session has already opened')
            return result
    result['reason'] = 'No next session found in the configured calendar'
    return result


def incompatible_queue(checkpoints, contract):
    """Keep old contracts' outstanding identities without repricing them under new rules."""
    outstanding = {}
    for prior in sorted(checkpoints, key=lambda p: p['scheduled_at']):
        old_contract = prior.get('contract_hash')
        for proposal in prior.get('proposals', []):
            outstanding.setdefault((old_contract, proposal['id']), proposal)
        for outcome in prior.get('outcomes', []):
            key = old_contract, outcome['id']
            if outcome['status'] == 'unresolved':
                outstanding.setdefault(key, outcome['proposal'])
            else:
                outstanding.pop(key, None)
        for review in prior.get('review_queue', []):
            outstanding.setdefault((review.get('contract_hash'), review['proposal']['id']), review['proposal'])
    return [{'contract_hash': old, 'proposal': proposal, 'reason': 'old_contract', 'status': 'unresolved'}
            for (old, _), proposal in outstanding.items() if old != contract]


def database_argument(value):
    name, rest = value.split('=', 1)
    path, commit = rest.rsplit('=', 1)
    if not name or not path or not commit:
        raise ValueError('Database must be NAME=PATH=COMMIT')
    return name, Path(path), commit


def inspect_snapshot(path, symbols, expected):
    """Select the last broadly covered official session, ignoring future/outlier rows."""
    from data_quality import bar_error, source_priority
    placeholders = ','.join('?' for _ in symbols)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        query = 'SELECT * FROM daily_ohlc WHERE symbol IN (' + placeholders + ')'
        future = con.execute(query.replace('SELECT *', 'SELECT COUNT(*)') + ' AND date>?',
                             [*symbols, expected]).fetchone()[0]
        frame = pd.read_sql_query(query + ' AND date<=? ORDER BY symbol,date', con,
                                  params=[*symbols, expected])
    cutoff, coverage = None, {}
    for day, rows in frame.groupby('date', sort=True):
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError('Noncanonical session date')
        except (ValueError, TypeError):
            raise ValueError('Malformed historical session date; snapshot requires review')
        if len(rows) >= len(symbols):
            valid = [r['symbol'] for r in rows.to_dict('records')
                     if not bar_error(r) and source_priority(r.get('source')) >= 3]
            if len(rows) == len(symbols) and set(valid) == set(symbols):
                cutoff = day
        if day == expected:
            coverage = {r['symbol']: bool(not bar_error(r) and source_priority(r.get('source')) >= 3)
                        for r in rows.to_dict('records')}
    metadata = {'cutoff': cutoff, 'expected_session': expected,
                'expected_valid_stocks': sum(coverage.values()), 'tracked_stocks': len(symbols),
                'missing_or_invalid_expected': [s for s in symbols if not coverage.get(s)],
                'future_rows_excluded': future}
    return metadata, frame.loc[frame.date <= cutoff].copy() if cutoff else frame.iloc[:0].copy()


def research(repo, frame, destination, data_commit):
    # The subprocess releases v2's SQLite handles before Windows deletes the temporary copy.
    with tempfile.TemporaryDirectory(prefix='psx-candle-daily-') as temporary:
        path = Path(temporary) / 'completed.db'
        with closing(sqlite3.connect(path)) as con:
            frame.to_sql('daily_ohlc', con, index=False)
        subprocess.run([sys.executable, str(STUDY), '--repo', str(repo), '--database', str(path),
                        '--output', str(destination), '--data-commit', data_commit],
                       check=True, capture_output=True, text=True)
    return {'observations': json.loads((destination / 'observations.json').read_text(encoding='utf-8')),
            'evidence': json.loads((destination / 'evidence.json').read_text(encoding='utf-8'))}


def predecessor_outcomes(previous, observations):
    current = {identity(row): row for row in observations}
    pending = {p['id']: p for p in previous.get('proposals', [])}
    pending.update({o['proposal']['id']: o['proposal'] for o in previous.get('outcomes', [])
                    if o['status'] == 'unresolved'})
    outcomes = []
    for key, proposal in sorted(pending.items()):
        row = current.get(key)
        status = {'priced_scenario': 'resolved', 'unfilled': 'unfilled', 'purged': 'purged'}.get(
            row.get('status') if row else None, 'unresolved')
        outcomes.append({'id': key, 'proposal': proposal, 'status': status,
                         'observed': row, 'review_required': row is None,
                         'reason': 'Frozen setup absent from revised observations; review required' if row is None
                                   else row.get('reason'),
                         'prospective_eligible': proposal['prospective_eligible']})
    return outcomes


def write_report(output, record, checkpoint, duplicate=False):
    output.mkdir(parents=True, exist_ok=True)
    status = {**record, 'checkpoint': str(checkpoint) if checkpoint else None, 'duplicate': duplicate}
    (output / 'status.json').write_text(json.dumps(status, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    text = (f"Daily candle research: {record['status']}\n\n"
            f"Scheduled: {record['scheduled_at']} · expected session: {record['expected_session']}\n\n"
            f"Selected cutoff: {record.get('cutoff')} · snapshot: {record.get('selected_snapshot')}\n\n"
            f"Frozen research setups: {len(record.get('proposals', []))}; "
            f"predecessor outcomes: {len(record.get('outcomes', []))}.\n\n"
            "Profitability probability: not measured. Production rules unchanged.\n\n"
            + '\n'.join('- ' + limit for limit in LIMITS) + '\n')
    (output / 'summary.md').write_text(text, encoding='utf-8')
    return status


def run(repo, databases, expected_session, output, checkpoints, scheduled_at=None, extraction_errors=None,
        clock=None):
    expected = date.fromisoformat(expected_session).isoformat()
    scheduled = datetime.fromisoformat(scheduled_at) if scheduled_at else datetime.now(timezone.utc)
    if scheduled.tzinfo is None or expected > scheduled.astimezone(PKT).date().isoformat():
        raise ValueError('Scheduled time must have timezone and expected session cannot be future-dated')
    sys.path.insert(0, str(repo.resolve()))
    import config
    import session_calendar
    if expected > session_calendar.last_completed(scheduled):
        raise ValueError('Expected session is not completed under the exchange close/publication proxy')
    symbols = list(config.STOCKS)
    if len(symbols) != len(set(symbols)) or not symbols:
        raise ValueError('Configured stock list must be nonempty and unique')
    code_commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    runner = runner_hash()
    contract = digest({'specification': study.SPEC, 'symbols': sorted(symbols), 'policy': POLICY})
    record = {'version': 'psx-candle-daily-v1', 'scheduled_at': scheduled.isoformat(),
              'expected_session': expected, 'status': 'unavailable', 'code_commit': code_commit,
              'runner_hash': runner, 'contract_hash': contract, 'policy': POLICY,
              'specification': study.SPEC, 'snapshots': [],
              'proposals': [], 'outcomes': [], 'limits': LIMITS, 'profit_probability': None}
    if extraction_errors:
        errors = json.loads(Path(extraction_errors).read_text(encoding='utf-8'))
        if isinstance(errors, dict):
            errors = [{'name': name, 'error': message} for name, message in errors.items()]
        if not isinstance(errors, list) or any(not isinstance(error, dict) for error in errors):
            raise ValueError('Extraction errors must be a name/error mapping or list of snapshot error objects')
        record['snapshots'].extend({'cutoff': None, **error} for error in errors)
    best = None
    for name, path, commit in databases:
        try:
            metadata, frame = inspect_snapshot(path, symbols, expected)
        except (sqlite3.Error, ValueError, OSError) as exc:
            metadata, frame = {'cutoff': None, 'error': str(exc)}, None
        metadata.update(name=name, data_commit=commit)
        record['snapshots'].append(metadata)
        if metadata['cutoff'] and (best is None or metadata['cutoff'] > best[0]['cutoff']):
            best = metadata, frame
    if best is None:
        return write_report(output, record, None)
    selected, frame = best
    dataset = hashlib.sha256(frame.to_csv(index=False, lineterminator='\n').encode()).hexdigest()
    record.update(cutoff=selected['cutoff'], selected_snapshot=selected['name'],
                  data_commit=selected['data_commit'], dataset_hash=dataset,
                  status='available' if selected['cutoff'] == expected else 'unavailable')
    checkpoints.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoints / f"{selected['cutoff']}-{dataset[:12]}-{runner[:12]}-{contract[:8]}.json"
    if checkpoint.exists():
        frozen = json.loads(checkpoint.read_text(encoding='utf-8'))
        if (frozen['dataset_hash'] != dataset or frozen['runner_hash'] != runner
                or frozen.get('contract_hash') != contract):
            raise ValueError('Checkpoint key collision; existing checkpoint preserved')
        # Current freshness is reported independently; an older immutable checkpoint is not relabelled.
        return write_report(output, {**frozen, 'scheduled_at': scheduled.isoformat(),
                            'expected_session': expected, 'status': record['status'],
                            'snapshots': record['snapshots']}, checkpoint, True)
    earlier = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(checkpoints.glob('*.json'))]
    eligible = [p for p in earlier if p.get('cutoff', '') <= selected['cutoff']]
    compatible = [p for p in eligible if p.get('contract_hash') == contract]
    record['incompatible_checkpoints'] = sum(p not in compatible for p in eligible)
    record['review_queue'] = incompatible_queue(eligible, contract)
    previous = max(compatible, key=lambda p: p['scheduled_at'], default={})
    result = research(repo, frame, output / checkpoint.stem, selected['data_commit'])
    observations, evidence = result['observations'], result['evidence']
    record.update(coverage=evidence['coverage'], pattern_diagnostics=evidence['pattern_diagnostics'],
                  study_limits=evidence['limits'])
    record['predecessor'] = previous.get('checkpoint_key')
    record['outcomes'] = predecessor_outcomes(previous, observations)
    record['data_correction'] = any(p['cutoff'] == selected['cutoff'] and p['dataset_hash'] != dataset
                                  for p in compatible)
    frozen_ids = {p['id'] for prior in compatible for p in prior.get('proposals', [])}
    record['reused_setup_ids'] = [identity(row) for row in observations
                                 if row['decision_date'] == selected['cutoff'] and identity(row) in frozen_ids]
    frozen_at = (clock or (lambda: datetime.now(timezone.utc)))()
    has_baseline = any(p.get('status') == 'available' and p.get('cutoff', '') < selected['cutoff']
                       for p in compatible)
    window = prospective_window(selected['cutoff'], frozen_at, session_calendar, has_baseline,
                                frame.date.unique())
    record['prospective_window'] = window
    record['frozen_at'] = frozen_at.isoformat()
    if record['status'] == 'available':
        prospective = window['eligible']
        record['proposals'] = [{**row, 'id': identity(row), 'prospective_eligible': prospective,
                                'frozen_dataset_hash': dataset, 'frozen_runner_hash': runner,
                                'frozen_contract_hash': contract,
                                'frozen_at': frozen_at.isoformat()}
                               for row in observations if row['decision_date'] == selected['cutoff']
                               and identity(row) not in frozen_ids]
    record['checkpoint_key'] = checkpoint.name
    with checkpoint.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(record, indent=2, allow_nan=False) + '\n')
    return write_report(output, record, checkpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--database', action='append', type=database_argument, required=True,
                        help='NAME=PATH=COMMIT; repeat for main and runtime-state')
    parser.add_argument('--expected-session', required=True, help='Explicit completed PKT date; no holiday inference')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checkpoints', type=Path, required=True)
    parser.add_argument('--extraction-errors', type=Path,
                        help='Optional JSON name/error mapping or snapshot error list; retained in provenance')
    args = parser.parse_args()
    result = run(args.repo, args.database, args.expected_session, args.output, args.checkpoints,
                 extraction_errors=args.extraction_errors)
    print(json.dumps({k: result.get(k) for k in ('status', 'cutoff', 'expected_session', 'checkpoint', 'duplicate')}))
    return 0 if result['status'] == 'available' else 2


if __name__ == '__main__':
    raise SystemExit(main())
