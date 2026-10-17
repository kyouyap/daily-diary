import copy
import unittest
from support import CONFIG, batch, record, event
from scripts.activity_collection.records import validate_batch


class XRecordTests(unittest.TestCase):
    def test_replies_require_reply_target_instead_of_absorbing_other_authors(self):
        b = batch([record('reply')])
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)
        b['records'][0]['data']['reply_to_id'] = 'parent-1'
        self.assertEqual(validate_batch(b, CONFIG)['records'][0]['kind'], 'reply')
        b['records'][0]['data']['author_id'] = 'other-author'
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)

    def test_quote_requires_separate_original_reference(self):
        b = batch([record('quote')])
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)
        b['records'][0]['data'].update(original_id='original-1', original_author_id='other-author', original_text='引用元の架空文')
        value = validate_batch(b, CONFIG)['records'][0]['data']
        self.assertEqual(value['text'], '架空の投稿')
        self.assertEqual(value['original_text'], '引用元の架空文')

    def test_private_account_mismatch_is_rejected_before_storage(self):
        b = batch()
        b['observed_identity'] = 'different-owner'
        with self.assertRaises(ValueError):
            validate_batch(b, CONFIG)

    def test_repost_unknown_share_time_keeps_original_time_separate(self):
        r = record('repost', time=event(None, 'unknown'), data={'author_id': 'other', 'text': '元投稿'})
        r['content_time'] = event('2026-09-01T12:00:00+09:00')
        value = validate_batch(batch([r], status='partial'), CONFIG)['records'][0]
        self.assertIsNone(value['event_time']['value'])
        self.assertEqual(value['content_time']['value'], '2026-09-01T12:00:00+09:00')
