"""Add licensed photographs to the already published LDV collection."""
import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import threading
import urllib.parse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.publish_collection import api, credentials, LOCAL, CHANNEL, BATCH, RequestFailure, load_posts, same_artist, payload_for
from cloud.publish import StateStore
WIKI_LOCK=threading.Lock()


def wiki_api(url):
    with WIKI_LOCK:
        for attempt in range(4):
            try:
                result=api(url)
                time.sleep(1.1)
                return result
            except RequestFailure as error:
                if error.status not in (None,429) or attempt==3:
                    raise
                delay=min(error.retry_after or (30 if error.status==429 else 5),55)
                print(json.dumps({'source_throttled':True,'wait_seconds':delay}),flush=True)
                time.sleep(delay)

PERSONS={6:'Virginia Woolf',14:'Jane Austen',21:'Agnes Varda',23:'Frida Kahlo',28:'Toni Morrison',30:'Sonia Delaunay',35:'Simone de Beauvoir',38:"Georgia O'Keeffe",42:'Maya Angelou',45:'Hilma af Klint',50:'Greta Gerwig',52:'Tamara de Lempicka',53:'Zaha Hadid',57:'Margaret Atwood',59:'Louise Bourgeois',60:'Eileen Gray',64:'Chimamanda Ngozi Adichie',66:'Yayoi Kusama',80:'Lee Miller',86:'Kara Walker',96:'Agnes Martin',97:'Cindy Sherman',98:'Paula Rego'}
TOPICS={3:['Bauhaus Dessau building'],5:['wide leg trousers'],10:['Art Deco facade'],11:['Florence panorama'],12:['colourful dress'],15:['waistcoat woman'],17:['Japanese room tatami'],20:['jeans'],22:['white shirt'],24:['interior lamp'],26:['Istanbul skyline'],27:['pantsuit woman'],29:['ballet flats'],32:['apartment interior'],33:['Kyoto street'],34:['handbag'],36:['layered clothing'],39:['colourful room interior'],40:['Lisbon cityscape'],41:['necklace'],44:['fashion runway'],46:['antique vase'],47:['Mexico City skyline'],48:['clothing store'],51:['knitted sweater'],54:['Tbilisi panorama'],56:['black dress'],58:['overcoat woman'],62:['art museum interior'],63:['sunglasses'],65:['midi skirt'],68:['Scandinavian interior'],69:['suitcase packing'],70:['sneakers'],71:['film costume exhibition'],72:['second hand clothing shop'],75:['gallery wall interior'],76:['breakfast coffee'],77:['street style fashion'],78:['business suit woman'],81:['carpet textile'],82:['coffeehouse interior'],83:['evening gown'],84:['clothes laundry'],87:['ceramic vase'],88:['postcards'],90:['bookshelves interior'],92:['food market'],94:['furniture restoration'],95:['cafe terrace'],100:['art museum interior']}
QUIZ={7:['Girl with a Pearl Earring'],13:['Berthe Morisot painting'],19:['clothes rack wardrobe'],25:["Georgia O'Keeffe"],31:['Bauhaus Dessau building'],37:['Art Deco facade'],43:['Virginia Woolf'],49:['Yayoi Kusama'],55:['Girl with a Pearl Earring'],61:['Zaha Hadid'],67:['Agnes Varda'],73:['linen fabric'],79:['Sonia Delaunay'],85:['mosaic'],91:['Jane Austen']}


def clean(value):
    return ' '.join(html.unescape(re.sub('<[^>]+>',' ',value or '')).split())


def credit_entities(text,asset):
    """Keep source/licence clickable without displaying long URLs in the post."""
    entities=[]
    def add(label,url):
        pos=text.rfind(label)
        if pos>=0:
            entities.append({'type':'text_link','offset':len(text[:pos].encode('utf-16-le'))//2,
                             'length':len(label.encode('utf-16-le'))//2,'url':url})
    if asset['license'].startswith('CC BY'):
        add(asset['artist'],asset['source_url'])
        part='by-sa' if asset['license'].startswith('CC BY-SA') else 'by'
        add(asset['license'],'https://creativecommons.org/licenses/'+part+'/'+asset['license'].split()[-1]+'/')
    return entities


def commons(params):
    return wiki_api('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode({'action':'query','format':'json','prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1200,**params}))


def extract(result):
    assets=[]
    for page in sorted(result.get('query',{}).get('pages',{}).values(),key=lambda p:p.get('index',0)):
        for info in page.get('imageinfo',[]):
            meta=info.get('extmetadata',{})
            license=clean(meta.get('LicenseShortName',{}).get('value'))
            artist=clean(meta.get('Artist',{}).get('value'))
            image=(info.get('thumburl') or info.get('url','')).split('?')[0]
            if info.get('mime') not in ('image/jpeg','image/png') or not image.startswith(('https://upload.wikimedia.org/','https://thumb.wikimedia.org/')):
                continue
            if not re.fullmatch(r'(?:Public domain|CC0(?: 1\.0)?|CC BY(?:-SA)? [1-4]\.0)',license):
                continue
            if not artist or len(artist)>230:
                continue
            assets.append({'title':page['title'].removeprefix('File:'),'image':image,'artist':artist,'license':license,'source_url':info['descriptionurl'],
                           'description':clean(meta.get('ImageDescription',{}).get('value'))[:700]})
    return assets


def candidates(number):
    path=LOCAL/'photo-candidates-v2'/('LDV-'+str(number).zfill(3)+'.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    assets=[]
    if number in PERSONS:
        name=PERSONS[number]
        result=wiki_api('https://www.wikidata.org/w/api.php?'+urllib.parse.urlencode({'action':'wbsearchentities','search':name,'language':'en','limit':1,'format':'json'}))
        hit=next(iter(result.get('search',[])),None)
        if hit and same_artist(name,hit.get('label','')):
            entity=wiki_api('https://www.wikidata.org/w/api.php?'+urllib.parse.urlencode({'action':'wbgetclaims','entity':hit['id'],'property':'P18','format':'json'}))
            for claim in entity.get('claims',{}).get('P18',[])[:2]:
                file=claim.get('mainsnak',{}).get('datavalue',{}).get('value')
                if file:
                    assets.extend(extract(commons({'titles':'File:'+file})))
    queries=QUIZ.get(number) or TOPICS.get(number) or [PERSONS.get(number,'')]
    if not assets:
        for query in queries:
            assets.extend(extract(commons({'generator':'search','gsrnamespace':6,'gsrsearch':'filetype:bitmap '+query,'gsrlimit':8})))
            if assets:
                break
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(assets,ensure_ascii=False,indent=2),encoding='utf-8')
    return assets


def prepare():
    receipts=json.loads((LOCAL/'mass-receipts.json').read_text(encoding='utf-8'))['published']
    posts={p['id']:p for p in load_posts(LOCAL/'review.md')}
    needed=[r for r in receipts if not r.get('illustrated')]
    plans=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(candidates,int(r['id'][4:])):r for r in needed}
        for future in concurrent.futures.as_completed(jobs):
            receipt=jobs[future]
            try:
                choices=future.result()
            except (RequestFailure,KeyError,ValueError):
                choices=[]
            plan={**receipt,'candidates':choices,'body':posts[receipt['id']].get('caption','')}
            plans.append(plan)
            print(json.dumps({'id':receipt['id'],'choices':[{'title':c['title'],'license':c['license']} for c in choices[:4]]},ensure_ascii=False),flush=True)
    plans.sort(key=lambda p:p['id'])
    (LOCAL/'photo-repair-plan.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'need_photos':len(plans),'have_candidates':sum(bool(p['candidates']) for p in plans)}),flush=True)


def run_articles():
    plans=json.loads((LOCAL/'photo-repair-plan.json').read_text(encoding='utf-8'))
    selections=json.loads((LOCAL/'photo-selections.json').read_text(encoding='utf-8'))
    refresh_path=LOCAL/'photo-refresh.json'
    refresh=set(json.loads(refresh_path.read_text(encoding='utf-8'))) if refresh_path.exists() else set()
    bot,github=credentials()
    store=StateStore(github)
    state=store.load()
    if state.get('pending'):
        raise ValueError('Unconfirmed previous delivery; stop before editing')
    ledger=state['editorial_batches'][BATCH]
    by_id={r['id']:r for r in ledger['published']}
    proofs=[]
    first=LOCAL/'photo-repair-first.json'
    if first.exists():
        proofs.append(json.loads(first.read_text(encoding='utf-8')))
    journal=LOCAL/'photo-repair-journal.jsonl'
    if journal.exists():
        proofs.extend(json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines() if line)
    for proof in proofs:
        receipt=by_id[proof['id']]
        receipt.update(illustrated=True,photo_source_url=proof.get('source_url'),photo_repaired=True)
    if proofs:
        store.save(state,'Reconcile confirmed editorial photo edits')
    for plan in plans:
        pid=plan['id']
        receipt=by_id[pid]
        if plan['kind']!='article' or receipt.get('illustrated') and pid not in refresh or pid not in selections:
            continue
        asset=plan['candidates'][selections[pid]]
        caption=plan['body']+('\n\n'+asset['label'] if asset.get('label') else '')+'\n\nФото: '+asset['artist']+' · '+asset['license']+'.'
        if len(caption)>1024:
            raise ValueError('Photo caption too long '+pid)
        payload={'chat_id':CHANNEL,'message_id':receipt['message_id'],'media':{'type':'photo','media':asset['image'],'caption':caption,'caption_entities':credit_entities(caption,asset)}}
        confirmation='returned_photo'
        try:
            result=api('https://api.telegram.org/bot'+bot+'/editMessageMedia',payload,timeout=40)
        except RequestFailure as error:
            if error.status==400 and 'message is not modified' in error.detail.lower():
                # Repeating this exact edit is safe after a lost response.
                result=None
                confirmation='already_current'
            elif error.status==400:
                print(json.dumps({'failed_photo':pid,'error':str(error)}),flush=True)
                continue
            else:
                raise
        message=result.get('result',{}) if result else {}
        if result and (not result.get('ok') or not message.get('photo') or message.get('message_id')!=receipt['message_id']):
            raise ValueError('No confirmed photo edit '+pid)
        proof={'id':pid,'message_id':receipt['message_id'],'photo':True,'confirmation':confirmation,'source_url':asset['source_url'],'image':asset['image'],'license':asset['license'],'artist':asset['artist'],'file_id':message.get('photo',[{}])[-1].get('file_id')}
        with journal.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(proof,ensure_ascii=False)+'\n')
            handle.flush()
            os.fsync(handle.fileno())
        receipt.update(illustrated=True,photo_source_url=asset['source_url'],photo_repaired=True,photo_credit_links=True)
        store.save(state,'Add verified photo to '+pid)
        (LOCAL/'mass-receipts.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        if pid in refresh:
            refresh.remove(pid)
            refresh_path.write_text(json.dumps(sorted(refresh)),encoding='utf-8')
        print(json.dumps({'photo_added':pid,'message_id':receipt['message_id'],'articles_with_photo':sum(r['kind']=='article' and r.get('illustrated',False) for r in by_id.values())}),flush=True)
        time.sleep(1.1)
    bodies={p['id']:p.get('caption','') for p in load_posts(LOCAL/'review.md')}
    for proof in proofs:
        receipt=by_id[proof['id']]
        if not proof.get('license','').startswith('CC BY') or receipt.get('photo_credit_links'):
            continue
        caption=bodies[proof['id']]+'\n\nФото: '+proof['artist']+' · '+proof['license']+'.'
        try:
            result=api('https://api.telegram.org/bot'+bot+'/editMessageCaption',{'chat_id':CHANNEL,'message_id':receipt['message_id'],'caption':caption,'caption_entities':credit_entities(caption,proof)})
        except RequestFailure as error:
            if error.status==400 and 'message is not modified' in error.detail.lower():
                result=None
            else:
                raise
        if result and (not result.get('ok') or not result.get('result',{}).get('photo')):
            raise ValueError('Credit update not confirmed '+proof['id'])
        receipt['photo_credit_links']=True
        store.save(state,'Complete photograph licence attribution '+proof['id'])
    (LOCAL/'mass-receipts.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'articles_with_photo':sum(r['kind']=='article' and r.get('illustrated',False) for r in by_id.values()),'target_articles':85}),flush=True)


def run_quizzes():
    plans=json.loads((LOCAL/'photo-repair-plan.json').read_text(encoding='utf-8'))
    selections=json.loads((LOCAL/'photo-selections.json').read_text(encoding='utf-8'))
    posts={p['id']:p for p in load_posts(LOCAL/'review.md')}
    bot,github=credentials()
    store=StateStore(github)
    state=store.load()
    if state.get('pending'):
        raise ValueError('Unconfirmed previous delivery; inspect before retrying')
    ledger=state['editorial_batches'][BATCH]
    by_id={r['id']:r for r in ledger['published']}
    for plan in plans:
        pid=plan['id']; receipt=by_id[pid]
        if plan['kind']!='quiz' or pid not in selections:
            continue
        asset=plan['candidates'][selections[pid]]
        if not receipt.get('photo_replaced_poll') or receipt.get('quiz_layout_version')!='balanced-v1':
            credit='Фото: '+asset['artist']+' · '+asset['license']+'.'
            if pid in ('LDV-007','LDV-055'):
                credit='Изображение: Mauritshuis · Public Domain.'
            post={**posts[pid],'image':asset['image'],'photo_credit':credit}
            method,payload=payload_for(post)
            payload['description_entities']=credit_entities(payload['description'],asset)
            state['pending']={'batch':BATCH,'editorial_id':pid,'action':'replace_quiz_with_photo','old_message_id':receipt['message_id'],'attempted_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            store.save(state,'Reserve photo quiz replacement '+pid)
            try:
                result=api('https://api.telegram.org/bot'+bot+'/'+method,payload,timeout=40)
            except RequestFailure as error:
                if error.status and error.status<500:
                    state['pending']=None
                    store.save(state,'Record rejected photo quiz '+pid)
                    print(json.dumps({'failed_quiz_photo':pid,'error':str(error)}),flush=True)
                    continue
                raise
            message=result.get('result',{})
            poll=message.get('poll',{})
            if not result.get('ok') or not message.get('message_id') or poll.get('type')!='quiz' or not poll.get('media',{}).get('photo'):
                raise ValueError('No confirmed quiz with photograph '+pid)
            proof={'id':pid,'old_message_id':receipt['message_id'],'message_id':message['message_id'],'photo':True,'poll_id':poll['id'],'source_url':asset['source_url'],'image':asset['image'],'license':asset['license'],'artist':asset['artist'],'quiz_layout_version':'balanced-v1','correct_option_ids':payload['correct_option_ids'],'options':[o['text'] for o in payload['options']],'shuffle_options':True}
            with (LOCAL/'photo-quiz-journal.jsonl').open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(proof,ensure_ascii=False)+'\n');handle.flush();os.fsync(handle.fileno())
            retired=receipt.get('retired_message_ids',[])
            for old_id in (receipt.get('old_message_id'),receipt['message_id']):
                if old_id and old_id not in retired:retired.append(old_id)
            receipt.update(message_id=message['message_id'],old_message_id=receipt['message_id'],retired_message_ids=retired,illustrated=True,photo_replaced_poll=True,old_poll_deleted=False,photo_source_url=asset['source_url'],poll_id=poll['id'],quiz_layout_version='balanced-v1',correct_option_ids=payload['correct_option_ids'],options=[o['text'] for o in payload['options']],shuffle_options=True)
            state['pending']=None
            store.save(state,'Confirm photo quiz replacement '+pid)
            (LOCAL/'mass-receipts.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        if not receipt.get('old_poll_deleted'):
            reply=api('https://api.telegram.org/bot'+bot+'/deleteMessage',{'chat_id':CHANNEL,'message_id':receipt['old_message_id']})
            if not reply.get('ok') or reply.get('result') is not True:
                raise ValueError('Old poll deletion not confirmed '+pid)
            receipt['old_poll_deleted']=True
            store.save(state,'Remove old imageless quiz '+pid)
            (LOCAL/'mass-receipts.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'quiz_with_photo':pid,'message_id':receipt['message_id'],'old_poll_deleted':receipt['old_poll_deleted']}),flush=True)
        time.sleep(1.1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','articles','quizzes'])
    args=parser.parse_args()
    try:
        {'prepare':prepare,'articles':run_articles,'quizzes':run_quizzes}[args.mode]()
    except (RequestFailure,ValueError) as error:
        print('Stopped safely: '+str(error),file=sys.stderr)
        sys.exit(1)
