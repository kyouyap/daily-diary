"""Read an existing verified export; never run or reconfigure the OS collector."""
from .collector_runner import run
from .sources import read_screentime_export


def collect(options, period):
    try:
        rows = read_screentime_export(options['export_root'], period['start'], period['end'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'failed', [], [], ['export_invalid_or_unreadable']
    # Hash integrity proves a consistent snapshot, not Apple/iCloud coverage.
    return 'partial', rows, [], ['source_coverage_unknown']


def main(argv=None):
    return run('screentime', collect, argv)


if __name__ == '__main__':
    raise SystemExit(main())
