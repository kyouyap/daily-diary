"""Private daily JSON store with recoverable multi-file commits.

No service access occurs here. A completed pending transaction is rolled
forward before any reader, writer, or backup sees the store.
"""
import copy
import fcntl
import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from .records import JST, SOURCES, alias, digest, event_date, merged_ranges, record_key, timestamp, valid_date, validate_batch


class BusyError(RuntimeError):
    pass


class RecoveryError(RuntimeError):
    pass


def safe(root, relative='.'):
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('runtime root must not traverse symlinks')
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('path outside runtime')
    current = root
    for part in rel.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('symlink in runtime path')
    return current


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(root, relative, value):
    path = safe(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def load(root, relative, default=None):
    path = safe(root, relative)
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise RecoveryError('unreadable local JSON') from exc


@contextmanager
def locked(root):
    root = safe(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = safe(root, 'collection.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError('collection store is busy') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def configuration(root):
    config = load(root, 'config.json')
    if not isinstance(config, dict) or config.get('schema_version') != 1 or not isinstance(config.get('targets'), list):
        raise ValueError('local config.json required; use the fictional example as a guide')
    seen = set()
    for t in config['targets']:
        key = (t['source'], alias(t['account']))
        if t['source'] not in SOURCES or not isinstance(t.get('identity'), str) or not t['identity'] or key in seen:
            raise ValueError('invalid or duplicate target')
        seen.add(key)
    return config


def state_path(source, account):
    if source not in SOURCES:
        raise ValueError('unknown source')
    return f'state/targets/{source}/{alias(account)}.json'


def daily_path(day, source, account):
    valid_date(day)
    state_path(source, account)
    return f'records/{day}/{source}/{account}.json'


def _recover(root):
    pending = safe(root, 'pending')
    if not pending.exists():
        return {'status': 'clean', 'recovered': 0}
    count = 0
    for p in sorted(pending.glob('*.json')):
        if p.is_symlink():
            raise RecoveryError('symlink transaction')
        tx = load(root, p.relative_to(root))
        try:
            files = tx['files']
            if not files or digest(files) != tx['sha256']:
                raise ValueError('transaction checksum mismatch')
            # Validate every destination and checksum before changing any file.
            for item in files:
                destination = item['path']
                parts = Path(destination).parts
                if parts[0] not in {'records', 'state'} or digest(item['value']) != item['sha256']:
                    raise ValueError('invalid transaction output')
                safe(root, destination)
            for item in files:
                atomic_json(root, item['path'], item['value'])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RecoveryError('invalid pending transaction; preserve for recovery') from exc
        p.unlink()
        sync_directory(pending)
        count += 1
    return {'status': 'recovered' if count else 'clean', 'recovered': count}


def recover(root):
    root = safe(root)
    with locked(root):
        return _recover(root)


def _states(root, config):
    result = []
    for t in config['targets']:
        value = load(root, state_path(t['source'], t['account']))
        if value is not None:
            if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('source') != t['source'] or value.get('account') != t['account']:
                raise RecoveryError('invalid target state')
            for field in ('covered_ranges', 'record_index', 'applied_batches', 'notification_state'):
                if field not in value:
                    raise RecoveryError('incomplete target state')
            result.append(value)
    return result


def _new_state(batch):
    midnight = timestamp(batch['observed_at']).astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return {'schema_version': 1, 'source': batch['source'], 'account': batch['account'],
            'initial_start': (midnight - timedelta(days=30)).isoformat(), 'covered_ranges': [],
            'last_attempt': None, 'last_success': None, 'blocked_reason': None, 'record_index': {},
            'applied_batches': {}, 'notification_state': None}


def _read_daily(root, path, day, source, account):
    value = load(root, path, {'schema_version': 1, 'date': day, 'source': source, 'account': account, 'records': [], 'updated_at': None})
    if not isinstance(value, dict) or (value.get('schema_version'), value.get('date'), value.get('source'), value.get('account')) != (1, day, source, account) or not isinstance(value.get('records'), list):
        raise RecoveryError('invalid daily record file')
    return value


def import_batch(root, batch):
    root = safe(root)
    with locked(root):
        _recover(root)
        config = configuration(root)
        b = validate_batch(batch, config)
        all_states = _states(root, config)
        source, account = b['source'], b['account']
        state = next((s for s in all_states if (s['source'], s['account']) == (source, account)), _new_state(b))
        batch_hash = digest(b)
        previous = state['applied_batches'].get(b['batch_id'])
        if previous:
            if previous != batch_hash:
                raise ValueError('batch ID reused for different content')
            return {'status': 'unchanged', 'changed_records': 0, 'notifications': []}
        documents, changed = {}, 0
        for input_record in b['records']:
            r = copy.deepcopy(input_record)
            day = event_date(r) or timestamp(b['observed_at']).astimezone(JST).date().isoformat()
            # Date-unknown YouTube observations have a stable identity distinct
            # from a confirmed day. Do not guess which later playback it was.
            unknown_key = digest([source, account, 'unknown', r['external_id']]) if source == 'youtube' and event_date(r) is None else None
            if unknown_key and unknown_key in state['record_index']:
                day = state['record_index'][unknown_key]['date']
            r['storage_date'] = day
            key = record_key(source, account, r)
            entry = state['record_index'].get(unknown_key or key)
            if entry:
                day = entry['date']
                key = entry['key']
            path = daily_path(day, source, account)
            if path not in documents:
                documents[path] = _read_daily(root, path, day, source, account)
            doc = documents[path]
            existing = next((item for item in doc['records'] if item['record_key'] == key), None)
            if entry and existing is None:
                raise RecoveryError('index points to a missing record')
            if existing:
                existing['last_observed_at'] = max(existing['last_observed_at'], b['observed_at'], key=timestamp)
                if source == 'youtube':
                    times = existing['data'].setdefault('observed_playback_times', [])
                    for item in [r['event_time'], *r['data'].get('observed_playback_times', [])]:
                        if item['precision'] != 'unknown' and item not in times:
                            times.append(item)
                elif source == 'screentime':
                    existing['data'] = r['data']
                elif source == 'langfuse':
                    existing['data'].update({k: v for k, v in r['data'].items() if v is not None})
                if existing['event_time']['precision'] == 'unknown' and r['event_time']['precision'] != 'unknown':
                    existing['event_time'] = r['event_time']
            else:
                r.update(record_key=key, storage_date=day, first_observed_at=b['observed_at'], last_observed_at=b['observed_at'])
                if source == 'youtube':
                    r['data']['observed_playback_times'] = []
                    for item in [r['event_time'], *input_record['data'].get('observed_playback_times', [])]:
                        if item['precision'] != 'unknown' and item not in r['data']['observed_playback_times']:
                            r['data']['observed_playback_times'].append(item)
                doc['records'].append(r)
                state['record_index'][unknown_key or key] = {'key': key, 'date': day}
                changed += 1
            doc['updated_at'] = b['observed_at']
        state['covered_ranges'] = merged_ranges([*state['covered_ranges'], *b['coverage']])
        state['last_attempt'] = {'status': b['status'], 'range': b['requested_range'], 'observed_at': b['observed_at'], 'record_count': len(b['records']), 'issues': b['issues']}
        if b['status'] == 'success':
            state['last_success'] = {'range': b['requested_range'], 'observed_at': b['observed_at']}
        issue_codes = sorted(i['code'] for i in b['issues'])
        blocked = 'login_required' if b['status'] == 'login_required' or 'login_required' in issue_codes else None
        state['blocked_reason'] = blocked
        problem = blocked or (b['status'] if b['status'] != 'success' else None)
        signature = {'status': problem, 'issues': issue_codes} if problem else None
        notifications = []
        if signature and signature != state['notification_state']:
            notifications.append({'source': source, 'account': account, **signature})
        state['notification_state'] = signature
        state['applied_batches'][b['batch_id']] = batch_hash
        outputs = [{'path': path, 'value': value, 'sha256': digest(value)} for path, value in sorted(documents.items())]
        outputs.append({'path': state_path(source, account), 'value': state, 'sha256': digest(state)})
        atomic_json(root, f'pending/{b["batch_id"]}.json', {'files': outputs, 'sha256': digest(outputs)})
        _recover(root)
        return {'status': 'stored', 'collection_status': b['status'], 'changed_records': changed,
                'notifications': notifications, 'covered_ranges': state['covered_ranges']}


def status(root):
    root = safe(root)
    with locked(root):
        _recover(root)
        config = configuration(root)
        states = _states(root, config)
        return {'targets': [{k: s[k] for k in ('source', 'account', 'initial_start', 'covered_ranges', 'last_attempt', 'last_success', 'blocked_reason')} for s in states]}


def read_day(root, date):
    valid_date(date)
    root = safe(root)
    with locked(root):
        _recover(root)
        config = configuration(root)
        states = _states(root, config)
        records = []
        # Unknown-date records stay in the first-observation file. Scan the
        # indexed files too so a later confirmed event date can be found.
        paths = set()
        for t in config['targets']:
            paths.add(daily_path(date, t['source'], t['account']))
        for s in states:
            paths.update(daily_path(e['date'], s['source'], s['account']) for e in s['record_index'].values())
        for path in sorted(paths):
            p = Path(path)
            doc = _read_daily(root, path, p.parts[1], p.parts[2], p.stem)
            for r in doc['records']:
                if event_date(r) == date or event_date(r) is None and r['storage_date'] == date:
                    records.append({**r, 'source': doc['source'], 'account': doc['account']})
        return {'date': date, 'records': records, 'targets': [{k: s[k] for k in ('source', 'account', 'covered_ranges', 'last_attempt', 'blocked_reason')} for s in states]}
