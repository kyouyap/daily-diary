import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from support import batch, record, event, runtime, again
from scripts.activity_collection import store
from scripts.activity_collection.backup import create_backup, restore_backup
from scripts.activity_collection.records import validate_batch
from support import CONFIG


def usage_batch(start, end, seconds):
    b = batch([record('usage', 'usage-a', event('2026-10-16', 'day'), {'device':'a','application':'app','duration_seconds':seconds,'generation':'snapshots/'+'a'*32,'sha256':'b'*64})], source='screentime')
    b['requested_range']={'start':start,'end':end}
    b['coverage']=[{**b['requested_range'],'evidence':{'method':'synthetic','reference':'fixture'}}]
    return b


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=runtime(self.temp.name)

    def test_partial_day_screen_time_cannot_overwrite_daily_prefix(self):
        store.import_batch(self.root,usage_batch('2026-10-16T00:00:00+09:00','2026-10-16T01:00:00+09:00',3600))
        with self.assertRaises(ValueError):
            store.import_batch(self.root,usage_batch('2026-10-16T01:00:00+09:00','2026-10-17T00:00:00+09:00',3600))
        self.assertEqual(store.read_day(self.root,'2026-10-16')['records'][0]['data']['duration_seconds'],3600)

    def test_older_shorter_prefix_does_not_erase_newer_usage(self):
        store.import_batch(self.root,usage_batch('2026-10-16T00:00:00+09:00','2026-10-16T12:00:00+09:00',7200))
        store.import_batch(self.root,usage_batch('2026-10-16T00:00:00+09:00','2026-10-16T01:00:00+09:00',3600))
        self.assertEqual(store.read_day(self.root,'2026-10-16')['records'][0]['data']['duration_seconds'],7200)

    def test_screen_time_backfill_rereads_midnight_prefix_of_partial_day(self):
        from scripts.activity_collection.state import plan_collection
        state={'source':'screentime','account':'self','initial_start':'2026-09-17T00:00:00+09:00',
               'covered_ranges':[{'start':'2026-09-17T00:00:00+09:00','end':'2026-09-17T12:00:00+09:00'}]}
        request=next(p for p in plan_collection(CONFIG,[state],datetime.fromisoformat('2026-10-17T07:00:00+09:00')) if p['source']=='screentime' and p['mode']=='backfill')
        self.assertEqual(request['range']['start'],'2026-09-17T00:00:00+09:00')

    def test_missing_indexed_daily_file_never_reports_empty_success(self):
        store.import_batch(self.root,batch())
        next((self.root/'records').rglob('*.json')).unlink()
        with self.assertRaises(store.RecoveryError):
            store.read_day(self.root,'2026-10-16')
        with self.assertRaises(store.RecoveryError):
            store.status(self.root)
        with self.assertRaises(store.RecoveryError):
            create_backup(self.root)

    def test_missing_state_does_not_turn_existing_records_into_first_run(self):
        b=batch([record('repost',time=event(None,'unknown'),data={'author_id':'other','text':'共有元'})])
        store.import_batch(self.root,b)
        next((self.root/'state/targets').rglob('*.json')).unlink()
        with self.assertRaises(store.RecoveryError):
            store.import_batch(self.root,again(b,'2026-10-18T07:00:00+09:00'))

    def test_never_attempted_targets_remain_visible_as_unconfirmed(self):
        result=store.status(self.root)
        self.assertEqual(len(result['targets']),5)
        self.assertTrue(all(t['last_attempt'] is None and t['covered_ranges']==[] for t in result['targets']))
        self.assertEqual(len(store.read_day(self.root,'2026-10-16')['targets']),5)

    def test_unknown_playback_does_not_merge_with_confirmed_playback_by_coincidence(self):
        b=batch([record('playback','v',event(None,'unknown'),{'title':'架空'})],source='youtube')
        store.import_batch(self.root,b)
        known=again(b)
        known['requested_range']={'start':'2026-10-17T00:00:00+09:00','end':'2026-10-17T07:00:00+09:00'}
        known['coverage']=[{**known['requested_range'],'evidence':{'method':'synthetic','reference':'fixture'}}]
        known['records'][0]['event_time']=event('2026-10-17T06:00:00+09:00','minute')
        store.import_batch(self.root,known)
        records=store.read_day(self.root,'2026-10-17')['records']
        self.assertEqual(len(records),2)
        self.assertEqual(sum(r['event_time']['precision']=='unknown' for r in records),1)

    def test_issue_free_text_cannot_leak_via_status(self):
        b=batch([],status='failed')
        b['issues']=[{'code':'read_failed','retryable':True,'range':None,'message':'sensitive private content'}]
        with self.assertRaises(ValueError):
            store.import_batch(self.root,b)
        self.assertNotIn('sensitive private content',json.dumps(store.status(self.root)))

    def test_extra_range_fields_cannot_leak_via_status(self):
        b=batch()
        b['requested_range']['message']='private input'
        with self.assertRaises(ValueError):
            store.import_batch(self.root,b)

    def test_backup_failure_does_not_repeat_same_notification(self):
        b=batch()
        with patch('scripts.activity_collection.backup._create_backup_locked', side_effect=OSError('disk full')):
            first=store.import_batch(self.root,b)
            second=store.import_batch(self.root,again(b))
        self.assertEqual(len(first['notifications']),1)
        self.assertEqual(second['notifications'],[])

    def test_restore_rejects_missing_state_even_if_file_and_manifest_entry_removed(self):
        store.import_batch(self.root,batch())
        saved=create_backup(self.root)
        directory=Path(saved['path'])
        manifest_path=directory/'manifest.json'
        manifest=json.loads(manifest_path.read_text())
        state=next(k for k in manifest['files'] if k.startswith('state/'))
        del manifest['files'][state]
        (directory/state).unlink()
        manifest_path.write_text(json.dumps(manifest))
        destination=Path(self.temp.name).resolve()/'restored'
        with self.assertRaises(ValueError):
            restore_backup(directory,destination)
        self.assertFalse(destination.exists())

    def test_restore_requires_manifest_to_include_each_target_state(self):
        store.import_batch(self.root,batch())
        saved=create_backup(self.root)
        directory=Path(saved['path'])
        manifest_path=directory/'manifest.json'
        manifest=json.loads(manifest_path.read_text())
        state=next(k for k in manifest['files'] if k.startswith('state/'))
        del manifest['files'][state]
        manifest_path.write_text(json.dumps(manifest))
        destination=Path(self.temp.name).resolve()/'restored'
        with self.assertRaises(ValueError):
            restore_backup(directory,destination)
        self.assertFalse(destination.exists())
