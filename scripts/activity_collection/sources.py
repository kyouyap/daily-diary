"""Read-only adapters; caller owns authenticated API access, never this module."""
import hashlib
import json
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from .records import JST, interval, timestamp
from .store import safe


def read_observation_pages(fetch_page, user_id, max_pages=100):
    """fetch_page(cursor) supplies an already-scoped v2 response.

    A cursor loop, limit, or transport failure keeps prior observations partial.
    User identity and required row IDs are checked before accepting any page.
    """
    if not user_id or type(max_pages) is not int or max_pages < 1:
        raise ValueError('user and bounded page count required')
    rows, seen, cursor = {}, set(), None
    for _ in range(max_pages):
        try:
            page = fetch_page(cursor)
        except Exception:
            return {'status': 'partial' if rows else 'failed', 'observations': list(rows.values()), 'issue': 'page_failed'}
        if not isinstance(page.get('data'), list) or not isinstance(page.get('meta'), dict):
            raise ValueError('invalid observation page')
        for row in page['data']:
            if row.get('userId') != user_id or not isinstance(row.get('id'), str):
                raise ValueError('observation owner or identity missing')
            rows[row['id']] = row
        cursor = page['meta'].get('cursor')
        if not cursor:
            return {'status': 'success', 'observations': list(rows.values()), 'issue': None}
        if not isinstance(cursor, str) or cursor in seen:
            return {'status': 'partial', 'observations': list(rows.values()), 'issue': 'repeated_cursor'}
        seen.add(cursor)
    return {'status': 'partial', 'observations': list(rows.values()), 'issue': 'page_limit'}


def read_screentime_export(export_root, start, end):
    """Verify a pinned export pair and return JST daily per-device/app totals.

    This function does not open the live SQLite DB, run the collector, or emit
    individual sessions. Aggregate values cannot prove iCloud completeness.
    """
    root = safe(Path(export_root))
    begin, finish = interval({'start': start, 'end': end})
    manifest = json.loads(safe(root, 'current.json').read_text())
    generation = manifest['generation']
    if not re.fullmatch(r'snapshots/[0-9a-f]{32}', generation) or manifest.get('timezone') != 'Asia/Tokyo':
        raise ValueError('unknown export generation or timezone')
    if set(manifest['sha256']) != {'sessions.json', 'sessions.csv'}:
        raise ValueError('incomplete export pair')
    payloads = {}
    for name in ('sessions.json', 'sessions.csv'):
        payload = safe(root, generation + '/' + name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest['sha256'][name]:
            raise ValueError('export hash mismatch')
        payloads[name] = payload
    grouped = defaultdict(list)
    for row in json.loads(payloads['sessions.json']):
        start_at, end_at = timestamp(row['start']), timestamp(row['end'])
        if end_at < start_at:
            raise ValueError('reversed Screen Time session')
        a, b = max(start_at, begin), min(end_at, finish)
        if a < b:
            grouped[(row['device_id'], row['bundle_id'])].append((a, b))
    totals = defaultdict(float)
    for (device, app), spans in grouped.items():
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        for a, b in merged:
            a, b = a.astimezone(JST), b.astimezone(JST)
            while a < b:
                boundary = min(b, a.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
                totals[(a.date().isoformat(), device, app)] += (boundary - a).total_seconds()
                a = boundary
    result = []
    for (day, device, app), seconds in sorted(totals.items()):
        result.append({'kind': 'usage', 'external_id': f'{day}:{device}:{app}',
                       'event_time': {'value': day, 'precision': 'day', 'timezone': 'Asia/Tokyo', 'observed_label': None},
                       'content_time': None, 'url': None,
                       'data': {'device': device, 'application': app, 'duration_seconds': seconds,
                                'generation': generation, 'sha256': manifest['sha256']['sessions.json']}})
    return result
