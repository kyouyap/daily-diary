"""Exercise the read-only diary reference using fictional CSV files."""
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime


SCRIPT = Path(__file__).resolve().parents[2] / 'skills/daily-diary/scripts/read_location.py'
START = '2099-01-02T00:00:00+09:00'
END = '2099-01-03T00:00:00+09:00'
HEADERS = ['record_type', 'start_time', 'end_time', 'title', 'day_key',
           'transport_mode', 'latitude', 'longitude', 'comment']


class LocationReferenceTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.exists():
            self.fail('read_location.py is not implemented')
        spec = importlib.util.spec_from_file_location('diary_location', SCRIPT)
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / 'location.csv'

    def write_rows(self, rows):
        with self.csv.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
            for row in rows:
                writer.writerow([row.get(k, '') for k in HEADERS])

    def row(self, start=START, end=END, **extra):
        return dict(record_type='place', start_time=start, end_time=end,
                    title='架空の施設', day_key='1900-01-01', **extra)

    def read(self):
        return self.reader.read_location(self.csv, datetime.fromisoformat(START),
                                         datetime.fromisoformat(END))

    def test_jst_overlap_uses_timestamps_not_day_key(self):
        self.write_rows([
            self.row('2099-01-01T14:59:00Z', '2099-01-01T15:01:00Z'),
            self.row('2099-01-01T14:00:00Z', '2099-01-01T15:00:00Z'),
            self.row('2099-01-02T15:00:00Z', '2099-01-02T16:00:00Z'),
        ])
        result = self.read()
        self.assertEqual(result['status'], 'read')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['start'], '2099-01-01T23:59:00+09:00')
        self.assertEqual(result['records'][0]['end'], '2099-01-02T00:01:00+09:00')
        self.assertEqual(result['records'][0]['line'], 2)

    def test_open_and_instant_records_are_not_extended_into_other_days(self):
        self.write_rows([self.row('2099-01-01T22:00:00+09:00', ''),
                         self.row(START, ''), self.row(START, START),
                         self.row(END, END)])
        records = self.read()['records']
        self.assertEqual([r['line'] for r in records], [3, 4])
        self.assertIsNone(records[0]['end'])
        self.assertEqual(records[1]['start'], records[1]['end'])

    def test_overlapping_places_and_moves_keep_individual_evidence(self):
        self.write_rows([self.row(latitude='10', longitude='10'),
                         self.row(latitude='11', longitude='11'),
                         dict(record_type='move', start_time=START, end_time=END,
                              title='架空の移動', transport_mode='unknown')])
        result = self.read()
        self.assertEqual(result['status'], 'read')
        self.assertEqual([r['kind'] for r in result['records']], ['place', 'place', 'move'])
        self.assertEqual([r['latitude'] for r in result['records']], [10.0, 11.0, None])
        self.assertEqual([r['end'] for r in result['records']], [END, END, END])

    def test_bad_rows_and_coordinates_preserve_readable_evidence(self):
        self.write_rows([self.row('invalid', END),
                         self.row('2099-01-02T10:00:00', END),
                         self.row(END, START),
                         self.row(latitude='NaN', longitude='10'),
                         self.row(latitude='10', longitude='181'),
                         self.row(latitude='10', longitude='')])
        result = self.read()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([r['line'] for r in result['records']], [5, 6, 7])
        self.assertTrue(all(r['latitude'] is None and r['longitude'] is None
                            for r in result['records']))
        self.assertEqual({i['line'] for i in result['issues']}, {2, 3, 4, 5, 6, 7})

    def test_missing_input_differs_from_read_file_with_no_matching_rows(self):
        self.assertEqual(self.read()['status'], 'unavailable')
        self.write_rows([self.row(END, END)])
        result = self.read()
        self.assertEqual(result['status'], 'read')
        self.assertEqual(result['records'], [])
        self.csv.write_text('wrong,headers\nfictional,content\n')
        self.assertEqual(self.read()['status'], 'unavailable')

    def test_blank_and_multiline_rows_keep_physical_source_start_lines(self):
        self.csv.write_text(
            'record_type,start_time,end_time,title\n'
            '\n'
            'place,2099-01-02T01:00:00+09:00,2099-01-02T02:00:00+09:00,"架空\n施設"\n'
            '\n'
            'place,invalid,,施設\n', encoding='utf-8')
        result = self.read()
        self.assertEqual(result['records'][0]['line'], 3)
        self.assertEqual(result['records'][0]['label'], '架空\n施設')
        self.assertEqual(result['issues'][0]['line'], 6)

    def cli(self, config, start=START, end=END):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--config', str(config),
                               '--start', start, '--end', end],
                              capture_output=True, text=True, cwd=self.root)

    def test_installed_style_cli_reads_config_without_writes_or_unrelated_data(self):
        self.write_rows([self.row()])
        config = self.root / 'local.json'
        config.write_text(json.dumps({'location_csv': str(self.csv),
                                      'unrelated': 'PRIVATE_SENTINEL'}))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.cli(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['records']), 1)
        self.assertNotIn('PRIVATE_SENTINEL', result.stdout + result.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_cli_handles_unconfigured_relative_and_invalid_request(self):
        config = self.root / 'local.json'
        for value in [{}, {'location_csv': 'relative.csv'}]:
            config.write_text(json.dumps(value))
            result = self.cli(config)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')
        result = self.cli(config, start='2099-01-02T00:00:00')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'invalid_request')
        result = self.cli(config, start=END, end=START)
        self.assertEqual(result.returncode, 2)

    def test_invalid_path_returns_unavailable_json_instead_of_traceback(self):
        config = self.root / 'local.json'
        config.write_text(json.dumps({'location_csv': '/nonexistent\u0000.csv'}))
        result = self.cli(config)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
