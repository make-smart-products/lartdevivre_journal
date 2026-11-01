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


if __name__ == "__main__":
    unittest.main()
