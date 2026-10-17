import copy
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from support import batch, record, event, runtime, again


class StoreTests(unittest.TestCase):
    def setUp(self):
        try:
            self.store = importlib.import_module('scripts.activity_collection.store')
        except ModuleNotFoundError:
            self.fail('日別JSONの保存処理がまだ存在しない')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = runtime(self.temp.name)

    def records(self, day='2026-10-16'):
        return self.store.read_day(self.root, day)['records']

    def test_reimport_and_reread_do_not_duplicate_posts(self):
        b = batch()
        self.store.import_batch(self.root, b)
        self.store.import_batch(self.root, b)
        self.store.import_batch(self.root, again(b))
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]['data']['text'], '架空の投稿')

    def test_reusing_batch_id_with_different_content_is_rejected(self):
        b = batch()
        self.store.import_batch(self.root, b)
        b['records'][0]['data']['text'] = '別の内容'
        with self.assertRaises(ValueError):
            self.store.import_batch(self.root, b)
        self.assertEqual(self.records()[0]['data']['text'], '架空の投稿')

    def test_unknown_repost_stays_on_first_observation_day(self):
        b = batch([record('repost', time=event(None, 'unknown'), data={'author_id': 'someone-else', 'text': '架空の共有元'})])
        self.store.import_batch(self.root, b)
        self.store.import_batch(self.root, again(b, '2026-10-18T07:00:00+09:00'))
        first = self.records('2026-10-17')
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['first_observed_at'], '2026-10-17T07:00:00+09:00')
        self.assertIsNone(first[0]['event_time']['value'])
        self.assertEqual(self.records('2026-10-18'), [])

    def test_partial_and_failed_do_not_erase_saved_records(self):
        self.store.import_batch(self.root, batch())
        partial = batch([record(external_id='456')], status='partial')
        self.store.import_batch(self.root, partial)
        self.store.import_batch(self.root, batch([], status='failed'))
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.store.status(self.root)['targets'][0]['last_attempt']['status'], 'failed')

    def test_empty_success_is_distinct_from_missing_data(self):
        self.store.import_batch(self.root, batch([]))
        target = self.store.status(self.root)['targets'][0]
        self.assertEqual(target['last_attempt']['status'], 'success')
        self.assertEqual(target['covered_ranges'][0]['end'], '2026-10-17T00:00:00+09:00')
        self.assertEqual(self.records(), [])

    def test_invalid_input_does_not_partially_save_a_batch(self):
        b = batch([record(), record(external_id='456')])
        b['records'][1]['event_time'] = event('not-a-date')
        with self.assertRaises(ValueError):
            self.store.import_batch(self.root, b)
        self.assertEqual(self.records(), [])

    def test_outside_coverage_and_false_success_are_rejected(self):
        for mutate in ('outside', 'gap'):
            b = batch()
            if mutate == 'outside':
                b['coverage'][0]['start'] = '2026-10-15T00:00:00+09:00'
            else:
                b['coverage'][0]['end'] = '2026-10-16T12:00:00+09:00'
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.store.import_batch(self.root, b)

    def test_account_escape_and_unknown_source_are_rejected(self):
        for key, value in [('account', '../outside'), ('source', 'youtube-music'), ('schema_version', True)]:
            b = batch()
            b[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.import_batch(self.root, b)

    def test_symlink_inside_storage_is_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (self.root / 'records').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.import_batch(self.root, batch())
        self.assertEqual(list(outside.iterdir()), [])

    def test_lock_contention_does_not_write(self):
        import fcntl
        with (self.root / 'collection.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(self.store.BusyError):
                self.store.import_batch(self.root, batch())
        self.assertEqual(self.records(), [])

    def test_crash_after_record_write_recovers_before_reading(self):
        replace = os.replace
        def crash(src, dst):
            if '/state/targets/' in str(dst):
                raise OSError('synthetic power loss')
            return replace(src, dst)
        with patch('os.replace', side_effect=crash), self.assertRaises(OSError):
            self.store.import_batch(self.root, batch())
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.store.status(self.root)['targets'][0]['last_attempt']['status'], 'success')
        self.assertEqual(list((self.root / 'pending').glob('*.json')), [])

    def test_corrupted_progress_is_not_replaced_with_empty_success(self):
        self.store.import_batch(self.root, batch())
        state = next((self.root / 'state/targets').rglob('*.json'))
        state.write_text('{broken')
        with self.assertRaises((ValueError, self.store.RecoveryError)):
            self.store.import_batch(self.root, again(batch()))
        self.assertEqual(state.read_text(), '{broken')

    def test_tampered_pending_is_not_silently_discarded(self):
        replace = os.replace
        def crash(src, dst):
            if '/state/targets/' in str(dst):
                raise OSError('synthetic power loss')
            return replace(src, dst)
        with patch('os.replace', side_effect=crash), self.assertRaises(OSError):
            self.store.import_batch(self.root, batch())
        pending = next((self.root / 'pending').glob('*.json'))
        pending.write_text('{}')
        with self.assertRaises(self.store.RecoveryError):
            self.store.read_day(self.root, '2026-10-16')
        self.assertTrue(pending.exists())

    def test_notifications_only_on_new_problem_or_recurrence(self):
        b = batch([], status='login_required')
        first = self.store.import_batch(self.root, b)
        repeated = self.store.import_batch(self.root, again(b))
        self.assertEqual(len(first['notifications']), 1)
        self.assertEqual(repeated['notifications'], [])
        self.store.import_batch(self.root, batch([]))
        self.assertEqual(len(self.store.import_batch(self.root, again(b))['notifications']), 1)

    def test_status_never_returns_post_content(self):
        self.store.import_batch(self.root, batch())
        encoded = json.dumps(self.store.status(self.root), ensure_ascii=False)
        self.assertNotIn('架空の投稿', encoded)
        self.assertNotIn('fictional-owner', encoded)
