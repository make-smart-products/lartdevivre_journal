import datetime as dt
import unittest
from unittest.mock import patch

import publish


class PublishTests(unittest.TestCase):
    def obj(self):
        return {"objectID": 123, "title": "A painting", "artistDisplayName": "An artist", "objectDate": "1874",
                "isPublicDomain": True, "primaryImageSmall": "https://images.metmuseum.org/example.jpg"}

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

    def test_three_hour_slot_deduplication(self):
        now = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc)
        self.assertFalse(publish.ensure_ready({"last_published_at": (now - dt.timedelta(hours=1)).isoformat()}, now))
        self.assertTrue(publish.ensure_ready({"last_published_at": (now - dt.timedelta(hours=3)).isoformat()}, now))

    def test_delayed_run_does_not_skip_next_slot(self):
        now = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(publish.ensure_ready({"last_published_at": "2026-11-01T09:10:00+00:00"}, now))

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
        with patch.object(publish, "QUERIES", ["test"]):
            card = publish.choose_post({"used_object_ids": used}, fetch)
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
        with patch.object(publish, "QUERIES", ["broken", "working"]):
            card = publish.choose_post({"used_object_ids": [436528, 436535]}, fetch)
        self.assertEqual(123, card["object_id"])


if __name__ == "__main__":
    unittest.main()
