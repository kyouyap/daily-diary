import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from support import batch, runtime


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = runtime(self.temp.name)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'scripts.activity_collection.cli', '--root', str(self.root), *args], capture_output=True, text=True)

    def test_import_status_and_day_read_have_distinct_disclosure(self):
        path = self.root / 'input.json'
        path.write_text(json.dumps(batch()))
        result = self.cli('import', '--input', str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli('status')
        self.assertNotIn('架空の投稿', result.stdout)
        result = self.cli('read-day', '--date', '2026-10-16')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['records'][0]['data']['text'], '架空の投稿')

    def test_bad_input_has_safe_error_and_nonzero_exit(self):
        path = self.root / 'input.json'
        path.write_text('{personal secret')
        result = self.cli('import', '--input', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('personal secret', result.stderr + result.stdout)

    def test_planning_does_not_create_records_or_touch_services(self):
        result = self.cli('plan', '--now', '2026-10-17T07:00:00+09:00')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['requests'][0]['range']['start'], '2026-10-14T00:00:00+09:00')
        self.assertFalse((self.root / 'records').exists())
