"""Read real JSONL files with fictional content through the shipped CLI."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'skills/work-diary/scripts/read_codex_day.py'
START = '2026-11-03T00:00:00+09:00'
END = '2026-11-04T00:00:00+09:00'


def event(timestamp, kind, payload):
    return dict(timestamp=timestamp, type=kind, payload=payload)


def message(timestamp, text, role='user', **extra):
    return event(timestamp, 'response_item', dict(
        type='message', role=role,
        content=[dict(type='input_text' if role == 'user' else 'output_text', text=text)],
        **extra))


class WorkDiaryCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.company = self.root / 'company'
        self.company.mkdir()
        self.home = self.root / 'codex'

    def session(self, name, rows, cwd=None, source='cli'):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = event('2026-11-01T00:00:00Z', 'session_meta', dict(
            id=name, cwd=str(cwd or self.company), source=source))
        lines = [json.dumps(metadata)]
        lines.extend(row if isinstance(row, str) else json.dumps(row) for row in rows)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def invoke(self, *extra):
        result = subprocess.run([
            sys.executable, '-B', str(SCRIPT), '--start', START, '--end', END,
            '--codex-home', str(self.home), '--workspace-root', str(self.company), *extra,
        ], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_resumed_and_archived_sessions_use_jst_event_time(self):
        old = self.session('sessions/2026/11/01/old.jsonl', [
            message('2026-11-02T14:59:59Z', 'yesterday'),
            message('2026-11-02T15:00:00Z', 'start'),
            message('2026-11-03T14:59:59Z', 'last'),
            message('2026-11-03T15:00:00Z', 'tomorrow'),
        ])
        self.session('archived_sessions/archived.jsonl', [
            message('2026-11-03T01:00:00Z', 'archived'),
        ])
        result = self.invoke()
        self.assertEqual([r['text'] for r in result['records']], ['start', 'archived', 'last'])
        self.assertEqual(result['records'][0]['source'], dict(path=str(old), line=3))
        self.assertEqual(result['records'][0]['timestamp'], START)
        self.assertEqual(result['status'], 'read')

    def test_scope_is_a_path_boundary_and_follows_turn_context(self):
        self.session('sessions/work.jsonl', [
            message('2026-11-03T01:00:00Z', 'work'),
            event('2026-11-03T01:01:00Z', 'turn_context', {'cwd': str(self.root/'private')}),
            message('2026-11-03T01:02:00Z', 'private after moving'),
        ])
        self.session('sessions/prefix.jsonl', [
            message('2026-11-03T02:00:00Z', 'private prefix'),
        ], cwd=self.root/'company-private')
        self.session('sessions/moved.jsonl', [
            event('2026-11-03T03:00:00Z', 'turn_context', {'cwd': str(self.company/'sdk')}),
            message('2026-11-03T03:01:00Z', 'moved into work'),
        ], cwd=self.root/'private')
        self.assertEqual([r['text'] for r in self.invoke()['records']], ['work', 'moved into work'])

    def test_messages_and_tool_results_are_distinct_without_echoes_or_reasoning(self):
        self.session('sessions/mixed.jsonl', [
            message('2026-11-03T01:00:00Z', '調査して', id='user-1'),
            event('2026-11-03T01:00:00Z', 'event_msg', {'type':'user_message','message':'調査して'}),
            message('2026-11-03T01:01:00Z', '修正完了', role='assistant'),
            message('2026-11-03T01:02:00Z', 'internal thought', role='assistant', channel='analysis'),
            message('2026-11-03T01:03:00Z', 'system instruction', role='developer'),
            event('2026-11-03T01:04:00Z', 'response_item', dict(
                type='function_call', name='exec_command', call_id='call-1', arguments='pytest')),
            event('2026-11-03T01:05:00Z', 'response_item', dict(
                type='function_call_output', call_id='call-1', output='1 failed')),
        ])
        records = self.invoke()['records']
        self.assertEqual([r['kind'] for r in records], ['user','assistant','tool_call','tool_result'])
        self.assertEqual(records[-1]['text'], '1 failed')
        self.assertEqual(records[-1]['tool'], 'exec_command')

    def test_legacy_messages_work_but_subagent_prompts_are_not_user_actions(self):
        self.session('sessions/legacy.jsonl', [
            event('2026-11-03T01:00:00Z','event_msg',dict(type='user_message',message='review')),
            event('2026-11-03T01:01:00Z','event_msg',dict(type='agent_message',message='reviewed')),
        ])
        self.session('sessions/child.jsonl', [message('2026-11-03T02:00:00Z','generated child prompt')],
                     source={'subagent':{'spawn':{'parent_thread_id':'parent'}}})
        self.assertEqual([r['text'] for r in self.invoke()['records']], ['review','reviewed'])

    def test_corruption_and_unzoned_time_keep_good_records_and_report_partial(self):
        self.session('sessions/broken.jsonl', [
            '{broken',
            message('2026-11-03T01:00:00', 'unknown timezone'),
            message('2026-11-03T02:00:00Z', 'good'),
        ])
        result = self.invoke()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([r['text'] for r in result['records']], ['good'])
        self.assertEqual({i['code'] for i in result['issues']}, {'invalid_json','invalid_timestamp'})

    def test_pagination_and_text_truncation_are_visible(self):
        self.session('sessions/pages.jsonl', [
            message('2026-11-03T01:00:00Z','first'),
            message('2026-11-03T02:00:00Z','second'),
            message('2026-11-03T03:00:00Z','third'),
        ])
        first = self.invoke('--limit','2','--max-chars','3')
        self.assertEqual(first['next_offset'], 2)
        self.assertEqual(first['total_records'], 3)
        self.assertEqual(first['records'][0]['text'], 'fir')
        self.assertTrue(first['records'][0]['truncated'])
        self.assertEqual(first['status'], 'partial')
        last = self.invoke('--offset','2','--limit','2')
        self.assertEqual([r['text'] for r in last['records']], ['third'])
        self.assertIsNone(last['next_offset'])

    def test_missing_transcripts_differs_from_no_matching_messages(self):
        self.assertEqual(self.invoke()['status'], 'unavailable')
        self.session('sessions/empty.jsonl', [message('2026-11-01T00:00:00Z','old')])
        result = self.invoke()
        self.assertEqual(result['status'], 'read')
        self.assertEqual(result['records'], [])

    def test_unsupported_old_format_is_one_explicit_gap_not_many_bad_json_rows(self):
        path = self.home / 'sessions/old-format.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('\n'.join(json.dumps(row) for row in [
            {'id':'old','timestamp':'2026-10-01T00:00:00Z','instructions':'context'},
            {'type':'message','role':'user','content':[]},
            {'type':'reasoning','summary':[]},
        ])+'\n')
        result = self.invoke()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['records'], [])
        self.assertEqual([i['code'] for i in result['issues']], ['unsupported_session_format'])

    def test_until_bound_is_exclusive_and_reader_does_not_write(self):
        path = self.session('sessions/readonly.jsonl', [
            message('2026-11-03T01:00:00Z','before'),
            message('2026-11-03T02:00:00Z','at end'),
        ])
        original = path.read_bytes()
        files = set(self.home.rglob('*'))
        result = self.invoke('--end','2026-11-03T11:00:00+09:00')
        self.assertEqual([r['text'] for r in result['records']], ['before'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(set(self.home.rglob('*')), files)

    def test_invalid_cli_input_is_an_error(self):
        for extra in [('--limit','0'), ('--offset','-1'), ('--start','2026-11-03T00:00:00'),
                      ('--workspace-root','relative/path')]:
            with self.subTest(extra=extra):
                p = subprocess.run([sys.executable,'-B',str(SCRIPT),'--start',START,'--end',END,
                    '--workspace-root',str(self.company),*extra],text=True,capture_output=True)
                self.assertEqual(p.returncode, 2)
                self.assertIn('error:', p.stderr)


if __name__ == '__main__':
    unittest.main()
