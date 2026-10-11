import tempfile
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from daily_diary.core import (DayState, DiaryError, approve, body_hash, canonical_body,
                              day_lock, jst_window, load_state, safe_path, write_state)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / '.daily-diary-runtime'
        self.day = date(2026, 10, 9)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jst_window(self):
        a, b = jst_window(self.day)
        self.assertEqual(a.isoformat(), '2026-10-08T15:00:00+00:00')
        self.assertEqual(b.isoformat(), '2026-10-09T15:00:00+00:00')

    def test_incomplete_or_wrongly_typed_state_stops_without_rewrite(self):
        write_state(self.root, DayState(day=self.day))
        p = safe_path(self.root, 'state/2026-10-09.json')
        valid = json.loads(p.read_bytes())
        for raw in ({'day': str(self.day)}, dict(valid, question_count=False),
                    dict(valid, used_ids='reset'), dict(valid, delivery='unexpected')):
            payload = json.dumps(raw).encode()
            p.write_bytes(payload)
            with self.assertRaises(DiaryError):
                load_state(self.root, self.day)
            self.assertEqual(p.read_bytes(), payload)

    def test_approval_binds_bytes(self):
        body = canonical_body('本文\r\n')
        self.assertEqual(body, '本文\n'.encode())
        state = DayState(day=self.day, status='確認待ち', body_sha256=body_hash(body),
                         presentation_id='review-1', delivery='confirmed')
        approve(state, body, 'human-reply', datetime(2026, 10, 9, 16, tzinfo=timezone.utc))
        self.assertEqual(state.day, self.day)
        self.assertEqual(state.approval.body_sha256, body_hash(body))
        with self.assertRaises(DiaryError):
            approve(state, canonical_body('変更'), 'reply-2', datetime.now(timezone.utc))

    def test_state_atomic_and_confined(self):
        state = DayState(day=self.day)
        write_state(self.root, state)
        self.assertEqual(load_state(self.root, self.day), state)
        p = safe_path(self.root, 'state/2026-10-09.json')
        p.write_text('{broken')
        with self.assertRaises(DiaryError):
            load_state(self.root, self.day)
        with self.assertRaises(DiaryError):
            safe_path(self.root, '../outside')
        outside = Path(self.tmp.name) / 'outside'
        outside.write_text('keep')
        p.unlink()
        p.symlink_to(outside)
        with self.assertRaises(DiaryError):
            write_state(self.root, state)
        self.assertEqual(outside.read_text(), 'keep')

    def test_root_symlink_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DiaryError):
            write_state(self.root, DayState(day=self.day))

    def test_lock_and_resume(self):
        state = DayState(day=self.day, question_count=2)
        with day_lock(self.root, self.day):
            with self.assertRaises(DiaryError):
                with day_lock(self.root, self.day):
                    self.fail('double lock')
            write_state(self.root, state)
        self.assertEqual(load_state(self.root, self.day).question_count, 2)
        with day_lock(self.root, self.day):
            pass

    def test_approval_requires_confirmed_current_review(self):
        body = canonical_body('x')
        state = DayState(day=self.day, status='確認待ち', body_sha256=body_hash(body),
                         presentation_id='r', delivery='intent')
        with self.assertRaises(DiaryError):
            approve(state, body, 'reply', datetime.now(timezone.utc))

    def test_failed_state_write_keeps_previous(self):
        state = DayState(day=self.day)
        write_state(self.root, state)
        state.question_count = 3
        with self.assertRaises(DiaryError):
            write_state(self.root, state)
        self.assertEqual(load_state(self.root, self.day).question_count, 0)
