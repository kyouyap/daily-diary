import copy
import json
import uuid
from pathlib import Path

CONFIG = {'schema_version': 1, 'targets': [
    {'source': 'x', 'account': 'account-a', 'identity': 'fictional-owner'},
    {'source': 'youtube', 'account': 'account-a', 'identity': 'fictional-channel'},
    *[{'source': s, 'account': 'self', 'identity': 'fictional-owner'} for s in ('langfuse', 'computer-history', 'screentime')],
], 'backup_generations': 7}


def runtime(directory):
    root = Path(directory).resolve() / 'runtime'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps(CONFIG))
    return root


def event(value='2026-10-16T12:00:00+09:00', precision='second'):
    return {'value': value, 'precision': precision, 'timezone': 'Asia/Tokyo' if value else None, 'observed_label': None}


def record(kind='post', external_id='123', time=None, data=None):
    return {'kind': kind, 'external_id': external_id, 'event_time': time or event(),
            'content_time': None, 'url': None,
            'data': data if data is not None else {'author_id': 'fictional-owner', 'text': '架空の投稿'}}


def batch(records=None, source='x', status='success', account=None):
    period = {'start': '2026-10-16T00:00:00+09:00', 'end': '2026-10-17T00:00:00+09:00'}
    return {'schema_version': 1, 'batch_id': str(uuid.uuid4()), 'source': source,
            'account': account or ('account-a' if source in ('x', 'youtube') else 'self'),
            'observed_identity': 'fictional-channel' if source == 'youtube' else 'fictional-owner',
            'requested_range': period, 'observed_at': '2026-10-17T07:00:00+09:00',
            'status': status, 'coverage': [{**period, 'evidence': {'method': 'synthetic_complete', 'reference': 'fixture'}}] if status == 'success' else [],
            'records': [record()] if records is None else records, 'issues': []}


def again(value, observed=None):
    result = copy.deepcopy(value)
    result['batch_id'] = str(uuid.uuid4())
    if observed:
        result['observed_at'] = observed
    return result
