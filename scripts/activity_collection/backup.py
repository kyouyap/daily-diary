"""Local directory snapshots; never upload and never overwrite a live store."""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from . import store
from .records import timestamp

GENERATION = re.compile(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}')


def _files(root):
    paths = [store.safe(root, 'config.json')]
    for folder in ('records', 'state'):
        directory = store.safe(root, folder)
        if directory.exists():
            for p in directory.rglob('*'):
                store.safe(root, p.relative_to(root))
                if p.is_file():
                    paths.append(p)
    return paths


def _create_backup_locked(root, observation_day=None):
    config = store.configuration(root)
    keep = config.get('backup_generations', 7)
    if type(keep) is not int or not 1 <= keep <= 100:
        raise ValueError('invalid backup retention')
    backups = store.safe(root, 'backups')
    backups.mkdir(mode=0o700, exist_ok=True)
    completed = sorted(p for p in backups.iterdir() if GENERATION.fullmatch(p.name) and not p.is_symlink() and p.is_dir())
    for p in completed:
        manifest = store.load(root, p.relative_to(root) / 'manifest.json')
        if not isinstance(manifest, dict):
            raise store.RecoveryError('incomplete backup generation')
        if observation_day and manifest.get('observation_day') == observation_day:
            return {'status': 'already_saved_today', 'path': str(p)}
    generation = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex
    staging = backups / ('.incomplete-' + generation)
    staging.mkdir(mode=0o700)
    hashes = {}
    try:
        for p in _files(root):
            rel = p.relative_to(root)
            data = p.read_bytes()
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            hashes[rel.as_posix()] = hashlib.sha256(data).hexdigest()
            store.sync_directory(target.parent)
        store.atomic_json(staging, 'manifest.json', {'schema_version': 1, 'observation_day': observation_day, 'files': hashes})
        final = backups / generation
        os.replace(staging, final)
        store.sync_directory(backups)
    except Exception:
        # Only our uniquely named incomplete copy is disposable.
        shutil.rmtree(staging)
        raise
    for old in (completed + [final])[:-keep]:
        # Do not follow links or delete unknown directories during retention.
        if old.is_symlink() or any(p.is_symlink() for p in old.rglob('*')):
            raise store.RecoveryError('unsafe old backup; retention stopped')
        shutil.rmtree(old)
    store.sync_directory(backups)
    return {'status': 'created', 'path': str(final)}


def create_backup(root):
    root = store.safe(root)
    with store.locked(root):
        store._recover(root)
        store._states(root, store.configuration(root))
        return _create_backup_locked(root)


def restore_backup(backup_dir, destination):
    source, dest = store.safe(backup_dir), store.safe(destination)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError('restore destination must be empty')
    if source == dest or source in dest.parents or dest in source.parents:
        raise ValueError('restore must be independent of backup storage')
    manifest = store.load(source, 'manifest.json')
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict) or 'config.json' not in manifest['files']:
        raise ValueError('invalid backup manifest')
    contents = {}
    for rel, expected in manifest['files'].items():
        parts = Path(rel).parts
        if rel != 'config.json' and (not parts or parts[0] not in {'records', 'state'}):
            raise ValueError('unexpected file in backup')
        data = store.safe(source, rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('backup checksum mismatch')
        contents[rel] = json.loads(data)
    # Check referential integrity before creating the destination.
    expected_files = {p.relative_to(source).as_posix() for p in _files(source)}
    if set(contents) != expected_files:
        raise ValueError('backup manifest omits snapshot files')
    # Validate only the verified in-memory payloads that will actually be
    # restored. Unlisted source files must never supply missing state.
    try:
        config = store.validate_configuration(contents['config.json'])
        targets = {(t['source'], t['account']) for t in config['targets']}
        states = {}
        for path, state in contents.items():
            if not path.startswith('state/'):
                continue
            if path != store.state_path(state['source'], state['account']) or state['schema_version'] != 1:
                raise ValueError('invalid state path or version')
            if (state['source'], state['account']) not in targets:
                raise ValueError('state target absent from config')
            states[(state['source'], state['account'])] = state
            for entry in state['record_index'].values():
                document = contents.get(store.daily_path(entry['date'], state['source'], state['account']))
                if not document or not any(r['record_key'] == entry['key'] for r in document['records']):
                    raise ValueError('backup index points to missing record')
        for path, doc in contents.items():
            if not path.startswith('records/'):
                continue
            if path != store.daily_path(doc['date'], doc['source'], doc['account']) or doc['schema_version'] != 1:
                raise ValueError('invalid daily record path or version')
            state = states.get((doc['source'], doc['account']))
            if state is None:
                raise ValueError('backup records have no target state')
            index = {(e['date'], e['key']) for e in state['record_index'].values()}
            if any((doc['date'], r['record_key']) not in index for r in doc['records']):
                raise ValueError('backup record is absent from index')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('invalid snapshot structure') from exc
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    for rel, value in contents.items():
        store.atomic_json(dest, rel, value)
    return {'status': 'restored', 'files': len(contents), 'destination': str(dest)}
