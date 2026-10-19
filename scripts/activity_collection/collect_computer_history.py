"""Extract app/action evidence from existing segments; do not copy AX or input."""
import json
from .collector_runner import run
from .records import interval, timestamp


def collect(options, period):
    begin, end = interval(period)
    segments_root = options['event_stream_root'] / 'segments'
    records, issues = [], ['history_coverage_unknown']
    scanned = 0
    seen = set()
    try:
        segments = sorted(p for p in segments_root.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return 'failed', [], [], ['history_unavailable']
    selected = []
    for segment in segments:
        try:
            metadata_path = segment / 'metadata.json'
            if metadata_path.is_symlink():
                raise ValueError('symlink metadata')
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            # Segment starts are scheduling hints, never a coverage claim.
            start = timestamp(metadata['startedAt'])
            finish = timestamp(metadata['endedAt']) if metadata.get('endedAt') else None
            if finish and finish < start:
                raise ValueError('reversed segment')
            if start >= end or (finish is not None and finish <= begin):
                continue
            selected.append(segment)
        except (OSError, ValueError, KeyError, TypeError):
            # Unknown metadata cannot safely exclude a segment with valid rows.
            issues.append('invalid_segment_metadata')
            selected.append(segment)
    if len(selected) > options['max_segments']:
        issues.append('segment_limit')
        selected = selected[:options['max_segments']]
    for segment in selected:
        path = segment / 'events.jsonl'
        try:
            if path.is_symlink():
                raise ValueError('symlink event stream')
            with path.open(encoding='utf-8') as stream:
                for line_number, line in enumerate(stream, 1):
                    if scanned >= options['max_events']:
                        return 'partial', records, [], [*issues, 'event_limit']
                    scanned += 1
                    try:
                        row = json.loads(line)
                        at = timestamp(row['timestamp'])
                        if not begin <= at < end:
                            continue
                        application = row.get('app', {}).get('name') or row.get('app', {}).get('bundleIdentifier')
                        action = row.get('kind')
                        if not isinstance(application, str) or not application or not isinstance(action, str) or not action:
                            raise ValueError('unknown event shape')
                        # IDs may be numeric and only unique within a segment.
                        external_id = f'{segment.name}:{line_number}'
                        if external_id in seen:
                            continue
                        seen.add(external_id)
                        records.append({'kind': 'activity', 'external_id': external_id,
                                        'event_time': {'value': at.isoformat(), 'precision': 'second',
                                                       'timezone': 'UTC', 'observed_label': None},
                                        'content_time': None, 'url': None,
                                        'data': {'segment_id': segment.name, 'line': line_number,
                                                 'application': application, 'action': action}})
                    except (ValueError, KeyError, TypeError, AttributeError):
                        issues.append('invalid_event')
        except (OSError, ValueError):
            issues.append('segment_unreadable')
    # Retained files cannot prove recording was enabled throughout the request.
    return 'partial', records, [], issues


def main(argv=None):
    return run('computer-history', collect, argv)


if __name__ == '__main__':
    raise SystemExit(main())
