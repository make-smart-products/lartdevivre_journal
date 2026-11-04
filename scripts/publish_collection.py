"""Prepare and publish the user's existing 100-item manuscript, with receipts.

Manuscripts/media plans stay local. GitHub holds only progress and confirmed IDs.
Unknown Telegram outcomes are never retried automatically.
"""
import argparse
import concurrent.futures
import copy
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import random
import subprocess
import sys
import time
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud.publish import StateStore, PublishError, MET, MET_SEARCH

LOCAL = ROOT / 'materials' / 'imported'
BATCH = 'ldv-original-100-v1'
CHANNEL = '@lartdevivre_journal'
AIC_LOCK = threading.Lock()
MET_BLOCKED = threading.Event()


def same_artist(query, creator):
    def normalized(value):
        return ''.join(c for c in unicodedata.normalize('NFKD',value.lower()) if not unicodedata.combining(c))
    tokens=[t for t in re.findall(r'[a-z]+',normalized(query)) if len(t)>2 and t not in ('van','von')]
    creator=re.sub(r'[^a-z]','',normalized(creator))
    return bool(tokens) and all(token in creator for token in tokens)


def aic_assets(query, mode):
    path = LOCAL/'asset-cache'/('aic-v4-'+hashlib.sha256((query+mode).encode()).hexdigest()+'.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    fields=['artist_titles'] if mode=='artist' else ['title','style_titles']
    search={'bool':{'must':[{'multi_match':{'query':query,'fields':fields,'operator':'and'}}],
                    'filter':[{'term':{'is_public_domain':True}}]}}
    params={'query':search,'limit':50,
            'fields':'id,title,artist_display,artist_titles,date_display,image_id,is_public_domain,artwork_type_title'}
    try:
        # The official catalogue asks small integrations to use one request/second.
        with AIC_LOCK:
            try:
                data=api('https://api.artic.edu/api/v1/artworks/search?'+urllib.parse.urlencode({'params':json.dumps(params)}))
            finally:
                time.sleep(1.1)
    except RequestFailure:
        return []
    result=[]
    for obj in data.get('data',[]):
        title=obj.get('title') or ''
        artist='; '.join(obj.get('artist_titles') or [])
        kind=(obj.get('artwork_type_title') or '').lower()
        if obj.get('is_public_domain') is not True or not obj.get('image_id'):
            continue
        if mode=='artist':
            if not same_artist(query,artist):
                continue
        if mode=='city' and not all(word in title.lower() for word in query.lower().split()):
            continue
        if mode=='fashion' and not any(w in (title+' '+kind).lower() for w in ('dress','coat','jacket','shoe','boot','bodice','tunic','shirt','skirt','trouser','suit','scarf','shawl','textile','costume','necklace','bag','vest','fashion')):
            continue
        if mode=='architecture' and not any(w in (title+' '+kind).lower() for w in ('architect','interior','chair','lamp','vase','column','temple','carpet','book','room','window','cabinet','furniture')):
            continue
        result.append({'objectID':'aic-'+str(obj['id']),'title':title,
                       'artistDisplayName':(obj.get('artist_display') or '').split('\n')[0],
                       'objectDate':obj.get('date_display'),
                       'primaryImageSmall':'https://www.artic.edu/iiif/2/'+obj['image_id']+'/full/843,/0/default.jpg',
                       'source_url':'https://www.artic.edu/artworks/'+str(obj['id']),
                       'institution':'Чикагский институт искусств · CC0', 'isPublicDomain':True})
        if len(result)==3:
            break
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


class RequestFailure(Exception):
    def __init__(self, status=None, retry_after=None, detail=None):
        self.status = status
        self.retry_after = retry_after
        self.detail = detail or ''
        super().__init__(('HTTP ' + str(status) if status else 'No confirmed response')+(': '+detail if detail else ''))


def api(url, payload=None, timeout=18):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {'User-Agent': 'LArtDeVivreJournal/1.0', 'Accept': 'application/json'}
    if data is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        retry = None
        try:
            retry=int(error.headers.get('Retry-After',''))
        except (TypeError,ValueError):
            pass
        detail = None
        if error.code in (400,429):
            try:
                body=json.loads(error.read())
                retry = body.get('parameters', {}).get('retry_after') or retry
                if urllib.parse.urlsplit(url).hostname=='api.telegram.org':
                    detail=re.sub(r'https?://\S+|\d{5,}:[A-Za-z0-9_-]+','[redacted]',body.get('description',''))[:180]
            except (ValueError, OSError):
                pass
        raise RequestFailure(error.code, retry,detail) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise RequestFailure() from None


def load_posts(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    posts = []
    for match in re.finditer(r'^## (LDV-\d{3}) · (.*?) · (.*?)\n([\s\S]*?)(?=^## LDV-|\Z)', text, re.M):
        pid, category, title, body = match.groups()
        post = {'id': pid, 'category': category, 'title': title}
        if '**Викторина:**' in body:
            question = re.search(r'\*\*Викторина:\*\*\s*(.+)', body).group(1).strip()
            options = re.findall(r'^- \d+\. (.+)$', body, re.M)
            answer = re.search(r'\*\*Ответ:\*\*\s*(.+)', body).group(1).strip()
            correct, explanation = answer.split(' — ', 1)
            if len(options) != 4 or options.count(correct) != 1:
                raise ValueError('Invalid quiz ' + pid)
            post.update(kind='quiz', question=question, options=options,
                        correct_option_ids=[options.index(correct)], explanation=explanation)
        else:
            paragraphs = [x.strip() for x in body.strip().split('\n\n')]
            paragraphs = [x for x in paragraphs if x and not x.startswith('L’ART DE VIVRE')]
            caption = '\n\n'.join(paragraphs)
            post.update(kind='article', caption=caption)
        posts.append(post)
    if [p['id'] for p in posts] != ['LDV-' + str(n).zfill(3) for n in range(1, 101)]:
        raise ValueError('Expected complete manuscript 001–100')
    if sum(p['kind'] == 'quiz' for p in posts) != 15:
        raise ValueError('Expected 85 articles and 15 quizzes')
    # The original pearl-earring article was already published in this channel.
    posts[1].update(title='Вермеер: свет в повседневности', caption='✦ ВЕРМЕЕР: СВЕТ В ПОВСЕДНЕВНОСТИ\n\nВместо уже знакомой «Девушки с жемчужной серёжкой» — другая работа Вермеера: «Молодая женщина с кувшином воды», около 1662 года.\n\nПрисмотритесь к свету у окна, ткани и блеску металла. Простое домашнее действие становится поводом заметить, сколько оттенков скрыто в привычных вещах.')
    return posts


def asset_for(query, mode):
    """Return a few licensed, relevant official images, never a search thumbnail."""
    path = LOCAL / 'asset-cache' / (hashlib.sha256((query + mode).encode()).hexdigest() + '.json')
    if path.exists():
        cached=json.loads(path.read_text(encoding='utf-8'))
        return [obj for obj in cached if mode!='artist' or same_artist(query,obj.get('artistDisplayName',''))]
    if MET_BLOCKED.is_set():
        return aic_assets(query,mode)
    params = {'q': query, 'hasImages': 'true', 'limit': 35}
    if mode == 'artist':
        params['artistOrCulture'] = 'true'
    if mode == 'fashion':
        params['departmentId'] = 8
    try:
        ids = api(MET_SEARCH + '?' + urllib.parse.urlencode(params)).get('objectIDs') or []
    except RequestFailure as error:
        if error.status==403:
            MET_BLOCKED.set()
        return aic_assets(query,mode)
    result = []
    for oid in ids[:16]:
        try:
            obj = api(MET + '/objects/' + str(oid), timeout=10)
        except RequestFailure:
            continue
        image = obj.get('primaryImageSmall', '')
        if not obj.get('isPublicDomain') or not image.startswith('https://images.metmuseum.org/'):
            continue
        kind = (obj.get('objectName', '') + ' ' + obj.get('classification', '')).lower()
        if mode == 'fashion' and obj.get('department') != 'Costume Institute':
            continue
        if mode == 'city' and query.lower() not in obj.get('title', '').lower():
            continue
        if mode == 'artist':
            # Match actual creator, not somebody else's portrait of the artist.
            if not same_artist(query,obj.get('artistDisplayName','')):
                continue
        if mode == 'architecture' and not any(x in (kind + ' ' + obj.get('title', '').lower()) for x in ('architect', 'interior', 'chair', 'lamp', 'vase', 'column', 'temple', 'carpet', 'book', 'room', 'window', 'cabinet')):
            continue
        result.append({k: obj.get(k) for k in ('objectID', 'title', 'artistDisplayName', 'objectDate', 'primaryImageSmall', 'isPublicDomain', 'objectName')})
        if len(result) == 3:
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    if result:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result or aic_assets(query,mode)


ART = {2: 'Johannes Vermeer', 9: 'Berthe Morisot', 16: 'Mary Cassatt', 23: 'Frida Kahlo', 30: 'Sonia Delaunay', 38: "Georgia O'Keeffe", 45: 'Hilma af Klint', 52: 'Tamara de Lempicka', 59: 'Louise Bourgeois', 66: 'Yayoi Kusama', 74: 'Dorothea Lange', 80: 'Lee Miller', 86: 'Kara Walker', 89: 'Artemisia Gentileschi', 93: 'Camille Claudel', 96: 'Agnes Martin', 97: 'Cindy Sherman', 98: 'Paula Rego', 99: 'Elisabeth Louise Vigee Le Brun'}
FASHION = {1:'dress',5:'trousers',8:'dress',12:'dress',15:'vest',20:'denim',22:'shirt',27:'suit',29:'slippers',34:'bag',36:'coat',41:'necklace',44:'dress',48:'jacket',51:'sweater',56:'dress',58:'coat',63:'sunglasses',65:'skirt',70:'sneakers',72:'dress',77:'jacket',78:'suit',83:'evening dress',84:'dress'}
CITIES = {4:'Paris',11:'Florence panorama',18:'Copenhagen',26:'Constantinople view',33:'Kyoto',40:'Lisbon',47:'Mexico City',54:'Tiflis'}
ARCH = {3:'Marcel Breuer',10:'Art Deco',17:'Japanese room',24:'lamp',32:'interior',39:'interior',46:'vase',53:'Zaha Hadid',60:'Eileen Gray',68:'Danish room',75:'interior',81:'carpet',87:'vase',90:'bookcase',94:'furniture'}


def prepare():
    posts = load_posts(LOCAL / 'review.md')
    plans = {}
    for post in posts:
        n = int(post['id'][4:])
        if post['kind'] == 'quiz':
            continue
        if n in ART:
            plans[n] = (ART[n], 'artist')
        elif n in FASHION:
            plans[n] = (FASHION[n], 'fashion')
        elif n in CITIES:
            plans[n] = (CITIES[n], 'city')
        elif n in ARCH:
            plans[n] = (ARCH[n], 'artist' if n in (3,53,60) else 'architecture')
    queries = sorted(set(plans.values()))
    assets = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(asset_for, *query): query for query in queries}
        for future in concurrent.futures.as_completed(jobs):
            query = jobs[future]
            try:
                assets[query] = future.result()
            except Exception:
                assets[query] = []
            print(json.dumps({'prepared_sources':len(assets),'total_sources':len(queries),'query':query[0],'images':len(assets[query])},ensure_ascii=False),flush=True)
    used = set()
    # Reviewed substitutions: catalogue search also finds coats of arms,
    # armour and city names inside caricature titles. These are unsuitable.
    overrides={5:('jacket','fashion',1),12:('dress','fashion',0),
               17:('Japanese room','architecture',0),27:('jacket','fashion',2),
               32:('interior','architecture',2),36:('coat','fashion',2),
               58:('coat','fashion',2),68:('Danish room','architecture',0),
               78:('jacket','fashion',2),83:('evening dress','fashion',1),
               94:('bookcase','architecture',0)}
    without_image={10,22,29,40,56}
    for post in posts:
        n = int(post['id'][4:])
        candidates = assets.get(plans.get(n), [])
        chosen = next((a for a in candidates if a['objectID'] not in used), candidates[0] if candidates else None)
        if n in overrides:
            query,mode,index=overrides[n]
            reviewed=aic_assets(query,mode)
            chosen=reviewed[index] if len(reviewed)>index else None
        if n in without_image:
            chosen=None
        if n == 9:
            chosen=next((a for a in candidates if 'Portraits of Julie' in a['title']),chosen)
        if n == 2:
            # Verified against the museum's official object page (Public Domain).
            chosen = {'objectID':437881,'title':'Young Woman with a Water Pitcher',
                      'artistDisplayName':'Johannes Vermeer','objectDate':'ca. 1662',
                      'primaryImageSmall':'https://collectionapi.metmuseum.org/api/collection/v1/iiif/437881/1476893/main-image'}
            try:
                obj=api(MET+'/objects/437881')
                if obj.get('isPublicDomain') and obj.get('primaryImageSmall'):
                    chosen = obj
            except RequestFailure:
                pass
        if chosen:
            used.add(chosen['objectID'])
            post['image'] = chosen['primaryImageSmall']
            post['object_id'] = chosen['objectID']
            post['source_url'] = chosen.get('source_url') or 'https://www.metmuseum.org/art/collection/search/' + str(chosen['objectID'])
            label = 'Музейная иллюстрация: ' + chosen['title']
            if chosen.get('artistDisplayName'):
                label += ', ' + chosen['artistDisplayName']
            if chosen.get('objectDate'):
                label += ', ' + chosen['objectDate']
            post['caption'] += '\n\n' + label + '.\n'+chosen.get('institution','Метрополитен-музей · Public Domain')+'.'
        if post['kind'] == 'article' and len(post['caption']) > 1024:
            raise ValueError('Caption too long: '+post['id'])
    validate_manifest(posts)
    (LOCAL/'mass-ready.json').write_text(json.dumps(posts,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ready':len(posts),'quizzes':sum(p['kind']=='quiz' for p in posts),'illustrated_articles':sum(bool(p.get('image')) for p in posts)},ensure_ascii=False),flush=True)


def validate_manifest(posts):
    if len(posts) != 100 or len({p['id'] for p in posts}) != 100:
        raise ValueError('Expected 100 unique posts')
    bodies=[]
    for p in posts:
        text=p.get('caption',p.get('question',''))
        if not text or re.search(r'https?://|www\.',text):
            raise ValueError('Empty text or a URL in publication')
        bodies.append(text)
        if p.get('image') and not p['image'].startswith(('https://images.metmuseum.org/','https://www.artic.edu/iiif/2/','https://collectionapi.metmuseum.org/api/collection/v1/iiif/')):
            raise ValueError('Unverified image origin')
    if len(set(bodies)) != 100:
        raise ValueError('Duplicate publication text')


def credentials():
    values={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        m=re.match(r'^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN|TELEGRAM_CHANNEL)\s*=\s*(.*?)\s*$',line)
        if m:values[m[1]]=m[2].strip().strip('"').strip("'")
    if not values.get('TELEGRAM_BOT_TOKEN') or values.get('TELEGRAM_CHANNEL') != CHANNEL:
        raise ValueError('Telegram configuration mismatch')
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
    r=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,env=env)
    if r.returncode:raise ValueError('GitHub login unavailable')
    gh=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    return values['TELEGRAM_BOT_TOKEN'],gh['password']


def payload_for(post):
    base={'chat_id':CHANNEL,'disable_notification':True}
    if post['kind']=='quiz':
        options=list(post['options'])
        correct=list(post['correct_option_ids'])
        if re.fullmatch(r'LDV-\d{3}',post.get('id','')) and len(correct)==1:
            answer=options[correct[0]]
            wrong=[option for index,option in enumerate(options) if index!=correct[0]]
            random.Random(post['id']+post['question']).shuffle(wrong)
            position=((int(post['id'][4:])-7)//6)%len(options)
            wrong.insert(position,answer)
            options=wrong
            correct=[position]
        base.update(question=post['question'],options=[{'text':o} for o in options],type='quiz',is_anonymous=True,correct_option_ids=correct,explanation=post['explanation'],shuffle_options=True)
        if post.get('image'):
            base['media']={'type':'photo','media':post['image']}
            if post.get('photo_credit'):
                base['description']=post['photo_credit']
        return 'sendPoll',base
    if post.get('image'):
        base.update(photo=post['image'],caption=post['caption'])
        return 'sendPhoto',base
    base.update(text=post['caption'],link_preview_options={'is_disabled':True})
    return 'sendMessage',base


def run():
    posts=json.loads((LOCAL/'mass-ready.json').read_text(encoding='utf-8'))
    validate_manifest(posts)
    bot,github=credentials()
    store=StateStore(github)
    state=store.load()
    if state.get('pending'):
        raise ValueError('Unconfirmed previous send; inspect channel before retrying')
    ledger=state.setdefault('editorial_batches',{}).setdefault(BATCH,{'target':100,'published':[]})
    completed={x['id'] for x in ledger['published']}
    if any(not post.get('image') for post in posts if post['id'] not in completed):
        raise ValueError('Every unpublished material must have a verified photo')
    print(json.dumps({'already_confirmed':len(completed),'target':100}),flush=True)
    for post in posts:
        if post['id'] in completed:continue
        method,payload=payload_for(post)
        state['pending']={'batch':BATCH,'editorial_id':post['id'],'sha256':hashlib.sha256(json.dumps(post,sort_keys=True).encode()).hexdigest(),'attempted_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        store.save(state,'Reserve editorial '+post['id'])
        endpoint='https://api.telegram.org/bot'+bot+'/'+method
        for attempt in range(4):
            try:
                response=api(endpoint,payload,timeout=40)
                break
            except RequestFailure as error:
                if error.status==429 and error.retry_after and error.retry_after<=60 and attempt<3:
                    time.sleep(error.retry_after+1)
                    continue
                # Explicit HTTP failure means Telegram rejected this request.
                # A timeout/network failure is ambiguous and retains pending.
                if error.status and error.status<500:
                    state['pending']=None
                    store.save(state,'Record rejected editorial request '+post['id'])
                raise
        if not response.get('ok') or not response.get('result',{}).get('message_id'):
            raise ValueError('Telegram confirmation missing')
        message=response['result']
        if method=='sendPhoto' and not message.get('photo') or method=='sendPoll' and not message.get('poll',{}).get('media',{}).get('photo'):
            raise ValueError('Telegram did not confirm the required photo')
        receipt={'id':post['id'],'title':post['title'],'kind':post['kind'],'message_id':message['message_id'],'date':message['date'],'illustrated':bool(message.get('photo'))}
        # Persist the successful response before a subsequent GitHub write can fail.
        with (LOCAL/'delivery-journal.jsonl').open('a',encoding='utf-8') as journal:
            journal.write(json.dumps({'batch':BATCH,'sha256':state['pending']['sha256'],**receipt},ensure_ascii=False)+'\n')
            journal.flush()
            os.fsync(journal.fileno())
        ledger['published'].append(receipt)
        completed.add(post['id'])
        if isinstance(post.get('object_id'),int) and post['object_id'] not in state.setdefault('used_object_ids',[]):
            state['used_object_ids'].append(post['object_id'])
        state['pending']=None
        store.save(state,'Confirm editorial '+post['id'])
        (LOCAL/'mass-receipts.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'confirmed':len(completed),'target':100,'id':post['id'],'message_id':message['message_id']},ensure_ascii=False),flush=True)
        time.sleep(1.2)
    print(json.dumps({'status':'complete','confirmed':len(completed),'first':ledger['published'][0]['message_id'],'last':ledger['published'][-1]['message_id']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','publish'])
    args=parser.parse_args()
    try:
        prepare() if args.mode=='prepare' else run()
    except (ValueError,PublishError,RequestFailure) as error:
        print('Stopped safely: '+str(error),file=sys.stderr)
        sys.exit(1)
