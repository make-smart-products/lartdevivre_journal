from pathlib import Path
import sys
import unittest
import copy
import json
import tempfile
from unittest.mock import patch
import scripts.publish_collection as collection

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.publish_collection import load_posts, payload_for, validate_manifest, same_artist, CHANNEL


class CollectionTests(unittest.TestCase):
    def test_artist_matching_rejects_namesakes(self):
        self.assertFalse(same_artist('Kara Walker','Walker Evans'))
        self.assertFalse(same_artist('Agnes Martin','Martin Schongauer'))
        self.assertFalse(same_artist('Lee Miller','Samuel J. Miller'))
        self.assertTrue(same_artist('Mary Cassatt','Cassatt, Mary (American)'))
        self.assertTrue(same_artist('Elisabeth Louise Vigee Le Brun','Élisabeth Louise Vigée Le Brun'))

    def test_article_payload_does_not_add_a_link_preview(self):
        method, payload = payload_for({'kind':'article','caption':'Test article'})
        self.assertEqual('sendMessage', method)
        self.assertEqual(CHANNEL, payload['chat_id'])
        self.assertTrue(payload['link_preview_options']['is_disabled'])

    def test_quiz_is_interactive_and_explanation_is_not_in_question(self):
        post={'kind':'quiz','question':'Who?','options':['A','B','C','D'],'correct_option_ids':[2],'explanation':'The answer is C'}
        method,payload=payload_for(post)
        self.assertEqual('sendPoll',method)
        self.assertEqual('quiz',payload['type'])
        self.assertEqual([2],payload['correct_option_ids'])
        self.assertEqual([{'text':x} for x in post['options']],payload['options'])
        self.assertNotIn('answer',payload['question'])

    def test_photo_uses_caption_and_correct_destination(self):
        method,payload=payload_for({'kind':'article','caption':'Caption','image':'https://images.metmuseum.org/a.jpg'})
        self.assertEqual('sendPhoto',method)
        self.assertEqual(CHANNEL,payload['chat_id'])
        self.assertEqual('Caption',payload['caption'])

    def test_incomplete_manuscript_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_manifest([{'id':'LDV-001','caption':'A','kind':'article'}])

    def test_manuscript_has_expected_posts_and_revised_vermeer(self):
        path=Path(__file__).resolve().parents[1]/'materials/imported/review.md'
        if not path.exists():
            self.skipTest('Private manuscript is intentionally not in the public repository')
        posts=load_posts(path)
        self.assertEqual(100,len(posts))
        self.assertEqual(85,sum(p['kind']=='article' for p in posts))
        self.assertEqual(15,sum(p['kind']=='quiz' for p in posts))
        self.assertIn('кувшином',posts[1]['caption'])
        self.assertEqual('sendPoll',payload_for(posts[6])[0])
        validate_manifest(posts)

    def test_timeout_retains_reservation_and_stops_batch(self):
        self.check_delivery(resume=False)

    def test_resume_skips_confirmed_posts(self):
        self.check_delivery(resume=True)

    def test_explicitly_rejected_photo_does_not_send_bare_text(self):
        self.check_delivery(resume=True,image_unavailable=True)

    def check_delivery(self,resume,image_unavailable=False):
        posts=[{'id':'LDV-'+str(n).zfill(3),'title':'Title '+str(n),'kind':'article','caption':'Article '+str(n),'image':'https://images.metmuseum.org/example.jpg'} for n in range(1,101)]
        if image_unavailable:
            posts[-1]['image']='https://images.metmuseum.org/example.jpg'
            posts[-1]['caption']+='\n\nМузейная иллюстрация: test'
        class Store:
            def __init__(self):
                receipts=[{'id':p['id'],'message_id':n+1} for n,p in enumerate(posts[:99])] if resume else []
                self.state={'editorial_batches':{collection.BATCH:{'target':100,'published':receipts}}}
            def load(self):
                return copy.deepcopy(self.state)
            def save(self,state,message):
                self.state=copy.deepcopy(state)
        store=Store()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            local=Path(directory)
            (local/'mass-ready.json').write_text(json.dumps(posts),encoding='utf-8')
            reply={'ok':True,'result':{'message_id':100,'date':12345,'photo':[{'file_id':'fake'}]}}
            effects=[collection.RequestFailure(400,detail='Bad Request: failed to get HTTP URL content'),reply] if image_unavailable else None if resume else collection.RequestFailure()
            with patch.object(collection,'LOCAL',local),patch.object(collection,'credentials',return_value=('fake','fake')),patch.object(collection,'StateStore',return_value=store),patch.object(collection.time,'sleep'),patch.object(collection,'api',return_value=reply,side_effect=effects) as send:
                if image_unavailable:
                    with self.assertRaises(collection.RequestFailure):
                        collection.run()
                    self.assertIsNone(store.state['pending'])
                    self.assertEqual(99,len(store.state['editorial_batches'][collection.BATCH]['published']))
                elif resume:
                    collection.run()
                    self.assertIsNone(store.state['pending'])
                    self.assertEqual(100,len(store.state['editorial_batches'][collection.BATCH]['published']))
                    proof=json.loads((local/'delivery-journal.jsonl').read_text(encoding='utf-8'))
                    self.assertEqual('LDV-100',proof['id'])
                else:
                    with self.assertRaises(collection.RequestFailure):
                        collection.run()
                    self.assertEqual('LDV-001',store.state['pending']['editorial_id'])
                    self.assertFalse((local/'delivery-journal.jsonl').exists())
                self.assertEqual(1,send.call_count)
                if image_unavailable:
                    payload=send.call_args.args[1]
                    self.assertIn('photo',payload)
                    self.assertNotIn('text',payload)
