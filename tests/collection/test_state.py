import importlib
import unittest
from datetime import datetime
from support import CONFIG


class PlanningTests(unittest.TestCase):
    def setUp(self):
        try:
            self.state = importlib.import_module('scripts.activity_collection.state')
        except ModuleNotFoundError:
            self.fail('取得要求の計画処理がまだ存在しない')

    def test_recent_range_includes_today_only_until_now(self):
        plans = self.state.plan_collection(CONFIG, [], datetime.fromisoformat('2026-10-17T07:00:00+09:00'))
        recent = next(p for p in plans if p['mode'] == 'recent')
        self.assertEqual(recent['range'], {'start': '2026-10-14T00:00:00+09:00', 'end': '2026-10-17T07:00:00+09:00'})
        backfill = next(p for p in plans if p['mode'] == 'backfill')
        self.assertEqual(backfill['range'], {'start': '2026-09-17T00:00:00+09:00', 'end': '2026-09-24T00:00:00+09:00'})

    def test_backfill_skips_confirmed_ranges_but_preserves_gaps(self):
        state = {'source': 'x', 'account': 'account-a', 'initial_start': '2026-09-01T00:00:00+09:00',
                 'covered_ranges': [{'start': '2026-09-01T00:00:00+09:00', 'end': '2026-09-09T00:00:00+09:00'}], 'blocked_reason': None}
        plans = self.state.plan_collection(CONFIG, [state], datetime.fromisoformat('2026-10-17T07:00:00+09:00'))
        old = next(p for p in plans if p['source'] == 'x' and p['mode'] == 'backfill')
        self.assertEqual(old['range']['start'], '2026-09-09T00:00:00+09:00')
        self.assertEqual(old['range']['end'], '2026-09-16T00:00:00+09:00')

    def test_login_wait_is_scoped_to_one_target(self):
        state = {'source': 'x', 'account': 'account-a', 'blocked_reason': 'login_required', 'covered_ranges': []}
        plans = self.state.plan_collection(CONFIG, [state], datetime.fromisoformat('2026-10-17T07:00:00+09:00'))
        self.assertEqual(next(p for p in plans if p['source'] == 'x')['blocked_reason'], 'login_required')
        self.assertIsNone(next(p for p in plans if p['source'] == 'youtube')['blocked_reason'])
