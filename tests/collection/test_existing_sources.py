import importlib
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from support import CONFIG, batch, record, event, runtime, again
from scripts.activity_collection.records import validate_batch
from scripts.activity_collection.store import import_batch, read_day


class ExistingSourcesTests(unittest.TestCase):
    def test_late_conversation_output_enriches_same_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = runtime(tmp)
            b = batch([record('conversation', 'ob-1', data={'user_id':'fictional-owner','observation_id':'ob-1','input':'架空の本人発言','output':None})], source='langfuse')
            import_batch(root, b)
            later = again(b)
            later['records'][0]['data']['output'] = '架空のAI返答'
            import_batch(root, later)
            r = read_day(root, '2026-10-16')['records']
            self.assertEqual(len(r), 1)
            self.assertEqual(r[0]['data']['output'], '架空のAI返答')
            later['records'][0]['data']['user_id'] = 'another-person'
            with self.assertRaises(ValueError):
                validate_batch(later, CONFIG)

    def test_pc_records_require_stable_event_evidence(self):
        b = batch([record('activity', 'segment:1', data={'application':'Browser','action':'displayed'})], source='computer-history')
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)
        b['records'][0]['data'].update(segment_id='segment', line=1)
        self.assertEqual(validate_batch(b, CONFIG)['records'][0]['data']['action'], 'displayed')

    def test_verified_export_splits_midnight_and_rejects_changed_files(self):
        try:
            sources = importlib.import_module('scripts.activity_collection.sources')
        except ModuleNotFoundError:
            self.fail('Screen Timeの世代検証・集計処理がまだ存在しない')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            snapshot = root / ('snapshots/' + 'a'*32)
            snapshot.mkdir(parents=True)
            rows = [{'start':'2026-10-16T23:50:00+09:00','end':'2026-10-17T00:10:00+09:00','device_id':'device-a','bundle_id':'example.app'}]
            (snapshot/'sessions.json').write_text(json.dumps(rows))
            (snapshot/'sessions.csv').write_text('synthetic csv')
            hashes = {n:hashlib.sha256((snapshot/n).read_bytes()).hexdigest() for n in ('sessions.json','sessions.csv')}
            manifest = {'generation':'snapshots/'+'a'*32, 'timezone':'Asia/Tokyo', 'sha256':hashes}
            (root/'current.json').write_text(json.dumps(manifest))
            records = sources.read_screentime_export(root, '2026-10-16T00:00:00+09:00', '2026-10-18T00:00:00+09:00')
            self.assertEqual([(r['event_time']['value'],r['data']['duration_seconds']) for r in records], [('2026-10-16',600),('2026-10-17',600)])
            (snapshot/'sessions.json').write_text('[]')
            with self.assertRaises(ValueError):
                sources.read_screentime_export(root, '2026-10-16T00:00:00+09:00', '2026-10-18T00:00:00+09:00')

    def test_repeated_page_cursor_remains_partial(self):
        try:
            sources = importlib.import_module('scripts.activity_collection.sources')
        except ModuleNotFoundError:
            self.fail('Langfuseのページ検証処理がまだ存在しない')
        result = sources.read_observation_pages(lambda cursor: {'data':[{'id':'ob-1','userId':'fictional-owner'}], 'meta':{'cursor':'same'}}, 'fictional-owner', max_pages=5)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['observations']), 1)

    def test_another_users_observations_are_not_accepted(self):
        try:
            sources = importlib.import_module('scripts.activity_collection.sources')
        except ModuleNotFoundError:
            self.fail('Langfuseの本人検証処理がまだ存在しない')
        with self.assertRaises(ValueError):
            sources.read_observation_pages(lambda cursor: {'data':[{'id':'ob-2','userId':'other'}], 'meta':{}}, 'fictional-owner')
