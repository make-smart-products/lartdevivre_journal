import copy
import datetime as dt
import os
import unittest
from collections import Counter
from unittest.mock import Mock, patch

import publish


class DailyBatchTests(unittest.TestCase):
    def run_batch(self, state, now):
        store = Mock()
        store.load.side_effect = lambda: copy.deepcopy(state)
        def save(value, message):
            state.clear()
            state.update(copy.deepcopy(value))
        store.save.side_effect = save
        def choose(value):
            obj = {'objectID': 1000 + len(value.get('used_object_ids', [])), 'title': 'Test work', 'objectDate': '1900', 'isPublicDomain': True, 'primaryImageSmall': 'https://images.metmuseum.org/test.jpg'}
            rubric = publish.RUBRIC_ORDER[value.get('next_rubric_index', 0)]
            return publish.make_card(obj, rubric)
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fake', 'TELEGRAM_BOT_TOKEN': 'fake'}), patch.object(publish, 'StateStore', return_value=store), patch.object(publish, 'choose_post', side_effect=choose), patch.object(publish, 'publish', return_value=[42]) as send:
            result = publish.execute(now=now)
            return result, send.call_count

    def test_full_block_has_fifteen_posts_and_three_per_rubric(self):
        state = {'next_rubric_index': 1}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(15, calls)
        self.assertEqual(15, result['published_this_run'])
        self.assertEqual({rubric: 3 for rubric in publish.RUBRIC_ORDER}, dict(Counter(x['rubric'] for x in state['publications'])))

    def test_second_invocation_does_not_repeat_completed_block(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 15}}}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(0, calls)
        self.assertEqual('batch_complete', result['status'])

    def test_resumes_only_remaining_items(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 7}}, 'used_object_ids': list(range(7)), 'next_rubric_index': 2}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(8, calls)
        self.assertEqual(15, state['daily_batches']['2026-11-03']['completed'])

    def test_does_not_send_before_ten_pm_moscow(self):
        result, calls = self.run_batch({}, dt.datetime(2026, 11, 3, 18, 59, tzinfo=dt.timezone.utc))
        self.assertEqual('not_due', result['status'])
        self.assertEqual(0, calls)

    def test_pending_send_blocks_batch_without_new_messages(self):
        with self.assertRaises(publish.PublishError):
            self.run_batch({'pending': {'object_id': 123}}, dt.datetime(2026, 11, 3, 19, 0, tzinfo=dt.timezone.utc))
