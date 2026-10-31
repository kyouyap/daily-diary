"""Read an existing location CSV for diary reference; never save or use network."""
import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo('Asia/Tokyo')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed.astimezone(JST)


def check_range(start, end):
    if (start.tzinfo is None or start.utcoffset() is None
            or end.tzinfo is None or end.utcoffset() is None or start >= end):
        raise ValueError('invalid range')


def unavailable(code):
    return {'status': 'unavailable', 'records': [], 'issues': [{'code': code}]}


def read_location(path: Path, start: datetime, end: datetime) -> dict:
    check_range(start, end)
    result = {'status': 'read', 'records': [], 'issues': [],
              'range': {'start': start.astimezone(JST).isoformat(),
                        'end': end.astimezone(JST).isoformat()}}
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f, strict=True)
            headers = next(reader, [])
            required = {'record_type', 'start_time', 'end_time', 'title'}
            if not required.issubset(headers):
                return unavailable('invalid_csv_headers')
            previous_line = reader.line_num
            for values in reader:
                line, previous_line = previous_line + 1, reader.line_num
                if not values:
                    continue
                row = dict(zip(headers, values))
                try:
                    if row.get('record_type') not in {'place', 'move'}:
                        raise ValueError('invalid kind')
                    a = timestamp(row.get('start_time') or '')
                    b = timestamp(row['end_time']) if row.get('end_time') else None
                    if b is not None and b < a:
                        raise ValueError('reversed times')
                except (ValueError, TypeError, OverflowError):
                    result['issues'].append({'line': line, 'code': 'invalid_record_time_or_kind'})
                    continue
                # Open/instant records are observations, not evidence of a full-day stay.
                overlaps = (start <= a < end if b is None or a == b
                            else a < end and b > start)
                if not overlaps:
                    continue
                lat, lon = row.get('latitude') or '', row.get('longitude') or ''
                coordinates = [None, None]
                if lat or lon:
                    try:
                        coordinates = [float(lat), float(lon)]
                        if not (all(math.isfinite(v) for v in coordinates)
                                and -90 <= coordinates[0] <= 90
                                and -180 <= coordinates[1] <= 180):
                            raise ValueError('invalid coordinates')
                    except (ValueError, TypeError):
                        coordinates = [None, None]
                        result['issues'].append({'line': line, 'code': 'invalid_coordinates'})
                result['records'].append({
                    'line': line, 'kind': row['record_type'],
                    'start': a.isoformat(), 'end': b.isoformat() if b else None,
                    'label': row.get('title') or '',
                    'latitude': coordinates[0], 'longitude': coordinates[1],
                    'transport_mode': row.get('transport_mode') or '',
                })
    except (OSError, UnicodeError, csv.Error, ValueError):
        if not result['records']:
            return unavailable('csv_unreadable')
        result['issues'].append({'code': 'csv_read_interrupted'})
    if result['issues']:
        result['status'] = 'partial'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path.home() / '.codex/daily-diary.json'))
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args(argv)
    try:
        start, end = timestamp(args.start), timestamp(args.end)
        check_range(start, end)
    except (ValueError, TypeError, OverflowError):
        print(json.dumps({'status': 'invalid_request', 'records': [],
                          'issues': [{'code': 'invalid_range'}]}))
        return 2
    try:
        config = json.loads(Path(args.config).expanduser().read_text(encoding='utf-8'))
        path = config.get('location_csv')
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError('location_csv not configured as an absolute path')
    except (OSError, UnicodeError, ValueError, AttributeError):
        result = unavailable('location_not_configured')
    else:
        result = read_location(Path(path), start, end)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if result['status'] == 'unavailable' else 0


if __name__ == '__main__':
    raise SystemExit(main())
