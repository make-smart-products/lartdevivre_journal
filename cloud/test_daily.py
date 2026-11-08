import copy
import datetime as dt
import os
import unittest
from collections import Counter
from unittest.mock import Mock, patch

import publish


class DailyBatchTests(unittest.TestCase):
    def run_batch(self, state, now, scheduled=False, publish_now=False):
        store = Mock()
        store.load.side_effect = lambda: copy.deepcopy(state)
        def save(value, message):
            state.clear()
            state.update(copy.deepcopy(value))
        store.save.side_effect = save
        def choose(value):
            obj = {'objectID': 150 + len(value.get('used_object_ids', [])), 'title': 'Test work', 'objectDate': '1900', 'objectName': 'Painting', 'isPublicDomain': True, 'primaryImageSmall': 'https://images.metmuseum.org/test.jpg'}
            rubric = publish.RUBRIC_ORDER[value.get('next_rubric_index', 0)]
            return {'object_id': obj['objectID'], 'rubric': rubric, 'topic': 'Test', 'images': [obj['primaryImageSmall']], 'source_url': 'https://example.com'}
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fake', 'TELEGRAM_BOT_TOKEN': 'fake'}), patch.object(publish, 'StateStore', return_value=store), patch.object(publish, 'choose_post', side_effect=choose), patch.object(publish.time,'sleep'), patch.object(publish, 'publish', return_value=[42]) as send:
            result = publish.execute(now=now, scheduled=scheduled, publish_now=publish_now)
            return result, send.call_count

    def test_full_block_has_fifteen_posts_and_four_rubrics(self):
        state = {'next_rubric_index': 1}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(15, calls)
        self.assertEqual(15, result['published_this_run'])
        expected = {'fashion': 4, 'architecture': 4, 'painting': 4, 'celebrities': 3}
        self.assertEqual(expected, dict(Counter(x['rubric'] for x in state['publications'])))

    def test_second_invocation_does_not_repeat_completed_block(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 15}}}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(0, calls)
        self.assertEqual('batch_complete', result['status'])

    def test_scheduled_run_delayed_past_midnight_resumes_previous_moscow_date(self):
        state = {'daily_batches': {'2026-11-04': {'completed': 14}}}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 5, 0, 29, tzinfo=dt.timezone.utc), scheduled=True)
        self.assertEqual(1, calls)
        self.assertEqual('2026-11-04', result['date_moscow'])
        self.assertEqual(15, state['daily_batches']['2026-11-04']['completed'])
        self.assertEqual(0, self.run_batch(state, dt.datetime(2026, 11, 5, 0, 30, tzinfo=dt.timezone.utc), scheduled=True)[1])

    def test_resumes_only_remaining_items(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 7}}, 'used_object_ids': list(range(7)), 'next_rubric_index': 2}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(8, calls)
        self.assertEqual(15, state['daily_batches']['2026-11-03']['completed'])

    def test_does_not_send_before_ten_thirty_pm_moscow(self):
        result, calls = self.run_batch({}, dt.datetime(2026, 11, 3, 19, 29, tzinfo=dt.timezone.utc))
        self.assertEqual('not_due', result['status'])
        self.assertEqual(0, calls)

    def test_explicit_immediate_run_uses_today_and_evening_cannot_repeat_it(self):
        state = {'daily_batches': {'2026-11-03': {'completed': 14}}}
        result, calls = self.run_batch(state, dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc), publish_now=True)
        self.assertEqual(1, calls)
        self.assertEqual('2026-11-03', result['date_moscow'])
        self.assertEqual(0, self.run_batch(state, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))[1])

    def test_previously_completed_large_block_is_not_repeated_after_reduction(self):
        state={'daily_batches':{'2026-11-03':{'completed':15,'target':15}},'used_object_ids':list(range(15))}
        result,calls=self.run_batch(state,dt.datetime(2026,11,3,19,30,tzinfo=dt.timezone.utc))
        self.assertEqual(0,calls)
        self.assertEqual({'completed':15,'target':15},state['daily_batches']['2026-11-03'])

    def test_pending_send_blocks_batch_without_new_messages(self):
        with self.assertRaises(publish.PublishError):
            self.run_batch({'pending': {'object_id': 123}}, dt.datetime(2026, 11, 3, 19, 30, tzinfo=dt.timezone.utc))

    def test_preflight_shortage_does_not_change_receipts_or_send_messages(self):
        state = {'used_object_ids': [1], 'next_rubric_index': 0,
                 'editorial_policy_version': '2026-11-08-four-rubrics'}
        original = copy.deepcopy(state)
        with patch.object(publish, 'choose_post', side_effect=[
            {'object_id': 2, 'rubric': 'fashion', 'kind': 'article', 'images': ['photo']},
            publish.PublishError('Needs new architecture material')]), patch.object(publish, 'publish') as send:
            with self.assertRaises(publish.PublishError):
                publish.preflight_batch(state, 15)
        send.assert_not_called()
        self.assertEqual(original, state)
