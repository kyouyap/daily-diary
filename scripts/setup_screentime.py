"""Prepare a new local Screen Time installation; never install or load an agent."""
import argparse
import json
import os
from pathlib import Path
import plistlib


def absolute(value):
    return Path(value).expanduser().absolute()


def prepare(args):
    prefix, config, database, logs, agent = map(absolute, (
        args.prefix, args.config, args.database, args.logs, args.launch_agent))
    python = absolute(args.python)
    collector = prefix / 'collector/local_screentime.py'
    files = [collector, config, agent]
    paths = [*files, database, logs, python]
    if len(set(paths)) != len(paths) or any(p in q.parents for p in files for q in paths if p != q):
        raise ValueError('overlapping_destinations')
    if any(p.exists() or p.is_symlink() for p in [*files, database]):
        raise ValueError('destination_exists')
    if any(part.is_symlink() for p in [*files, database, logs] for part in [p, *p.parents]):
        raise ValueError('symlink_destination')
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('python_unavailable')
    source = Path(__file__).resolve().parents[1] / 'resources/screentime-local/local_screentime.py'
    content = source.read_bytes()
    plist = {'Label': 'io.github.nichtlegacy.screentime',
             'ProgramArguments': [str(python), '-I', '-B', str(collector), 'collect', '--config', str(config)],
             'StartInterval': 900, 'RunAtLoad': True, 'Umask': 0o077,
             'EnvironmentVariables': {'TZ': 'Asia/Tokyo'},
             'StandardOutPath': str(logs / 'stdout.log'), 'StandardErrorPath': str(logs / 'stderr.log')}
    settings = ('timezone = "Asia/Tokyo"\ndb_path = ' + json.dumps(str(database), ensure_ascii=False)
                + '\n[sources]\nbiome = true\nknowledgec = true\n[lookup]\nenabled = false\n')
    # Validate every destination before creating files. O_EXCL also handles a
    # conflicting writer that appears after the preflight check.
    payloads = [(collector, content), (config, settings.encode()), (agent, plistlib.dumps(plist))]
    created = []
    try:
        for directory in [database.parent, logs, *(p.parent for p, _ in payloads)]:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path, payload in payloads:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
    except OSError:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return {'status': 'prepared', 'agent_loaded': False, 'collector_run': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prefix', 'python', 'config', 'database', 'logs', 'launch-agent'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args)
        code = 0
    except ValueError as exc:
        result, code = {'status': 'failed', 'issue': str(exc)}, 2
    except OSError:
        result, code = {'status': 'failed', 'issue': 'destination_unavailable'}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
