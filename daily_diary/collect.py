"""Read-only, bounded collection with conservative material extraction.

Raw records stay in memory. Unknown formats and sensitive lines are omitted.
This is a minimization filter, not proof that all natural-language PII is gone.
"""
from datetime import date, datetime, timedelta
import json
import re
from urllib.parse import urlsplit

from .core import Collection, Config, DiaryError, JST, Page, Source, jst_window, safe_id


def sanitize_text(text: str) -> str:
    if not isinstance(text, str) or len(text) > 100_000:
        return '[安全に扱えない本文は省略]'
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)',
                  '[秘密鍵省略]', text, flags=re.S)
    text = re.sub(r'```.*?(?:```|\Z)', '[コード・生出力省略]', text, flags=re.S)
    lines = []
    for line in text.splitlines():
        if re.search(r'(?i)(authorization|password|passwd|api[_ -]?key|secret[_ -]?key|'
                     r'access[_ -]?token|bearer\s|顧客|氏名|第三者|電話番号|住所|'
                     r'外部へ送信|秘密を送|OK\s*とみな|ignore.*instructions|<script|'
                     r'\$\(|\b(?:curl|wget)\s)', line):
            lines.append('[機密・個人情報または資料内命令を省略]')
            continue
        line = re.sub(r'\b(?:sk|pk)-lf-[A-Za-z0-9_-]+|\b(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_-]+',
                      '[秘密省略]', line)
        line = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[連絡先省略]', line)
        line = re.sub(r'(?<!\w)(?:\+?\d[\d -]{8,}\d)(?!\w)',
                      lambda m: '[個人識別値省略]' if len(re.sub(r'\D', '', m[0])) >= 10 else m[0], line)
        line = re.sub(r'https?://[^\s)<>]+', '[URL省略]', line)
        line = re.sub(r'/(?:Users|home)/[^\s)<>]+', '[個人パス省略]', line)
        lines.append(line)
    return '\n'.join(lines)[:8000]


def _text(value) -> str | None:
    if value is None:
        return ''
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, (dict, list)):
            return _text(parsed)
        return value
    if isinstance(value, list):
        parts = [_text(x) for x in value]
        return '\n'.join(parts) if all(x is not None for x in parts) else None
    if isinstance(value, dict):
        # Only normal text/messages. Function calls, media and arbitrary objects are not material.
        if value.get('role') in ('system', 'developer', 'tool'):
            return ''
        if set(value) <= {'role', 'content'} and 'content' in value:
            return _text(value['content'])
        if value.get('type') in ('text', 'input_text', 'output_text') and isinstance(value.get('text'), str):
            return value['text']
    return None


def _time(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def sanitize_record(record: dict) -> Source | None:
    if record.get('type') != 'AGENT' or record.get('name') != 'Codex Turn':
        return None
    incoming, outgoing = _text(record.get('input')), _text(record.get('output'))
    if incoming is None or outgoing is None:
        return None
    if not incoming and not outgoing:
        return None
    try:
        for key in ('projectId', 'traceId', 'id'):
            safe_id(record[key])
    except (KeyError, DiaryError):
        return None
    start, end = _time(record.get('startTime')), _time(record.get('endTime'))
    text = '[本人発言] ' + sanitize_text(incoming) + '\n[assistant報告] ' + sanitize_text(outgoing)
    if start and end and start.astimezone(JST).date() != end.astimezone(JST).date():
        text += '\n[観測事実] この turn は日跨ぎで継続。開始日へ所属。'
    return Source(record['projectId'], record['traceId'], record['id'], record['id'],
                  start, end, '本人発言・assistant報告', text)


def collect_day(config: Config, day: date, now: datetime, fetch_page) -> Collection:
    if not config.project_id or not config.user_id or not config.session_ids:
        raise DiaryError('scope_required')
    if now.tzinfo is None:
        raise DiaryError('naive_time')
    start, end = jst_window(day)
    end = min(end, now)
    if end <= start:
        raise DiaryError('future_day')
    sources, warnings, seen, turns = [], set(), set(), set()
    cursor, cursors, complete, pages = None, set(), False, 0
    for _ in range(100):
        try:
            page = fetch_page(start, end, cursor)
            if not isinstance(page, Page) or not isinstance(page.records, list):
                raise DiaryError('invalid_page')
        except Exception:
            warnings.add('api_read_failed')
            break
        pages += 1
        for row in page.records:
            if not isinstance(row, dict):
                warnings.add('invalid_record_omitted')
                continue
            if (row.get('projectId') != config.project_id or row.get('userId') != config.user_id or
                    row.get('sessionId') not in config.session_ids):
                warnings.add('scope_mismatch_omitted')
                continue
            key = (row.get('traceId'), row.get('id'))
            if not all(isinstance(x, str) for x in key):
                warnings.add('missing_id_omitted')
                continue
            if key in seen:
                continue
            started = _time(row.get('startTime'))
            if started is None:
                warnings.add('missing_time_omitted')
                continue
            if not start <= started < end:
                continue
            seen.add(key)
            source = sanitize_record(row)
            if source is None:
                if row.get('name') == 'Codex Turn':
                    warnings.add('unsafe_or_unknown_text_omitted')
                continue
            if source.turn_id not in turns:
                sources.append(source)
                turns.add(source.turn_id)
            if '省略]' in source.text:
                warnings.add('sensitive_material_omitted')
        cursor = page.next_cursor
        if cursor is None:
            complete = True
            break
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            warnings.add('cursor_repeated_or_invalid')
            break
        cursors.add(cursor)
    if not complete:
        warnings.add('pagination_incomplete')
    warnings.add('life_unobserved_and_ingestion_may_be_delayed')
    issues = warnings - {'life_unobserved_and_ingestion_may_be_delayed'}
    status = ('取得失敗' if pages == 0 else '部分取得' if issues or not complete else
              '取得成功' if seen else '対象ログなし')
    return Collection(sorted(sources, key=lambda x: x.started_at), start, end, now, status,
                      sorted(warnings), len(seen), complete)


def collect_recent(config: Config, day: date, now: datetime, fetch_page) -> dict[date, Collection]:
    return {d: collect_day(config, d, now, fetch_page) for d in (day - timedelta(days=1), day)}
