"""Run collectors against private temporary sources, never the user's accounts."""
import contextlib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from support import runtime
from scripts.activity_collection.store import read_day


REPOSITORY = Path(__file__).resolve().parents[2]
START = '2026-10-16T00:00:00+09:00'
END = '2026-10-17T00:00:00+09:00'


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = runtime(self.base)
        self.settings = self.base / 'settings' / 'collectors.json'
        self.settings.parent.mkdir()

    def configure(self, source, **options):
        self.settings.write_text(json.dumps({'schema_version': 1, 'collectors': {
            source: {'account': 'self', **options}}}))

    def run_collector(self, source, *extra, dates=True):
        module = 'scripts.activity_collection.collect_' + source.replace('-', '_')
        command = [sys.executable, '-B', '-m', module, '--root', str(self.root),
                   '--settings', str(self.settings)]
        if dates:
            command += ['--start', START, '--end', END]
        result = subprocess.run(command + list(extra), cwd=REPOSITORY, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.stderr, '', result.stderr)
        try:
            output = json.loads(result.stdout)
        except ValueError:
            self.fail('collector must emit one JSON result: ' + result.stdout)
        return result.returncode, output

    def export(self):
        root = self.settings.parent / 'exports'
        snap = root / 'snapshots' / ('a' * 32)
        snap.mkdir(parents=True)
        (snap / 'sessions.json').write_text(json.dumps([
            {'start': '2026-10-16T23:50:00+09:00', 'end': END,
             'device_id': 'test-device', 'bundle_id': 'example.app'}]))
        (snap / 'sessions.csv').write_text('synthetic csv')
        (root / 'current.json').write_text(json.dumps({
            'generation': 'snapshots/' + 'a' * 32, 'timezone': 'Asia/Tokyo',
            'exported_at_utc': '2026-10-17T01:00:00Z',
            'sha256': {n: hashlib.sha256((snap / n).read_bytes()).hexdigest()
                       for n in ('sessions.json', 'sessions.csv')}}))
        return root

    def history(self, lines):
        root = self.settings.parent / 'history'
        segment = root / 'segments' / 'synthetic-segment'
        segment.mkdir(parents=True)
        (segment / 'metadata.json').write_text(json.dumps({
            'id': 'synthetic-segment', 'startedAt': '2026-10-15T15:00:00Z',
            'endedAt': '2026-10-16T15:00:00Z'}))
        (segment / 'events.jsonl').write_text('\n'.join(
            json.dumps(x) if isinstance(x, dict) else x for x in lines) + '\n')
        return root

    def pc_event(self, **overrides):
        return {'id': 1, 'timestamp': '2026-10-16T03:00:00Z',
                'kind': 'window.changed', 'app': {'name': 'Editor'},
                'window': {'title': 'PRIVATE_TITLE'},
                'ax': {'text': 'PRIVATE_AX'}, **overrides}

    def test_screentime_relative_source_survives_relocation_and_reimport(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        # A fixed working directory must not determine source resolution.
        moved = self.base / 'moved settings'
        self.settings.parent.rename(moved)
        self.settings = moved / 'collectors.json'
        code, result = self.run_collector('screentime')
        self.assertEqual(code, 1)  # Saved, but Apple source completeness is unknown.
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['save_status'], 'saved')
        self.assertEqual(result['records_read'], 1)
        doc = read_day(self.root, '2026-10-16')
        self.assertEqual(doc['records'][0]['data']['duration_seconds'], 600)
        self.assertEqual(doc['targets'][-1]['covered_ranges'], [])
        _, result = self.run_collector('screentime')
        self.assertEqual(result['changed_records'], 0)

    def test_check_and_missing_configuration_do_not_create_records(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        code, result = self.run_collector('screentime', '--check', dates=False)
        self.assertEqual((code, result['status']), (0, 'ready'))
        self.assertFalse((self.root / 'state').exists())
        self.assertFalse((self.root / 'batches').exists())
        self.settings.unlink()
        code, result = self.run_collector('screentime', '--check', dates=False)
        self.assertEqual(code, 2)
        self.assertIn('settings_missing', result['issues'])

    def test_invalid_range_and_unknown_setting_fail_before_saving(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        code, _ = self.run_collector('screentime', '--start', END, '--end', START)
        self.assertEqual(code, 2)
        self.configure('screentime', identity='fictional-owner', export_root='exports', export_rooot='typo')
        code, result = self.run_collector('screentime')
        self.assertEqual(code, 2)
        self.assertIn('invalid_settings', result['issues'])
        self.assertFalse((self.root / 'records').exists())

    def test_parent_relative_export_path_is_resolved_from_settings(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='../settings/exports')
        code, result = self.run_collector('screentime')
        self.assertEqual((code, result['status'], result['records_read']), (1, 'partial', 1))

    def test_screentime_bad_hash_is_failure_instead_of_empty_success(self):
        root = self.export()
        (root / 'snapshots' / ('a' * 32) / 'sessions.json').write_text('[]')
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        code, result = self.run_collector('screentime')
        self.assertEqual((code, result['status'], result['records_read']), (1, 'failed', 0))
        self.assertEqual(result['save_status'], 'saved')

    def test_pc_filters_dates_and_drops_ax_and_titles_by_default(self):
        self.history([self.pc_event(timestamp='2026-10-15T14:59:59Z'),
                      self.pc_event(), self.pc_event(timestamp='2026-10-16T15:00:00Z')])
        self.configure('computer-history', identity='fictional-owner', event_stream_root='history')
        code, result = self.run_collector('computer-history')
        self.assertEqual((code, result['status'], result['records_read']), (1, 'partial', 1))
        record = read_day(self.root, '2026-10-16')['records'][0]
        self.assertEqual(record['data']['application'], 'Editor')
        self.assertEqual(record['data']['action'], 'window.changed')
        self.assertEqual(record['data']['line'], 2)
        self.assertNotIn('PRIVATE_', json.dumps(record))
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_pc_preserves_readable_rows_with_malformed_input_and_limit(self):
        self.history([self.pc_event(), 'not-json', self.pc_event(id=2)])
        self.configure('computer-history', identity='fictional-owner', event_stream_root='history', max_events=2)
        _, result = self.run_collector('computer-history')
        self.assertEqual(result['records_read'], 1)
        self.assertIn('invalid_event', result['issues'])
        self.assertIn('event_limit', result['issues'])

    def test_pc_missing_source_does_not_save_an_unverified_identity(self):
        self.configure('computer-history', identity='fictional-owner', event_stream_root='missing')
        code, result = self.run_collector('computer-history')
        self.assertEqual(code, 2)
        self.assertEqual(result['save_status'], 'not_requested')
        self.assertIn('history_unavailable', result['issues'])
        self.assertFalse((self.root / 'state').exists())

    def test_wrong_local_identity_is_rejected_before_import(self):
        self.export()
        self.configure('screentime', identity='other-owner', export_root='exports')
        code, result = self.run_collector('screentime')
        self.assertEqual(code, 2)
        self.assertIn('identity_mismatch', result['issues'])
        self.assertFalse((self.root / 'state').exists())

    @contextlib.contextmanager
    def langfuse_server(self, mode='success'):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                from urllib.parse import parse_qs, urlsplit
                query = parse_qs(urlsplit(handler.path).query)
                requests.append((handler.path, handler.headers.get('Authorization')))
                if mode == 'login':
                    handler.send_response(401)
                    handler.end_headers()
                    handler.wfile.write(b'PRIVATE_SERVER_ERROR')
                    return
                if mode == 'redirect':
                    handler.send_response(302)
                    handler.send_header('Location', '/forbidden-redirect')
                    handler.end_headers()
                    return
                if mode == 'second_page_error' and 'cursor' in query:
                    handler.send_response(500)
                    handler.end_headers()
                    return
                first = 'cursor' not in query
                row = {'id': 'ob-1' if first else 'ob-2', 'userId': 'fictional-owner',
                       'name': 'Codex Turn', 'type': 'AGENT',
                       'startTime': '2026-10-16T03:00:00Z', 'traceId': 'trace-1',
                       'sessionId': 'session-1', 'input': 'PRIVATE_INPUT secret-test',
                       'output': {'text': 'PRIVATE_OUTPUT'}}
                if mode == 'wrong_owner':
                    row['userId'] = 'someone-else'
                payload = ({'data': [], 'meta': {'cursor': None}} if mode == 'empty' else
                           {'data': [row], 'meta': {'cursor': 'next' if first or mode == 'loop' else None}})
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credentials = self.settings.parent / 'credentials.json'
            credentials.write_text(json.dumps({'base_url': f'http://127.0.0.1:{server.server_port}',
                                               'public_key': 'public-test', 'secret_key': 'secret-test',
                                               'user_id': 'fictional-owner'}))
            credentials.chmod(0o600)
            self.configure('langfuse', credentials_file='credentials.json')
            yield requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_langfuse_fetches_all_pages_and_keeps_bodies_out_of_stdout(self):
        with self.langfuse_server() as requests:
            code, result = self.run_collector('langfuse')
        self.assertEqual((code, result['status'], result['records_read']), (0, 'success', 2))
        self.assertEqual(len(requests), 2)
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(requests[0][0]).query)
        self.assertEqual(query['fromStartTime'], ['2026-10-15T15:00:00Z'])
        self.assertEqual(query['toStartTime'], ['2026-10-16T15:00:00Z'])
        self.assertEqual(query['userId'], ['fictional-owner'])
        self.assertEqual(query['fields'], ['core,basic,io'])
        self.assertTrue(requests[0][1].startswith('Basic '))
        self.assertNotIn('PRIVATE_', json.dumps(result))
        stored = read_day(self.root, '2026-10-16')['records']
        self.assertEqual(len(stored), 2)
        self.assertIn('PRIVATE_INPUT', stored[0]['data']['input'])
        self.assertNotIn('secret-test', json.dumps(stored))

    def test_langfuse_second_page_failure_keeps_first_page_without_coverage(self):
        with self.langfuse_server('second_page_error'):
            _, result = self.run_collector('langfuse')
        self.assertEqual((result['status'], result['records_read']), ('partial', 1))
        target = next(t for t in read_day(self.root, '2026-10-16')['targets'] if t['source'] == 'langfuse')
        self.assertEqual(target['covered_ranges'], [])

    def test_langfuse_login_failure_is_saved_without_server_body(self):
        with self.langfuse_server('login'):
            code, result = self.run_collector('langfuse')
        self.assertEqual((code, result['status']), (1, 'login_required'))
        self.assertNotIn('PRIVATE_SERVER_ERROR', json.dumps(result))

    def test_langfuse_rejects_another_users_response(self):
        with self.langfuse_server('wrong_owner'):
            _, result = self.run_collector('langfuse')
        self.assertEqual((result['status'], result['records_read']), ('failed', 0))
        self.assertFalse(read_day(self.root, '2026-10-16')['records'])

    def test_langfuse_check_rejects_public_credentials_without_network(self):
        with self.langfuse_server() as requests:
            (self.settings.parent / 'credentials.json').chmod(0o644)
            code, result = self.run_collector('langfuse', '--check', dates=False)
            self.assertEqual(requests, [])
        self.assertEqual(code, 2)
        self.assertIn('credentials_permissions', result['issues'])

    def test_langfuse_empty_completed_request_has_coverage(self):
        with self.langfuse_server('empty'):
            code, result = self.run_collector('langfuse')
        self.assertEqual((code, result['status'], result['records_read']), (0, 'success', 0))
        target = next(t for t in read_day(self.root, '2026-10-16')['targets'] if t['source'] == 'langfuse')
        self.assertEqual(target['covered_ranges'], [{'start': START, 'end': END}])

    def test_langfuse_redirect_is_not_followed(self):
        with self.langfuse_server('redirect') as requests:
            _, result = self.run_collector('langfuse')
            self.assertEqual(len(requests), 1)
        self.assertEqual(result['status'], 'failed')

    def test_langfuse_cursor_loop_and_page_limit_are_partial(self):
        with self.langfuse_server('loop'):
            _, result = self.run_collector('langfuse')
            self.assertEqual(result['status'], 'partial')
            self.assertIn('repeated_cursor', result['issues'])
        with self.langfuse_server() as requests:
            self.configure('langfuse', credentials_file='credentials.json', max_pages=1)
            _, result = self.run_collector('langfuse')
            self.assertEqual(len(requests), 1)
        self.assertIn('page_limit', result['issues'])

    def test_save_failure_retains_batch_for_retry(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        # Damaged existing state must not be replaced by an initial state.
        state = self.root / 'state/targets/screentime/self.json'
        state.parent.mkdir(parents=True)
        state.write_text('broken')
        code, result = self.run_collector('screentime')
        self.assertEqual(code, 4)
        self.assertEqual(result['save_status'], 'failed')
        batches = list((self.root / 'batches').glob('*.json'))
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.read_text(), 'broken')

    def test_batch_write_failure_reports_save_failure_without_writing_outside_runtime(self):
        self.export()
        self.configure('screentime', identity='fictional-owner', export_root='exports')
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / 'batches').symlink_to(outside, target_is_directory=True)
        code, result = self.run_collector('screentime')
        self.assertEqual((code, result['save_status']), (4, 'failed'))
        self.assertEqual(list(outside.iterdir()), [])
