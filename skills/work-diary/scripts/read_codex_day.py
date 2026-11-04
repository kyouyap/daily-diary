#!/usr/bin/env python3
"""Read scoped, timestamped Codex evidence without changing source files."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import unicodedata


def aware_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp requires a timezone')
    return parsed


def normalized_path(value):
    return Path(unicodedata.normalize('NFC', str(Path(value).expanduser().resolve())))


def plain_text(value):
    """Keep text blocks; don't serialize images, internal metadata or binary blobs."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(filter(None, (plain_text(item) for item in value)))
    if isinstance(value, dict):
        if value.get('type') in ('input_text', 'output_text', 'text'):
            return plain_text(value.get('text', ''))
        if 'content' in value:
            return plain_text(value['content'])
    return ''


def injected_context(text):
    stripped = text.strip()
    return (stripped.startswith('# AGENTS.md instructions for ')
            or (stripped.startswith('<environment_context>')
                and stripped.endswith('</environment_context>')))


def message_text(content, filter_context):
    if not isinstance(content, list):
        return '', True
    parts, malformed = [], False
    for block in content:
        if not isinstance(block, dict):
            malformed = True
        elif block.get('type') in ('input_text', 'output_text', 'text'):
            text = block.get('text')
            if not isinstance(text, str):
                malformed = True
            elif not (filter_context and injected_context(text)):
                parts.append(text)
        elif block.get('type') not in ('input_image', 'output_image', 'image', 'image_url', 'input_file'):
            malformed = True
    return '\n'.join(parts), malformed


def read_day(home, roots, start, end, offset=0, limit=200, max_chars=2000):
    records, issues = [], []
    roots = [normalized_path(root) for root in roots]
    directories = [home / 'sessions', home / 'archived_sessions']
    available = [directory for directory in directories if directory.is_dir()]
    if not available:
        issues.append({'code': 'transcripts_unavailable'})
    elif not directories[0].is_dir():
        issues.append({'code': 'sessions_unavailable'})
    files = sorted({path for directory in available for path in directory.rglob('*.jsonl')})
    for path in files:
        cwd, session_id, subagent = None, None, False
        current, legacy, calls = [], [], {}
        message_keys = set()
        try:
            with path.open(encoding='utf-8') as stream:
                for number, line in enumerate(stream, 1):
                    source = {'path': str(path), 'line': number}
                    try:
                        entry = json.loads(line)
                        if (number == 1 and isinstance(entry, dict) and 'type' not in entry
                                and {'id', 'timestamp', 'instructions'} <= entry.keys()):
                            issues.append({'code': 'unsupported_session_format', **source})
                            break
                        if not isinstance(entry, dict) or not isinstance(entry.get('payload'), dict):
                            raise ValueError('expected an event object')
                    except ValueError:
                        issues.append({'code': 'invalid_json', **source})
                        continue
                    payload = entry['payload']
                    entry_type, kind = entry.get('type'), payload.get('type')
                    if entry_type == 'session_meta':
                        cwd = payload.get('cwd')
                        session_id = payload.get('id') or payload.get('session_id')
                        origin = payload.get('source')
                        subagent = (isinstance(origin, dict) and 'subagent' in origin) or origin == 'subagent'
                        if subagent:
                            break
                        continue
                    if entry_type == 'turn_context':
                        cwd = payload.get('cwd', cwd)
                        continue
                    text, record_kind, tool, call_id = '', None, None, payload.get('call_id')
                    malformed = False
                    if entry_type == 'response_item':
                        if kind == 'message' and payload.get('role') in ('user', 'assistant'):
                            if payload.get('channel') == 'analysis' or payload.get('phase') == 'analysis':
                                continue
                            record_kind = payload['role']
                            text, malformed = message_text(payload.get('content'), record_kind == 'user')
                        elif kind in ('function_call', 'custom_tool_call'):
                            tool = payload.get('name')
                            if call_id:
                                calls[call_id] = tool
                            record_kind = 'tool_call'
                            text = plain_text(payload.get('arguments', payload.get('input')))
                        elif kind in ('function_call_output', 'custom_tool_call_output'):
                            record_kind = 'tool_result'
                            tool = calls.get(call_id)
                            text = plain_text(payload.get('output'))
                    elif entry_type == 'event_msg' and kind in ('user_message', 'agent_message'):
                        record_kind = 'user' if kind == 'user_message' else 'assistant'
                        text = plain_text(payload.get('message'))
                        if record_kind == 'user' and injected_context(text):
                            continue
                    if record_kind is None or (not text and not malformed):
                        continue
                    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                        issues.append({'code': 'workspace_unknown', **source})
                        continue
                    normalized_cwd = normalized_path(cwd)
                    if not any(normalized_cwd.is_relative_to(root) for root in roots):
                        continue
                    try:
                        timestamp = aware_time(entry.get('timestamp', ''))
                    except (ValueError, TypeError, AttributeError):
                        issues.append({'code': 'invalid_timestamp', **source})
                        continue
                    if not start <= timestamp < end:
                        continue
                    if malformed:
                        issues.append({'code': 'invalid_message_content', **source})
                    if not text:
                        continue
                    record = dict(timestamp=timestamp.astimezone(start.tzinfo).isoformat(),
                                  kind=record_kind, text=text[:max_chars],
                                  truncated=len(text) > max_chars, session_id=session_id,
                                  cwd=cwd, source=source)
                    if call_id:
                        record.update(call_id=call_id, tool=tool)
                    key = (timestamp, record_kind, hashlib.sha256(text.encode('utf-8')).digest())
                    if entry_type == 'event_msg':
                        legacy.append((key, record))
                    else:
                        current.append(record)
                        if record_kind in ('user', 'assistant'):
                            message_keys.add(key)
        except (OSError, UnicodeError) as error:
            issues.append({'code': 'unreadable_file', 'path': str(path), 'reason': type(error).__name__})
        if not subagent:
            records.extend(current)
            records.extend(record for key, record in legacy if key not in message_keys)
    records.sort(key=lambda r: (aware_time(r['timestamp']), r['source']['path'], r['source']['line']))
    page = records[offset:offset + limit]
    next_offset = offset + limit if offset + limit < len(records) else None
    partial = bool(issues or next_offset is not None or any(r['truncated'] for r in page))
    return dict(status='unavailable' if not available else ('partial' if partial else 'read'),
                range={'start': start.isoformat(), 'end': end.isoformat()},
                coverage='local_records_only', files_scanned=len(files), total_records=len(records),
                records=page, issues=issues, next_offset=next_offset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, help='inclusive ISO timestamp with timezone')
    parser.add_argument('--end', required=True, help='exclusive ISO timestamp with timezone')
    parser.add_argument('--workspace-root', action='append', required=True,
                        help='absolute company workspace path; repeat for multiple roots')
    parser.add_argument('--codex-home', type=Path,
                        default=Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex'))
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--max-chars', type=int, default=2000)
    args = parser.parse_args()
    try:
        start, end = aware_time(args.start), aware_time(args.end)
        if start >= end:
            raise ValueError('end must be after start')
        if args.offset < 0 or args.limit < 1 or args.max_chars < 1:
            raise ValueError('offset must be nonnegative; limit and max-chars must be positive')
        if any(not Path(root).expanduser().is_absolute() for root in args.workspace_root):
            raise ValueError('workspace roots must be absolute paths')
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    result = read_day(args.codex_home.expanduser().resolve(), args.workspace_root,
                      start, end, args.offset, args.limit, args.max_chars)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
