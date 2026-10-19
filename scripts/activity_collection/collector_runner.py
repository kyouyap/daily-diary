"""Shared private batch persistence and compact results for manual collectors."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4
from . import store
from .collector_config import ConfigurationError, load_collector
from .records import JST, interval, validate_batch


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigurationError('invalid_arguments')


def run(source, collect, argv=None):
    parser = JsonArgumentParser(description=f'{source}: read existing sources and save locally (prototype)')
    parser.add_argument('--root', required=True, type=Path, help='Existing private runtime directory')
    parser.add_argument('--settings', type=Path, help='Default: <root>/collectors.json; relative source paths use its directory')
    parser.add_argument('--start', help='Inclusive ISO timestamp with UTC offset')
    parser.add_argument('--end', help='Exclusive ISO timestamp with UTC offset')
    parser.add_argument('--check', action='store_true', help='Check configuration and source locations only; no HTTP or writes')
    result = {'source': source, 'status': 'environment_unavailable', 'save_status': 'not_requested',
              'records_read': 0, 'issues': []}
    try:
        args = parser.parse_args(argv)
        root = store.safe(args.root.expanduser().absolute())
        # Do not let the store's writer implicitly create a misconfigured runtime.
        if not root.is_dir() or not (root / 'config.json').is_file():
            raise ConfigurationError('runtime_not_configured')
        settings = (args.settings.expanduser().absolute() if args.settings else root / 'collectors.json')
        now = datetime.now(JST)
        if not args.check:
            period = {'start': args.start, 'end': args.end}
            begin, end = interval(period)
            if end > now:
                raise ConfigurationError('future_range')
            if source == 'screentime' and begin.astimezone(JST).time().isoformat() != '00:00:00':
                raise ConfigurationError('midnight_start_required')
        config, target, options = load_collector(root, settings, source)
        if args.check:
            result['status'] = 'ready'
            print(json.dumps(result, ensure_ascii=False))
            return 0
        status, records, coverage, issues = collect(options, period)
        batch = {'schema_version': 1, 'batch_id': str(uuid4()), 'source': source,
                 'account': target['account'], 'observed_identity': target['identity'],
                 'requested_range': period, 'observed_at': datetime.now(JST).isoformat(),
                 'status': status, 'records': records, 'coverage': coverage,
                 'issues': [{'code': code, 'retryable': True, 'range': period} for code in sorted(set(issues))]}
        validate_batch(batch, config)
        result.update(status=status, records_read=len(records), issues=sorted(set(issues)), batch_id=batch['batch_id'])
        # Retain the exact receipt before import so an uncertain write is retried
        # with the same ID. Original input and auth files are never copied here.
        result['save_status'] = 'failed'
        store.atomic_json(root, f'batches/{batch["batch_id"]}.json', batch)
        receipt = store.import_batch(root, batch)
        result.update(save_status='saved', changed_records=receipt['changed_records'],
                      notifications=receipt['notifications'], backup=receipt.get('backup'))
        exit_code = 0 if status == 'success' else 1
    except ConfigurationError as exc:
        result['issues'] = [str(exc)]
        exit_code = 2
    except store.BusyError:
        result['issues'] = sorted(set([*result['issues'], 'store_busy']))
        exit_code = 3
    except store.RecoveryError:
        result['issues'] = sorted(set([*result['issues'], 'recovery_required']))
        exit_code = 4
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        # A traceback or an HTTP error message can contain private text/paths.
        result['issues'] = sorted(set([*result['issues'], 'invalid_input_or_unavailable_source']))
        exit_code = 4 if result['save_status'] == 'failed' else 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return exit_code
