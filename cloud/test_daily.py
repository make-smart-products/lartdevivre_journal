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
            obj = {'objectID': 1000 + len(value.get('used_object_ids', [])), 'title': 'Test work', 'objectDate': '1900', 'objectName': 'Painting', 'isPublicDomain': True, 'primaryImageSmall': 'https://images.metmuseum.org/test.jpg'}
            rubric = publish.RUBRIC_ORDER[value.get('next_rubric_index', 0)]
            return publish.make_card(obj, rubric)
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fake', 'TELEGRAM_BOT_TOKEN': 'fake'}), patch.object(publish, 'StateStore', return_value=store), patch.object(publish, 'choose_post', side_effect=choose), patch.object(publish.time,'sleep'), patch.object(publish, 'publish', return_value=[42]) as send:
            result = publish.execute(now=now)
            return result, send.call_count

    def test_full_block_has_one_hundred_posts_and_all_seven_rubrics(self):
        state = {'next_rubric_index': 1}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(100, calls)
        self.assertEqual(100, result['published_this_run'])
        expected={rubric:15 for rubric in publish.BASE_RUBRIC_ORDER}
        expected.update(books=10,quiz=15)
        self.assertEqual(expected, dict(Counter(x['rubric'] for x in state['publications'])))

    def test_second_invocation_does_not_repeat_completed_block(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 100}}}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(0, calls)
        self.assertEqual('batch_complete', result['status'])

    def test_resumes_only_remaining_items(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 7}}, 'used_object_ids': list(range(7)), 'next_rubric_index': 2}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(93, calls)
        self.assertEqual(100, state['daily_batches']['2026-11-03']['completed'])

    def test_does_not_send_before_ten_thirty_pm_moscow(self):
        result, calls = self.run_batch({}, dt.datetime(2026, 11, 3, 19, 29, tzinfo=dt.timezone.utc))
        self.assertEqual('not_due', result['status'])
        self.assertEqual(0, calls)

    def test_previous_fifteen_item_target_is_extended_without_repeating_items(self):
        state={'daily_batches':{'2026-11-03':{'completed':15,'target':15}},'used_object_ids':list(range(15))}
        result,calls=self.run_batch(state,dt.datetime(2026,11,3,19,30,tzinfo=dt.timezone.utc))
        self.assertEqual(85,calls)
        self.assertEqual({'completed':100,'target':100},state['daily_batches']['2026-11-03'])

    def test_pending_send_blocks_batch_without_new_messages(self):
        with self.assertRaises(publish.PublishError):
            self.run_batch({'pending': {'object_id': 123}}, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
