import importlib
import json
import tempfile
import unittest
from pathlib import Path
from support import batch, runtime, again
from scripts.activity_collection.store import import_batch, read_day, status


class BackupTests(unittest.TestCase):
    def setUp(self):
        try:
            self.backup = importlib.import_module('scripts.activity_collection.backup')
        except ModuleNotFoundError:
            self.fail('Mac内バックアップ・復旧処理がまだ存在しない')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = runtime(self.temp.name)

    def test_backup_restores_records_first_observation_and_gaps(self):
        import_batch(self.root, batch(status='partial'))
        saved = self.backup.create_backup(self.root)
        destination = Path(self.temp.name).resolve() / 'restored'
        self.backup.restore_backup(Path(saved['path']), destination)
        restored = read_day(destination, '2026-10-16')['records']
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['first_observed_at'], '2026-10-17T07:00:00+09:00')
        self.assertEqual(status(destination)['targets'][0]['last_attempt']['status'], 'partial')
        self.assertEqual(status(destination)['targets'][0]['covered_ranges'], [])
        self.assertFalse((destination / 'backups').exists())

    def test_tampered_backup_is_rejected_before_destination_is_written(self):
        import_batch(self.root, batch())
        saved = self.backup.create_backup(self.root)
        directory = Path(saved['path'])
        next((directory/'records').rglob('*.json')).write_text('[]')
        dest = Path(self.temp.name).resolve() / 'restored'
        with self.assertRaises(ValueError):
            self.backup.restore_backup(directory, dest)
        self.assertFalse(dest.exists())

    def test_restore_does_not_overwrite_existing_data(self):
        import_batch(self.root, batch())
        saved = self.backup.create_backup(self.root)
        with self.assertRaises(ValueError):
            self.backup.restore_backup(Path(saved['path']), self.root)
        self.assertEqual(len(read_day(self.root, '2026-10-16')['records']), 1)

    def test_manual_backups_keep_at_most_seven_complete_generations(self):
        import_batch(self.root, batch())
        for _ in range(9):
            self.backup.create_backup(self.root)
        self.assertEqual(len(list((self.root/'backups').glob('*/manifest.json'))), 7)

    def test_automatic_backups_only_once_per_updated_observation_day(self):
        b = batch()
        import_batch(self.root, b)
        self.assertEqual(len(list((self.root/'backups').glob('*/manifest.json'))), 1)
        import_batch(self.root, again(b))
        self.assertEqual(len(list((self.root/'backups').glob('*/manifest.json'))), 1)
        import_batch(self.root, again(b, '2026-10-18T07:00:00+09:00'))
        self.assertEqual(len(list((self.root/'backups').glob('*/manifest.json'))), 2)
