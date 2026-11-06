import datetime as dt
import unittest
from unittest.mock import patch

import publish


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(publish.Path, "glob", return_value=[]))
        self.enterContext(patch.object(publish.Path, "exists", return_value=False))

    def obj(self):
        return {"objectID": 123, "title": "A painting", "artistDisplayName": "An artist", "objectDate": "1874",
                "isPublicDomain": True, "primaryImageSmall": "https://images.metmuseum.org/example.jpg", "objectName": "Painting"}

    def test_reject_restricted_image(self):
        obj = self.obj()
        obj["isPublicDomain"] = False
        with self.assertRaises(publish.PublishError):
            publish.make_card(obj)

    def test_no_links_in_caption(self):
        card = publish.make_card(self.obj())
        card["caption"] += " https://example.com"
        with self.assertRaises(publish.PublishError):
            publish.validate_post(card)

    def test_lookalike_image_domain_rejected(self):
        card = publish.make_card(self.obj())
        card["images"] = ["https://images.metmuseum.org.evil.com/image.jpg"]
        with self.assertRaises(publish.PublishError):
            publish.validate_post(card)

    def test_unknown_send_blocks_next_publication(self):
        with self.assertRaises(publish.PublishError):
            publish.ensure_ready({"pending": {"object_id": 123}}, dt.datetime.now(dt.timezone.utc))

    def test_hourly_slot_deduplication(self):
        now = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc)
        self.assertFalse(publish.ensure_ready({"last_published_at": now.isoformat()}, now + dt.timedelta(minutes=55)))
        self.assertTrue(publish.ensure_ready({"last_published_at": now.isoformat()}, now + dt.timedelta(hours=1)))

    def test_delayed_run_does_not_skip_next_slot(self):
        now = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(publish.ensure_ready({"last_published_at": "2026-11-01T11:50:00+00:00"}, now))

    def test_incomplete_telegram_album_rejected(self):
        card = publish.make_card(self.obj())
        card["images"] *= 2
        with self.assertRaises(publish.PublishError):
            publish.publish(card, "fake-token", lambda *args: {"ok": True, "result": [{"message_id": 1, "photo": [{}]}]})

    def test_only_confirmed_ids_returned(self):
        card = publish.make_card(self.obj())
        self.assertEqual([42], publish.publish(card, "fake-token", lambda *args: {"ok": True, "result": {"message_id": 42, "photo": [{}]}}))

    def test_request_errors_do_not_leak_bot_token(self):
        with patch("urllib.request.urlopen", side_effect=OSError("https://api.telegram.org/botSECRET/sendPhoto")):
            with self.assertRaises(publish.PublishError) as error:
                publish.request("https://api.telegram.org/botSECRET/sendPhoto", {})
        self.assertNotIn("SECRET", str(error.exception))

    def test_search_uses_current_api_and_reads_next_page(self):
        calls = []
        used = list(range(100)) + [436528, 436535]
        def fetch(url):
            calls.append(url)
            if "/v1.1/search?" in url:
                query = publish.urllib.parse.parse_qs(publish.urllib.parse.urlparse(url).query)
                return {"total": 101, "objectIDs": list(range(100)) if query["offset"] == ["0"] else [123]}
            if url.endswith("/objects/123"):
                return self.obj()
            self.fail("Unexpected API URL: " + url)
        with patch.object(publish, "QUERIES", ["test"]), patch.object(publish, "RESERVE_OBJECT_IDS", {}):
            card = publish.choose_post({"used_object_ids": used}, fetch, rubric="painting")
        self.assertEqual(123, card["object_id"])
        self.assertTrue(any("offset=100" in url for url in calls))
        self.assertFalse(any("/v1/search" in url for url in calls))

    def test_failed_search_does_not_block_other_queries(self):
        def fetch(url):
            if "q=broken" in url:
                raise publish.PublishError("Сервис вернул HTTP 410")
            if "/search?" in url:
                return {"total": 1, "objectIDs": [123]}
            return self.obj()
        with patch.object(publish, "QUERIES", ["broken", "working"]), patch.object(publish, "RESERVE_OBJECT_IDS", {}):
            card = publish.choose_post({"used_object_ids": [436528, 436535]}, fetch, rubric="painting")
        self.assertEqual(123, card["object_id"])

    def test_painting_reserve_skips_used_work_and_checks_live_rights(self):
        calls = []
        def fetch(url):
            calls.append(url)
            obj = self.obj()
            obj['objectID'] = 123
            return obj
        with patch.object(publish, 'RESERVE_OBJECT_IDS', {'painting': [122, 123]}):
            card = publish.choose_post({'used_object_ids': [436528, 436535, 122]}, fetch, rubric='painting')
        self.assertEqual(123, card['object_id'])
        self.assertEqual([publish.MET + '/objects/123'], calls)

    def test_womens_rubric_rejects_portrait_of_woman_by_man(self):
        obj = self.obj()
        obj.update(title="Portrait of Berthe Morisot", artistDisplayName="Edouard Manet")
        self.assertFalse(publish.matches_rubric(obj, "women"))
        obj.update(artistGender="Female", artistDisplayName="Berthe Morisot")
        self.assertTrue(publish.matches_rubric(obj, "women"))

    def test_fashion_requires_real_costume_not_painted_dress(self):
        obj = self.obj()
        obj.update(title="The Pink Dress")
        self.assertFalse(publish.matches_rubric(obj, "fashion"))
        obj.update(objectName="Evening dress", department="Costume Institute")
        self.assertTrue(publish.matches_rubric(obj, "fashion"))

    def test_travel_requires_place_not_artist_name(self):
        obj = self.obj()
        obj.update(artistDisplayName="A Parisian artist")
        self.assertFalse(publish.matches_rubric(obj, "travel"))
        obj.update(title="Venice")
        self.assertTrue(publish.matches_rubric(obj, "travel"))
        for title in ('[Florence Anne Claxton]', 'Portrait of Miss Florence Smith', 'A Parisian Woman'):
            obj.update(title=title)
            self.assertFalse(publish.matches_rubric(obj, "travel"))
        obj.update(title='View of Florence from the Arno')
        self.assertTrue(publish.matches_rubric(obj, "travel"))

    def test_architecture_accepts_actual_temple(self):
        obj = self.obj()
        obj.update(title="The Temple of Dendur", objectName="Temple, Dendur")
        self.assertTrue(publish.matches_rubric(obj, "architecture"))

    def test_rotation_advances_only_after_confirmed_send(self):
        import copy
        state = {"used_object_ids": [], "next_rubric_index": 4}
        snapshots = []
        card = publish.make_card(self.obj())
        store = unittest.mock.Mock()
        store.load.return_value = state
        store.save.side_effect = lambda value, message: snapshots.append(copy.deepcopy(value))
        def send(post, token):
            self.assertEqual(4, state["next_rubric_index"])
            self.assertIsNotNone(snapshots[0]["pending"])
            return [42]
        with patch.dict(publish.os.environ, {"GITHUB_TOKEN": "fake", "TELEGRAM_BOT_TOKEN": "fake"}), patch.object(publish.sys, "argv", ["publish"]), patch.object(publish, "StateStore", return_value=store), patch.object(publish, "choose_post", return_value=card), patch.object(publish, "publish", side_effect=send):
            publish.execute_one()
        self.assertEqual(5 % len(publish.RUBRIC_ORDER), state["next_rubric_index"])
        self.assertIsNone(snapshots[-1]["pending"])
        self.assertEqual("painting", state["publications"][-1]["rubric"])

    def test_uncertain_send_keeps_rubric_and_pending(self):
        state = {"used_object_ids": [], "next_rubric_index": 0}
        store = unittest.mock.Mock()
        store.load.return_value = state
        card = publish.make_card(self.obj())
        with patch.dict(publish.os.environ, {"GITHUB_TOKEN": "fake", "TELEGRAM_BOT_TOKEN": "fake"}), patch.object(publish.sys, "argv", ["publish"]), patch.object(publish, "StateStore", return_value=store), patch.object(publish, "choose_post", return_value=card), patch.object(publish, "publish", side_effect=publish.PublishError("Timeout")):
            with self.assertRaises(publish.PublishError):
                publish.execute_one()
        self.assertEqual(0, state["next_rubric_index"])
        self.assertIsNotNone(state["pending"])
        self.assertEqual(1, store.save.call_count)


if __name__ == "__main__":
    unittest.main()
