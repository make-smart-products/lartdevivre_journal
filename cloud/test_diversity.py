import unittest
import datetime as dt
import hashlib
import json
from unittest.mock import patch
import publish


class EditorialDiversityTests(unittest.TestCase):
    def obj(self):
        return {'objectID': 123, 'title': 'A print', 'artistDisplayName': 'Artist', 'objectDate': '1900',
                'objectName': 'Print', 'isPublicDomain': True, 'primaryImageSmall': 'https://images.metmuseum.org/new.jpg'}

    def test_different_cyanotypes_cannot_fill_same_hundred_item_window(self):
        obj=self.obj();obj['medium']='Cyanotype'
        card=publish.make_card(obj,'women')
        state={'recent_content':[{'visual_family':'cyanotype','image':'https://images.metmuseum.org/other.jpg'}]}
        self.assertFalse(publish.diverse_enough(card,state))
        self.assertTrue(publish.diverse_enough(card,{'recent_content':[]}))

    def test_artist_cannot_repeat_consecutively_or_exceed_two_per_hundred(self):
        card=publish.make_card(self.obj(),'women')
        self.assertFalse(publish.diverse_enough(card,{'recent_content':[{'creator':'artist'}]}))
        self.assertFalse(publish.diverse_enough(card,{'recent_content':[{'creator':'artist'},{'creator':'artist'},{'creator':'other'}]}))
        self.assertTrue(publish.diverse_enough(card,{'recent_content':[{'creator':'artist'},{'creator':'other'}]}))

    def test_same_work_or_image_cannot_be_reused_under_new_catalogue_number(self):
        card=publish.make_card(self.obj(),'painting')
        for recent in ({'image':card['images'][0]},{'work_key':card['work_key']}):
            self.assertFalse(publish.diverse_enough(card,{'recent_content':[recent]}))

    def test_quiz_keeps_catalogue_answer_and_requires_confirmed_photo(self):
        card=publish.make_card(self.obj(),'quiz')
        self.assertEqual('Печатная графика',card['options'][card['correct_option_ids'][0]])
        calls=[]
        def fetch(url,payload):
            calls.append(payload)
            return {'ok':True,'result':{'message_id':7,'poll':{'type':'quiz','media':{'photo':[{}]}}}}
        self.assertEqual([7],publish.publish(card,'fake',fetch))
        self.assertTrue(calls[0]['shuffle_options'])
        self.assertEqual('photo',calls[0]['media']['type'])
        with self.assertRaises(publish.PublishError):
            publish.publish(card,'fake',lambda *a:{'ok':True,'result':{'message_id':7,'poll':{'type':'quiz'}}})

    def test_books_rubric_rejects_unrelated_objects(self):
        obj=self.obj()
        self.assertFalse(publish.matches_rubric(obj,'books'))
        obj['title']='King Lear'
        self.assertTrue(publish.matches_rubric(obj,'books'))

    def test_quiz_rejects_unverifiable_medium_and_invalid_answer(self):
        obj=self.obj();obj['objectName']='Fragment'
        with self.assertRaises(publish.PublishError):publish.make_card(obj,'quiz')
        card=publish.make_card(self.obj(),'quiz');card['correct_option_ids']=[9]
        with self.assertRaises(publish.PublishError):publish.validate_post(card)

    def test_uncertain_museum_classification_is_not_a_quiz_answer(self):
        obj = dict(self.obj(), objectName='Sculpture or whistle', classification='Ceramics',
                   title='Possibly a Sculpture or Whistle')
        self.assertFalse(publish.matches_rubric(obj, 'quiz'))

    def test_travel_reserve_prefers_a_less_featured_city(self):
        catalogue = json.dumps({'rubrics': {'travel': {'Florence': [122], 'Venice': [123]}}})
        obj = dict(self.obj(), title='View of Venice from the Grand Canal')
        state = {'recent_content': [{'rubric': 'travel', 'subject': 'Florence'}]}

        def fetch(url):
            self.assertTrue(url.endswith('/objects/123'))
            return obj

        with patch.object(publish.Path, 'glob', return_value=[]), \
             patch.object(publish.Path, 'exists', return_value=True), \
             patch.object(publish.Path, 'read_text', return_value=catalogue):
            card = publish.choose_legacy_post(state, fetch, rubric='travel')
        self.assertEqual('Venice', card['subject'])

    def test_fictional_scene_in_rome_is_not_a_travel_view(self):
        obj = dict(self.obj(), title="Rome, Titus's Garden (Shakespeare, Act 4, Scene 1)")
        self.assertFalse(publish.matches_rubric(obj, 'travel'))
        self.assertTrue(publish.matches_rubric(obj, 'books'))

    def test_city_only_on_reverse_does_not_qualify_the_front_as_travel(self):
        obj = dict(self.obj(), title='A River God (recto); Sketches for the Vestibule, Florence (verso)')
        self.assertFalse(publish.matches_rubric(obj, 'travel'))

    def test_century_quiz_uses_catalogue_range_and_skips_cross_century_dates(self):
        obj=self.obj();obj.update(objectID=125,objectBeginDate=1880,objectEndDate=1890,objectDate='1880–1890')
        quiz=publish.make_quiz_question(obj)
        self.assertEqual('19-й век',quiz['options'][quiz['correct_option_ids'][0]])
        obj.update(objectBeginDate=1890,objectEndDate=1910)
        self.assertNotIn('веке',publish.make_quiz_question(obj)['question'])

    def test_artist_quiz_preserves_author_and_does_not_duplicate_correct_option(self):
        obj=self.obj();obj.update(objectID=124,artistDisplayName='Mary Cassatt')
        quiz=publish.make_quiz_question(obj)
        self.assertEqual('Mary Cassatt',quiz['options'][quiz['correct_option_ids'][0]])
        self.assertEqual(4,len(set(quiz['options'])))

    def test_quiz_switches_to_less_used_verified_technique(self):
        obj = dict(self.obj(), medium='Etching')
        quiz = publish.make_quiz_question(obj, [{'quiz_format': 'medium'}, {'quiz_format': 'artist'}])
        self.assertEqual('technique', quiz['quiz_format'])
        self.assertEqual('Офорт', quiz['options'][quiz['correct_option_ids'][0]])
        obj['medium'] = 'Etching and engraving'
        quiz = publish.make_quiz_question(obj, [{'quiz_format': 'medium'}, {'quiz_format': 'artist'}])
        self.assertNotEqual('technique', quiz['quiz_format'])

    def test_modern_copy_cannot_be_presented_as_artist_work_from_antiquity(self):
        obj=self.obj();obj.update(artistBeginDate='1881',objectEndDate=-1300)
        with self.assertRaises(publish.PublishError):publish.make_card(obj,'women')

    def test_cached_catalogue_requires_recent_verification_exact_object_photo_and_rights(self):
        obj=self.obj();card=publish.make_card(obj)
        now=dt.datetime(2026,11,4,tzinfo=dt.timezone.utc)
        card.update(editorial_reviewed=True,verified_object=obj,source_verified_at=now.isoformat(),photo_verified_at=now.isoformat(),
                    catalogue_sha256=hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest())
        self.assertEqual(obj,publish.snapshot_object(card,now))
        self.assertIsNone(publish.snapshot_object(card,now+dt.timedelta(days=8)))
        for change in ({'object_id':124},{'images':['https://images.metmuseum.org/wrong.jpg']},
                       {'editorial_reviewed':False},{'source_verified_at':'invalid'}):
            self.assertIsNone(publish.snapshot_object(dict(card,**change),now))
        bad=dict(obj,isPublicDomain=False)
        self.assertIsNone(publish.snapshot_object(dict(card,verified_object=bad,catalogue_sha256=hashlib.sha256(json.dumps(bad,sort_keys=True).encode()).hexdigest()),now))
