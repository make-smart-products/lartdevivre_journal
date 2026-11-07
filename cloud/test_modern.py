import copy
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import modern
import publish


class ModernTests(unittest.TestCase):
    def cards(self):
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in (Path(__file__).parent / "queue").glob("modern-*.json")]

    def card(self, rubric="painting"):
        return copy.deepcopy(next(post for post in self.cards() if post["rubric"] == rubric))

    def test_queue_has_unique_images_and_supported_rights(self):
        posts = self.cards()
        self.assertGreaterEqual(len(posts), 50)
        self.assertEqual(len(posts), len({post["object_id"] for post in posts}))
        self.assertEqual(len(posts), len({post["images"][0] for post in posts}))
        for post in posts:
            with self.subTest(topic=post["topic"]):
                modern.validate(post, dt.datetime.fromisoformat(post["source_verified_at"]))
                self.assertLessEqual(len(post["caption"].encode("utf-16-le")) // 2, 1024)

    def test_historical_art_and_outdated_fashion_are_rejected(self):
        for rubric, year in (("painting", 1900), ("women", 2000), ("fashion", 2024)):
            post = self.card(rubric)
            post["modern_year"] = year
            with self.assertRaises(ValueError):
                modern.validate(post, dt.datetime.fromisoformat(post["source_verified_at"]))

    def test_photo_licence_alone_does_not_authorize_artwork(self):
        post = self.card()
        post["artwork_rights_verified"] = False
        with self.assertRaises(ValueError):
            modern.validate(post, dt.datetime.fromisoformat(post["source_verified_at"]))
        post["artwork_rights_verified"] = True
        post["licensed_images"][0].update(credit="Photo in a gallery", permission="")
        with self.assertRaises(ValueError):
            modern.validate(post, dt.datetime.fromisoformat(post["source_verified_at"]))

    def test_expired_fashion_and_lookalike_source_are_rejected(self):
        post = self.card("fashion")
        checked = dt.datetime.fromisoformat(post["source_verified_at"])
        with self.assertRaises(ValueError):
            modern.validate(post, checked + dt.timedelta(days=61))
        post["source_url"] = "https://pradagroup.com.example.org/collection"
        with self.assertRaises(ValueError):
            modern.validate(post, checked)

    def test_attribution_survives_telegram_payload(self):
        post = self.card()
        payloads = []
        def send(url, payload):
            payloads.append(payload)
            return {"ok": True, "result": {"message_id": 1, "photo": [{}]}}
        publish.publish(post, "fake", send)
        self.assertEqual(post["caption_entities"], payloads[0]["caption_entities"])
        self.assertNotIn("https://", payloads[0]["caption"])

    def test_modern_queue_never_falls_back_to_historical_catalogue(self):
        def fetch(*args):
            self.fail("Modern rubric must not query historical catalogue")
        with patch.object(Path, "glob", return_value=[]):
            for rubric in modern.RUBRICS:
                with self.assertRaisesRegex(publish.PublishError, "современную очередь"):
                    publish.choose_post({}, fetch, rubric=rubric)

    def test_all_four_rubrics_supply_fifteen_distinct_posts_in_rotation(self):
        state = {"used_object_ids": [], "recent_content": []}
        for _ in range(15):
            for rubric in ("fashion", "architecture", "women", "painting"):
                post = publish.choose_post(state, rubric=rubric)
                state["used_object_ids"].append(post["object_id"])
                publish.record_editorial_history(state, post)
        self.assertEqual(60, len(set(state["used_object_ids"])))

    def test_portrait_profile_requires_explicit_image_role(self):
        post = next(post for post in self.cards() if post.get("content_kind") == "artist-profile")
        checked = dt.datetime.fromisoformat(post["source_verified_at"])
        modern.validate(post, checked)
        post["licensed_images"][0]["image_role"] = "artwork"
        with self.assertRaises(ValueError):
            modern.validate(post, checked)


if __name__ == "__main__":
    unittest.main()
