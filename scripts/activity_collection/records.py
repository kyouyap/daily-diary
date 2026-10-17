"""Validate observations without reading services or credentials."""
import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
SOURCES = {'x', 'youtube', 'langfuse', 'computer-history', 'screentime'}
STATUSES = {'success', 'partial', 'failed', 'login_required', 'environment_unavailable'}
FIELDS = {
    'x': {'author_id', 'text', 'reply_to_id', 'original_id', 'original_author_id', 'original_text', 'owner_id'},
    'youtube': {'title', 'channel', 'category', 'duration_seconds', 'observed_playback_times'},
    'langfuse': {'user_id', 'observation_id', 'trace_id', 'session_id', 'input', 'output'},
    'computer-history': {'segment_id', 'event_id', 'line', 'application', 'action', 'window_title', 'url', 'observation'},
    'screentime': {'generation', 'sha256', 'device', 'application', 'duration_seconds'},
}
KINDS = {'x': {'post', 'reply', 'quote', 'repost'}, 'youtube': {'playback'}, 'langfuse': {'conversation'},
         'computer-history': {'activity'}, 'screentime': {'usage'}}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('explicit timezone required')
    return parsed


def valid_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('invalid calendar date')
    return value


def alias(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', value):
        raise ValueError('invalid local alias')
    return value


def interval(value):
    start, end = timestamp(value['start']), timestamp(value['end'])
    if start >= end:
        raise ValueError('empty or reversed interval')
    return start, end


def merged_ranges(ranges):
    values = sorted(interval(r) for r in ranges)
    merged = []
    for start, end in values:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in merged]


def checked_time(value):
    if not isinstance(value, dict) or set(value) != {'value', 'precision', 'timezone', 'observed_label'}:
        raise ValueError('invalid time fields')
    precision, raw = value['precision'], value['value']
    if precision == 'unknown':
        if raw is not None:
            raise ValueError('unknown time must have null value')
    elif precision == 'day':
        valid_date(raw)
    elif precision in {'second', 'minute'}:
        timestamp(raw)
    else:
        raise ValueError('invalid time precision')
    if value['timezone'] is not None:
        ZoneInfo(value['timezone'])
    if value['observed_label'] is not None and not isinstance(value['observed_label'], str):
        raise ValueError('invalid observed label')


def event_date(record):
    value = record['event_time']
    if value['precision'] == 'unknown' or value['timezone'] is None:
        return None
    if value['precision'] == 'day':
        # A day in another zone cannot be converted to an exact JST day.
        return value['value'] if value['timezone'] == 'Asia/Tokyo' else None
    return timestamp(value['value']).astimezone(JST).date().isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def record_key(source, account, record):
    parts = [source, account, record['kind'], record['external_id']]
    if source == 'youtube':
        parts += ['dated' if event_date(record) else 'unknown', event_date(record) or record['storage_date']]
    elif source == 'screentime':
        parts = [source, account, record['storage_date'], record['data']['device'], record['data']['application']]
    return digest(parts)


def validate_batch(batch, config):
    try:
        value = copy.deepcopy(batch)
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('unsupported batch version')
        UUID(value['batch_id'])
        source, account = value['source'], alias(value['account'])
        if source not in SOURCES:
            raise ValueError('unsupported source')
        target = next((t for t in config['targets'] if (t['source'], t['account']) == (source, account)), None)
        if target is None or value['observed_identity'] != target['identity']:
            raise ValueError('unverified source identity')
        if set(value['requested_range']) != {'start', 'end'}:
            raise ValueError('unexpected requested range fields')
        start, end = interval(value['requested_range'])
        observed = timestamp(value['observed_at'])
        if end > observed or value['status'] not in STATUSES:
            raise ValueError('invalid collection status or future range')
        if not all(isinstance(value[k], list) for k in ('records', 'coverage', 'issues')):
            raise ValueError('invalid batch arrays')
        for c in value['coverage']:
            a, b = interval(c)
            evidence = c['evidence']
            if not (start <= a < b <= end) or not all(isinstance(evidence[k], str) and evidence[k] for k in ('method', 'reference')):
                raise ValueError('invalid coverage evidence')
        coverage = merged_ranges(value['coverage'])
        if value['status'] == 'success' and coverage != merged_ranges([value['requested_range']]):
            raise ValueError('success requires full requested coverage')
        if value['status'] not in {'success', 'partial'} and (value['records'] or coverage):
            raise ValueError('failure cannot assert coverage or records')
        for issue in value['issues']:
            if set(issue) != {'code', 'retryable', 'range'} or not re.fullmatch('[a-z][a-z0-9_]*', issue['code']) or type(issue['retryable']) is not bool:
                raise ValueError('invalid issue')
            if issue['range'] is not None:
                if set(issue['range']) != {'start', 'end'}:
                    raise ValueError('unexpected issue range fields')
                a, b = interval(issue['range'])
                if not start <= a < b <= end:
                    raise ValueError('issue outside request')
        for r in value['records']:
            if set(r) != {'kind', 'external_id', 'event_time', 'content_time', 'url', 'data'}:
                raise ValueError('invalid record fields')
            if r['kind'] not in KINDS[source] or not isinstance(r['external_id'], str) or not r['external_id']:
                raise ValueError('invalid record identity')
            checked_time(r['event_time'])
            if r['content_time'] is not None:
                checked_time(r['content_time'])
            if r['url'] is not None:
                parsed = urlsplit(r['url'])
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('invalid source URL')
            d = r['data']
            if not isinstance(d, dict) or set(d) - FIELDS[source]:
                raise ValueError('unexpected source data fields')
            day = event_date(r)
            last_day = (end.astimezone(JST) - timedelta(microseconds=1)).date().isoformat()
            if day and not (start.astimezone(JST).date().isoformat() <= day <= last_day):
                raise ValueError('record outside request dates')
            if r['event_time']['precision'] in {'second', 'minute'}:
                if not start <= timestamp(r['event_time']['value']) < end:
                    raise ValueError('record outside request')
            if source == 'x':
                if not isinstance(d.get('text'), str) or not isinstance(d.get('author_id'), str):
                    raise ValueError('missing post text or author')
                if r['kind'] != 'repost' and d['author_id'] != target['identity']:
                    raise ValueError('post written by another author')
                if r['kind'] == 'reply' and not isinstance(d.get('reply_to_id'), str):
                    raise ValueError('reply target required')
                if r['kind'] == 'quote' and not all(isinstance(d.get(k), str) and d[k] for k in ('original_id', 'original_author_id')):
                    raise ValueError('quote reference required')
            elif source == 'youtube':
                if not isinstance(d.get('title'), str):
                    raise ValueError('missing video title')
                for playback in d.get('observed_playback_times', []):
                    checked_time(playback)
                    playback_day = event_date({'event_time': playback})
                    if playback_day and playback_day != day:
                        raise ValueError('playback belongs to a different day')
                    if playback['precision'] in {'second', 'minute'} and not start <= timestamp(playback['value']) < end:
                        raise ValueError('playback outside request')
            elif source == 'langfuse':
                if d.get('user_id') != target['identity'] or d.get('observation_id') != r['external_id']:
                    raise ValueError('conversation owner or ID mismatch')
            elif source == 'computer-history':
                if not d.get('event_id') and not (isinstance(d.get('segment_id'), str) and type(d.get('line')) is int and d['line'] > 0):
                    raise ValueError('stable event reference required')
            elif source == 'screentime':
                if r['event_time']['precision'] != 'day' or r['event_time']['timezone'] != 'Asia/Tokyo':
                    raise ValueError('daily JST usage required')
                day_start = datetime.fromisoformat(day).replace(tzinfo=JST)
                if start > day_start:
                    raise ValueError('daily usage must cover a midnight prefix; request the full day again')
                if not all(isinstance(d.get(k), str) and d[k] for k in ('device', 'application', 'generation', 'sha256')):
                    raise ValueError('missing Screen Time reference')
            if 'duration_seconds' in d and (type(d['duration_seconds']) not in (int, float) or d['duration_seconds'] < 0):
                raise ValueError('invalid duration')
        # Reject NaN/infinity and non-JSON values anywhere before storage.
        digest(value)
        return value
    except (KeyError, TypeError, StopIteration, OverflowError) as exc:
        raise ValueError('malformed collection batch') from exc
