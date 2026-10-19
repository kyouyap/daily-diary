"""Explicit source locations, resolved relative to the settings file, not cwd."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from . import store


class ConfigurationError(ValueError):
    """A public reason code; never attach credentials or raw exception text."""


def read_json(path, missing_code):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ConfigurationError(missing_code) from None


def configured_path(value, settings_path):
    if not isinstance(value, str) or not value or 'REPLACE_' in value:
        raise ConfigurationError('invalid_settings')
    path = Path(value).expanduser()
    # Normalize '..' lexically while retaining symlinks for the source/store's
    # own checks. Path.resolve() would hide an unexpected symlink traversal.
    return Path(os.path.abspath(path if path.is_absolute() else settings_path.parent / path))


def load_collector(root, settings_path, source):
    config = store.configuration(root)
    value = read_json(settings_path, 'settings_missing')
    if not isinstance(value, dict) or value.get('schema_version') != 1 or set(value) != {'schema_version', 'collectors'}:
        raise ConfigurationError('invalid_settings')
    entries = value['collectors']
    if not isinstance(entries, dict) or set(entries) - {'screentime', 'langfuse', 'computer-history'}:
        raise ConfigurationError('invalid_settings')
    options = entries.get(source)
    allowed = {
        'screentime': {'account', 'identity', 'export_root'},
        'langfuse': {'account', 'credentials_file', 'max_pages', 'timeout_seconds'},
        'computer-history': {'account', 'identity', 'event_stream_root', 'max_events', 'max_segments'},
    }[source]
    if not isinstance(options, dict) or set(options) - allowed:
        raise ConfigurationError('invalid_settings')
    target = next((t for t in config['targets'] if t['source'] == source and t['account'] == options.get('account')), None)
    if target is None or target['identity'].startswith('REPLACE_'):
        raise ConfigurationError('target_not_configured')
    options = dict(options)
    if source == 'langfuse':
        credentials = configured_path(options.get('credentials_file'), settings_path)
        info = credentials.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ConfigurationError('credentials_permissions')
        auth = read_json(credentials, 'credentials_missing')
        if not isinstance(auth, dict) or not all(isinstance(auth.get(k), str) and auth[k]
                for k in ('base_url', 'public_key', 'secret_key', 'user_id')):
            raise ConfigurationError('credentials_missing')
        if auth['user_id'] != target['identity']:
            raise ConfigurationError('identity_mismatch')
        parsed = urlsplit(auth['base_url'])
        local_http = parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        if (parsed.scheme != 'https' and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigurationError('invalid_base_url')
        options['auth'] = auth
        for key, default, ceiling in [('max_pages', 100, 1000), ('timeout_seconds', 30, 120)]:
            number = options.setdefault(key, default)
            if type(number) is not int or not 1 <= number <= ceiling:
                raise ConfigurationError('invalid_settings')
    else:
        if options.get('identity') != target['identity']:
            raise ConfigurationError('identity_mismatch')
        key = 'export_root' if source == 'screentime' else 'event_stream_root'
        options[key] = configured_path(options.get(key), settings_path)
        path = options[key]
        required = path / ('current.json' if source == 'screentime' else 'segments')
        if not path.is_dir() or not required.exists() or path.stat().st_uid != os.getuid():
            raise ConfigurationError('export_unavailable' if source == 'screentime' else 'history_unavailable')
        if source == 'computer-history':
            for key, default in [('max_events', 100000), ('max_segments', 1000)]:
                number = options.setdefault(key, default)
                if type(number) is not int or not 1 <= number <= 1000000:
                    raise ConfigurationError('invalid_settings')
    return config, target, options
