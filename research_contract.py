"""Sourced research context contract. Never accepts model-authored execution levels."""
from datetime import date, datetime, timedelta, timezone
import json
from urllib.parse import urlparse

UNIVERSE = ('PRL','MEBL','SYS','PSO','GAL','EFERT','ATRL','NRL','ASL','FCL','FCEPL','CNERGY','THCCL','FABL','OGDC')
BIASES = {'supportive','mixed','adverse','unknown'}
STANCES = {'watch','supportive','cautious','avoid','unavailable'}
MAX_BYTES = 256_000


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO string')
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp needs timezone')
    return dt


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('Unsupported research schema')
    if len(json.dumps(data, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Research artifact too large')
    generated, asof, expires = (stamp(data[k]) for k in ('generated_at','as_of','expires_at'))
    if not asof <= generated < expires <= generated + timedelta(hours=72):
        raise ValueError('Invalid research validity window')
    if len(data['universe']) != len(UNIVERSE) or set(data['universe']) != set(UNIVERSE):
        raise ValueError('Research must cover the approved 15-stock universe')
    ids = set()
    source_times = {}
    for source in data['sources']:
        sid = source['id']
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError('Source identifiers must be unique')
        ids.add(sid)
        url = urlparse(source['url'])
        if url.scheme not in ('https','http') or not url.hostname or url.username or url.password:
            raise ValueError('Invalid public source URL')
        if not isinstance(source['title'], str) or not source['title'].strip() or not source['kind']:
            raise ValueError('Source title and kind required')
        verified = stamp(source['verified_at'])
        source_times[sid] = verified
        if verified > generated:
            raise ValueError('Source verified after artifact generation')
        if source.get('published_date') is not None:
            from zoneinfo import ZoneInfo
            published_day=date.fromisoformat(source['published_date'])
            if published_day > generated.astimezone(ZoneInfo('Asia/Karachi')).date():
                raise ValueError('Future publication date')
        if source.get('publication_precision') == 'date' and (source.get('published_at') is not None or not source.get('published_date')):
            raise ValueError('Date-only publication must not invent a clock time')
        if source.get('published_at') is not None and stamp(source['published_at']) > generated:
            raise ValueError('Future publication time')
    def refs(item):
        if not isinstance(item.get('source_ids'), list) or any(s not in ids for s in item['source_ids']):
            raise ValueError('Unknown source reference')
    def evidence(item, allow_no_news=False, reviewed_at=None):
        if item['status'] not in ('available','unavailable','no_material_news') or item['bias'] not in BIASES:
            raise ValueError('Invalid evidence state')
        if not isinstance(item['summary'],str) or not item['summary'].strip():
            raise ValueError('Evidence explanation required')
        refs(item)
        if item['status'] == 'no_material_news':
            if not allow_no_news or item['bias'] not in ('mixed','unknown') or not item['source_ids']:
                raise ValueError('No-material-news requires searched source evidence and a non-positive bias')
        if item['status'] in ('available','no_material_news'):
            if not item['source_ids']:
                raise ValueError('Available evidence needs sources')
            reference = reviewed_at or asof
            window = timedelta(hours=24 if reviewed_at is not None else 1)
            upper = min(generated, reference + window) if reviewed_at is not None else generated
            if not any(reference - window <= source_times[s] <= upper for s in item['source_ids']):
                raise ValueError('Evidence has no source verified within its declared review window')
    for item in data['market_context']:
        if item['category'] not in ('macro','geopolitical','sector'):
            raise ValueError('Unknown market context category')
        evidence(item)
    symbols = []
    for row in data['stocks']:
        symbols.append(row['symbol'])
        if any(not isinstance(row.get(k),str) or not row[k].strip() for k in ('thesis','countercase')):
            raise ValueError('Thesis and countercase required')
        for key in ('news','sector','fundamentals','public_sentiment'):
            review_time = stamp(row[key]['reviewed_at']) if key == 'fundamentals' and row[key]['status']=='available' else None
            evidence(row[key], allow_no_news=key=='news', reviewed_at=review_time)
        fundamentals = row['fundamentals']
        if type(fundamentals['event_review_required']) is not bool or not isinstance(fundamentals['event_triggers'],list):
            raise ValueError('Fundamental event review state required')
        for event in fundamentals.get('events', []):
            if event['kind'] not in ('earnings','agm','dividend','corporate_action'):
                raise ValueError('Invalid event kind')
            date.fromisoformat(event['date'])
            if stamp(event['known_at']) > generated:
                raise ValueError('Future known-at event timestamp')
            refs(event)
            if not event['source_ids']:
                raise ValueError('Dated events need sources')
        if fundamentals['status'] == 'available':
            reviewed, due = stamp(fundamentals['reviewed_at']), stamp(fundamentals['next_review_at'])
            if not fundamentals.get('report_period') or not reviewed <= generated or not reviewed < due <= reviewed + timedelta(days=31):
                raise ValueError('Fundamentals require report period and monthly review dates')
        for horizon in ('intraday','swing','investment'):
            view = row['horizons'][horizon]
            if view['stance'] not in STANCES or not isinstance(view['rationale'],str) or not view['rationale'].strip():
                raise ValueError('Invalid horizon assessment')
            refs(view)
            if view['stance'] in ('supportive','avoid') and not view['source_ids']:
                raise ValueError('Directional view needs sources')
    if len(symbols) != len(UNIVERSE) or set(symbols) != set(UNIVERSE):
        raise ValueError('Duplicate or missing stock research')
    return data


def current(data, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        validate(data)
        return stamp(data['as_of']) <= stamp(data['generated_at']) <= now < stamp(data['expires_at'])
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def fundamentals_current(item, now):
    try:
        return (item['status'] == 'available' and item['report_period'] and
                not item['event_review_required'] and
                stamp(item['reviewed_at']) <= now < stamp(item['next_review_at']) and
                now - stamp(item['reviewed_at']) <= timedelta(days=31))
    except (ValueError, TypeError, KeyError):
        return False


if __name__ == '__main__':
    import sys
    validate(json.load(open(sys.argv[1], encoding='utf-8')))
    print('Research context schema valid; this does not verify source truth or forecast accuracy.')
