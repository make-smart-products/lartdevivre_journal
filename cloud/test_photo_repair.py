import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scripts.repair_collection_photos as repair


class PhotoRepairTests(unittest.TestCase):
    def test_repeated_exact_photo_edit_is_confirmed_without_creating_another_post(self):
        class Store:
            def __init__(self):
                self.state={'editorial_batches':{repair.BATCH:{'published':[{'id':'LDV-001','message_id':57,'kind':'article','illustrated':False}]}}}
            def load(self):return copy.deepcopy(self.state)
            def save(self,state,message):self.state=copy.deepcopy(state)
        store=Store()
        asset={'image':'https://upload.wikimedia.org/example.jpg','artist':'Author','license':'Public domain','source_url':'https://commons.wikimedia.org/wiki/File:Example.jpg'}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            local=Path(directory)
            (local/'photo-repair-plan.json').write_text(json.dumps([{'id':'LDV-001','kind':'article','body':'Article','candidates':[asset]}]),encoding='utf-8')
            (local/'photo-selections.json').write_text(json.dumps({'LDV-001':0}),encoding='utf-8')
            with patch.object(repair,'LOCAL',local),patch.object(repair,'credentials',return_value=('fake','fake')),patch.object(repair,'StateStore',return_value=store),patch.object(repair,'load_posts',return_value=[{'id':'LDV-001','caption':'Article'}]),patch.object(repair,'api',side_effect=repair.RequestFailure(400,detail='Bad Request: message is not modified')) as request,patch.object(repair.time,'sleep'),patch('builtins.print'):
                repair.run_articles()
                self.assertEqual(1,request.call_count)
                self.assertTrue(request.call_args.args[0].endswith('/editMessageMedia'))
                self.assertEqual(57,request.call_args.args[1]['message_id'])
                self.assertTrue(store.state['editorial_batches'][repair.BATCH]['published'][0]['illustrated'])
                proof=json.loads((local/'photo-repair-journal.jsonl').read_text(encoding='utf-8'))
                self.assertEqual('already_current',proof['confirmation'])

    def check_quiz_replacement(self,with_photo):
        class Store:
            def __init__(self):
                self.state={'editorial_batches':{repair.BATCH:{'published':[{'id':'LDV-007','message_id':63,'kind':'quiz','illustrated':False}]}}}
            def load(self):
                return copy.deepcopy(self.state)
            def save(self,state,message):
                self.state=copy.deepcopy(state)
        store=Store()
        asset={'image':'https://upload.wikimedia.org/example.jpg','artist':'Photographer','license':'CC BY 4.0','source_url':'https://commons.wikimedia.org/wiki/File:Example.jpg'}
        post={'id':'LDV-007','kind':'quiz','question':'Who?','options':['A','B','C','D'],'correct_option_ids':[0],'explanation':'A is correct'}
        methods=[]
        def api(url,payload,**kwargs):
            method=url.rsplit('/',1)[-1];methods.append(method)
            if method=='sendPoll':
                self.assertEqual('photo',payload['media']['type'])
                self.assertEqual('Who?',payload['question'])
                poll={'id':'new-poll','type':'quiz'}
                if with_photo:
                    poll['media']={'photo':[{'file_id':'file'}]}
                return {'ok':True,'result':{'message_id':157,'poll':poll}}
            self.assertEqual('deleteMessage',method)
            self.assertEqual(63,payload['message_id'])
            confirmed=store.state['editorial_batches'][repair.BATCH]['published'][0]
            self.assertEqual(157,confirmed['message_id'])
            self.assertTrue(confirmed['illustrated'])
            self.assertIsNone(store.state['pending'])
            return {'ok':True,'result':True}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            local=Path(directory)
            (local/'photo-repair-plan.json').write_text(json.dumps([{'id':'LDV-007','kind':'quiz','candidates':[asset]}]),encoding='utf-8')
            (local/'photo-selections.json').write_text(json.dumps({'LDV-007':0}),encoding='utf-8')
            with patch.object(repair,'LOCAL',local),patch.object(repair,'credentials',return_value=('fake','fake')),patch.object(repair,'StateStore',return_value=store),patch.object(repair,'load_posts',return_value=[post]),patch.object(repair,'api',side_effect=api),patch.object(repair.time,'sleep'),patch('builtins.print'):
                if with_photo:
                    repair.run_quizzes()
                    self.assertEqual(['sendPoll','deleteMessage'],methods)
                    self.assertTrue(store.state['editorial_batches'][repair.BATCH]['published'][0]['old_poll_deleted'])
                else:
                    with self.assertRaises(ValueError):
                        repair.run_quizzes()
                    self.assertEqual(['sendPoll'],methods)
                    self.assertEqual(63,store.state['editorial_batches'][repair.BATCH]['published'][0]['message_id'])
                    self.assertTrue(store.state['pending'])

    def test_old_quiz_is_deleted_only_after_confirmed_photo_replacement(self):
        self.check_quiz_replacement(True)

    def test_missing_photo_does_not_delete_existing_quiz(self):
        self.check_quiz_replacement(False)

    def test_credit_links_use_utf16_offsets_and_keep_visible_text_short(self):
        text='🎨 Подпись\nФото: Author · CC BY-SA 4.0.'
        asset={'artist':'Author','license':'CC BY-SA 4.0','source_url':'https://commons.wikimedia.org/wiki/File:Example.jpg'}
        entities=repair.credit_entities(text,asset)
        self.assertEqual(2,len(entities))
        encoded=text.encode('utf-16-le')
        self.assertEqual('Author',encoded[entities[0]['offset']*2:(entities[0]['offset']+entities[0]['length'])*2].decode('utf-16-le'))
        self.assertEqual('https://creativecommons.org/licenses/by-sa/4.0/',entities[1]['url'])
