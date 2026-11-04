import unittest
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
