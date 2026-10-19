"""Bounded, authenticated Observations v2 reads without an AI or SDK dependency."""
import base64
from datetime import timezone
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from .collector_runner import run
from .records import interval, timestamp
from .sources import read_observation_pages


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Do not forward the Authorization header to another host.
        raise HTTPError(request.full_url, code, 'redirect_refused', headers, fp)


def redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {redact(key, secrets): redact(item, secrets) for key, item in value.items()}
    return value


def collect(options, period):
    auth = options['auth']
    begin, end = interval(period)
    basic = base64.b64encode((auth['public_key'] + ':' + auth['secret_key']).encode()).decode()
    query = {'fromStartTime': begin.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
             'toStartTime': end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
             'name': 'Codex Turn', 'type': 'AGENT', 'fields': 'core,basic,io',
             'userId': auth['user_id'], 'limit': 100}
    opener = build_opener(NoRedirect())
    failure = []

    def fetch(cursor):
        parameters = {**query, **({'cursor': cursor} if cursor else {})}
        request = Request(auth['base_url'].rstrip('/') + '/api/public/v2/observations?' + urlencode(parameters),
                          headers={'Authorization': 'Basic ' + basic, 'Accept': 'application/json'})
        try:
            with opener.open(request, timeout=options['timeout_seconds']) as response:
                payload = response.read(10 * 1024 * 1024 + 1)
                if len(payload) > 10 * 1024 * 1024:
                    raise ValueError('response too large')
                page = json.loads(payload)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                raise ValueError('invalid response')
            for row in page['data']:
                if (row.get('userId') != auth['user_id'] or row.get('name') != 'Codex Turn'
                        or row.get('type') != 'AGENT' or not isinstance(row.get('id'), str)
                        or not row['id'] or not begin <= timestamp(row['startTime']) < end):
                    raise ValueError('response filter mismatch')
            return page
        except HTTPError as exc:
            failure.append('login_required' if exc.code in {401, 403} else 'http_failed')
            exc.close()
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            failure.append('invalid_response')
            raise

    try:
        result = read_observation_pages(fetch, auth['user_id'], options['max_pages'])
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'failed', [], [], ['invalid_response']
    records = []
    for row in result['observations']:
        records.append({'kind': 'conversation', 'external_id': row['id'],
                        'event_time': {'value': row['startTime'], 'precision': 'second',
                                       'timezone': 'UTC', 'observed_label': None},
                        'content_time': None, 'url': None,
                        'data': {'user_id': row['userId'], 'observation_id': row['id'],
                                 'trace_id': row.get('traceId'), 'session_id': row.get('sessionId'),
                                 'input': redact(row.get('input'), [auth['public_key'], auth['secret_key'], basic]),
                                 'output': redact(row.get('output'), [auth['public_key'], auth['secret_key'], basic])}})
    status = result['status']
    if not records and 'login_required' in failure:
        status = 'login_required'
    issues = failure or ([result['issue']] if result['issue'] else [])
    coverage = [{**period, 'evidence': {'method': 'observations_v2_cursor_exhausted',
                                      'reference': 'langfuse:observations-v2'}}] if status == 'success' else []
    return status, records, coverage, issues


def main(argv=None):
    return run('langfuse', collect, argv)


if __name__ == '__main__':
    raise SystemExit(main())
