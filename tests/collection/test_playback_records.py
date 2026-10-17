import copy
import tempfile
import unittest
from support import CONFIG, batch, record, event, runtime, again
from scripts.activity_collection.records import validate_batch
from scripts.activity_collection.store import import_batch, read_day


class PlaybackTests(unittest.TestCase):
    def test_same_video_keeps_distinct_days_and_observed_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = runtime(tmp)
            b = batch([record('playback', 'video-1', event('2026-10-16T10:00:00+09:00', 'minute'), {'title': '架空の動画'})], source='youtube')
            import_batch(root, b)
            second = again(b)
            second['records'][0]['event_time'] = event('2026-10-16T12:00:00+09:00', 'minute')
            import_batch(root, second)
            import_batch(root, again(second))
            old = again(b)
            old['requested_range'] = {'start': '2026-10-15T00:00:00+09:00', 'end': '2026-10-16T00:00:00+09:00'}
            old['coverage'][0].update(old['requested_range'])
            old['records'][0]['event_time'] = event('2026-10-15', 'day')
            import_batch(root, old)
            self.assertEqual(len(read_day(root, '2026-10-15')['records']), 1)
            r = read_day(root, '2026-10-16')['records']
            self.assertEqual(len(r), 1)
            self.assertEqual(len(r[0]['data']['observed_playback_times']), 2)
            self.assertNotIn('play_count', r[0]['data'])

    def test_non_playback_records_and_invented_duration_are_rejected(self):
        for kind, data in [('search', {'title': '架空の検索'}), ('playback', {'title': '架空の動画', 'watched_seconds': 500})]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_batch(batch([record(kind, data=data)], source='youtube'), CONFIG)

    def test_dates_at_exclusive_midnight_are_not_in_previous_day(self):
        b = batch([record('playback', time=event('2026-10-17', 'day'), data={'title': '翌日の動画'})], source='youtube')
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)

    def test_observed_times_cannot_be_from_another_date(self):
        b = batch([record('playback', time=event('2026-10-16', 'day'), data={'title': '動画', 'observed_playback_times': [event('2026-10-17', 'day')]})], source='youtube')
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)
