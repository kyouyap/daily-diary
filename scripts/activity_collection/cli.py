"""Local-only collection store commands. No credentials or browser API."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from . import store
from .records import JST, timestamp
from .state import plan_collection


def main(argv=None):
    parser = argparse.ArgumentParser(description='活動記録の保存と取得要求（外部への通信なし）')
    parser.add_argument('--root', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--now')
    imp = commands.add_parser('import')
    imp.add_argument('--input', type=Path, required=True)
    commands.add_parser('status')
    commands.add_parser('recover')
    commands.add_parser('backup')
    restore = commands.add_parser('restore')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    day = commands.add_parser('read-day')
    day.add_argument('--date', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'import':
            result = store.import_batch(args.root, json.loads(args.input.read_text()))
        elif args.command == 'status':
            result = store.status(args.root)
        elif args.command == 'recover':
            result = store.recover(args.root)
        elif args.command == 'read-day':
            result = store.read_day(args.root, args.date)
        elif args.command == 'backup':
            from .backup import create_backup
            result = create_backup(args.root)
        elif args.command == 'restore':
            from .backup import restore_backup
            result = restore_backup(args.backup, args.destination)
        else:
            states = store.status(args.root)['targets']
            result = {'requests': plan_collection(store.configuration(args.root), states, timestamp(args.now) if args.now else datetime.now(JST))}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except store.BusyError:
        print('{"error":"busy"}')
        return 3
    except store.RecoveryError:
        print('{"error":"recovery_required"}')
        return 4
    except (ValueError, KeyError, TypeError):
        print('{"error":"invalid_input_or_configuration"}')
        return 2
    except OSError:
        print('{"error":"local_io_failed"}')
        return 4


if __name__ == '__main__':
    sys.exit(main())
