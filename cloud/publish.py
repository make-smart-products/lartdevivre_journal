"""Publish a source-backed museum card without paid generation services.

GitHub state is saved BEFORE Telegram is called. An uncertain send is never retried
automatically. Only trusted, public-domain museum images are eligible.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MET = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
REPO = "make-smart-products/lartdevivre_journal"
CHANNEL = "@lartdevivre_journal"
DAILY_TARGET = 100
QUERIES = ["Vincent van Gogh", "Edgar Degas", "Paul Cezanne", "Edouard Manet", "Berthe Morisot", "Rembrandt", "landscape", "still life", "garden", "seascape", "interior", "flowers"]
RESERVE_OBJECT_IDS = {"painting": [436531, 436533, 437423, 437430]}
BASE_RUBRIC_ORDER = ["fashion", "architecture", "travel", "women", "painting"]
# A twenty-slot cycle: 15 per core topic, 10 books/cinema and 15 quizzes per 100.
RUBRIC_ORDER = (BASE_RUBRIC_ORDER + ["books", "quiz"]) * 2 + BASE_RUBRIC_ORDER + ["quiz"]
RUBRICS = {
    "fashion": {"label": "👗 История моды", "tag": "#ИсторияМоды", "queries": ["evening dress", "robe", "coat", "hat", "House of Worth"]},
    "architecture": {"label": "🏛 Архитектура и интерьер", "tag": "#Архитектура", "queries": ["temple", "architectural", "column", "facade", "floor plan", "cathedral", "interior", "chair", "cabinet"]},
    "travel": {"label": "🧳 Культурные путешествия", "tag": "#КультурныеПутешествия", "queries": ["Venice", "Rome", "Paris", "Florence", "Kyoto", "Istanbul", "Amsterdam", "London", "Naples", "New York"]},
    "women": {"label": "🎨 Женщины в искусстве", "tag": "#ЖенщиныВИскусстве", "queries": ["Berthe Morisot", "Mary Cassatt", "Rosa Bonheur", "Judith Leyster", "Elisabeth Louise Vigee Le Brun", "Artemisia Gentileschi", "Adelaide Labille-Guiard", "Rachel Ruysch", "Clara Peeters", "Eva Gonzales", "Marie Bracquemond", "Fidelia Bridges", "Edmonia Lewis", "Harriet Hosmer", "Camille Claudel", "Paula Modersohn-Becker", "Lilla Cabot Perry", "Elizabeth Nourse", "Cecilia Beaux", "Margaret Sarah Carpenter"]},
    "painting": {"label": "🖼 Картина дня", "tag": "#КартинаДня"},
    "books": {"label": "📚 Книги, кино и визуальные истории", "tag": "#КнигиИКино", "queries": ["Shakespeare", "Tennyson", "Hamlet", "King Lear", "illustrated book", "theater"]},
    "quiz": {"label": "❓ Визуальная викторина", "tag": "#Викторина", "queries": ["painting", "photograph", "engraving", "sculpture"]},
}
ARTISTS = {"Vincent van Gogh": "Винсент ван Гог", "Katsushika Hokusai": "Кацусика Хокусай", "Edgar Degas": "Эдгар Дега", "Paul Cézanne": "Поль Сезанн", "Edouard Manet": "Эдуард Мане", "Mary Cassatt": "Мэри Кассат", "Berthe Morisot": "Берта Моризо", "Albrecht Dürer": "Альбрехт Дюрер"}
MEDIUM_LABELS = {
    "oil on canvas": "Масло на холсте", "oil on wood": "Масло на дереве",
    "etching": "Офорт", "engraving": "Гравюра резцом", "lithograph": "Литография",
    "woodcut": "Ксилография", "cyanotype": "Цианотипия",
    "silk": "Шёлк", "cotton": "Хлопок", "wool": "Шерсть", "linen": "Лён",
    "bronze": "Бронза", "marble": "Мрамор", "watercolor": "Акварель",
}
EXTRA_ANGLES = {
    "fashion": ["Рассмотрите застёжки и швы. По ним можно понять, как устроена вещь и где она держит форму.",
                "Идея для примерки: повторить пропорции силуэта с вещами, которые уже есть дома."],
    "architecture": ["Посмотрите, как проёмы и пустые участки меняют ощущение масштаба.",
                     "Представьте этот предмет или фрагмент в обычной комнате. Что изменится, если перенести его в другой угол?"],
    "travel": ["Выберите на изображении одну точку, откуда вы бы начали прогулку по этому историческому виду.",
               "На старом изображении города интересно сравнить масштаб зданий и пространство между ними."],
    "women": ["Присмотритесь к материалу и следам работы. С них можно начать разговор о выборе художницы.",
              "Поставьте рядом другую работу этой художницы. Что меняется от одного сюжета к другому?"],
    "painting": ["На минуту прикройте самый яркий участок. Что теперь первым притягивает взгляд?",
                 "Выберите два участка, между которыми взгляд движется сам. Из них можно собрать небольшую зарисовку."],
    "books": ["Для книжного клуба: придумайте к этому изображению подпись из одной строки и сравните версии.",
              "Вообразите изображение первым кадром фильма. Какой звук подошёл бы сцене?"],
}


class PublishError(Exception):
    pass


def request(url, payload=None, token=None, method=None):
    headers = {"User-Agent": "LArtDeVivreJournal/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Neither exception strings nor response bodies are printed: Telegram URLs
        # contain credentials, and untrusted services can echo request contents.
        raise PublishError("Сервис вернул HTTP " + str(error.code)) from None
    except (OSError, ValueError):
        raise PublishError("Сервис недоступен или вернул некорректный ответ") from None


def validate_post(post):
    caption = post.get("caption", "")
    if not caption.strip() or len(caption) > 1024 or re.search(r"https?://|www\.|t\.me/", caption, re.I):
        raise PublishError("Нужна короткая подпись без ссылок")
    photos = post.get("images", [])
    if not 1 <= len(photos) <= 3:
        raise PublishError("Нужны 1–3 изображения")
    for photo in photos:
        if urllib.parse.urlparse(photo).hostname != "images.metmuseum.org" or not photo.startswith("https://"):
            raise PublishError("Изображение должно происходить из официальной коллекции")
    if not post.get("source_url", "").startswith("https://www.metmuseum.org/art/collection/search/"):
        raise PublishError("Отсутствует официальный первоисточник")
    if post.get("kind") == "quiz":
        options = post.get("options", [])
        correct = post.get("correct_option_ids", [])
        if not 2 <= len(options) <= 10 or len(set(options)) != len(options) or len(correct) != 1 or not isinstance(correct[0], int) or not 0 <= correct[0] < len(options):
            raise PublishError("Неверные варианты или ответ викторины")
        if len(photos) != 1 or not post.get("question") or len(post["question"]) > 300 or len(post.get("explanation", "")) > 200:
            raise PublishError("Неверный формат викторины с фото")


def matches_rubric(obj, rubric):
    kind = (obj.get("objectName", "") + " " + obj.get("classification", "")).lower()
    title = obj.get("title", "").lower()
    if rubric == "women":
        # A portrait OF a woman is not necessarily a work BY a woman.
        return obj.get("artistGender", "").lower() == "female"
    if rubric == "fashion":
        return obj.get("department") == "Costume Institute" and any(word in kind for word in ("dress", "robe", "coat", "gown", "hat", "shoe", "jacket", "ensemble", "suit"))
    if rubric == "architecture":
        return bool(re.search(r"\b(chair|table|cabinet|sofa|furniture|interior)\b", kind)) or any(word in kind + " " + title for word in ("temple", "capital", "column", "architectural", "floor plan", "facade", "façade", "elevation", "portal", "church", "cathedral", "palace"))
    if rubric == "travel":
        visual = any(word in kind for word in ("painting", "watercolor", "print", "drawing", "photograph"))
        places = [place.casefold() for place in RUBRICS["travel"]["queries"]]
        has_place = any(re.search(r"\b" + re.escape(place) + r"\b", title) for place in places)
        # Florence can be a person's given name. A place word alone is not evidence
        # that a portrait is a city view; require an explicit topographical context.
        context = re.search(r"\b(view|landscape|cityscape|canal|river|bridge|street|square|piazza|palace|cathedral|temple|church|harbou?r|port|coast|bay|island|gardens?|panorama|giudecca|lagoon|quay|boulevard|tower|rooftops?|seine|arno|gion)\b", title)
        exact_place = title.strip(' []().') in places
        portrait = re.search(r"\b(portrait|madame|mademoiselle|mrs|miss|self-portrait)\b", title)
        return bool(visual and has_place and (context or exact_place) and not portrait)
    if rubric == "books":
        return any(word in title + " " + kind for word in ("shakespeare", "tennyson", "hamlet", "king lear", "illustrated book", "theater", "theatre", "cinema"))
    if rubric == "quiz":
        return bool(quiz_medium(obj))
    return any(word in kind for word in ("painting", "watercolor"))


def content_signature(obj):
    creator = re.sub(r"\s+", " ", obj.get("artistDisplayName", "").strip().casefold())
    medium = (obj.get("medium", "") + " " + obj.get("objectName", "")).casefold()
    if "cyanotype" in medium:
        family = "cyanotype"
    elif "photograph" in obj.get("classification", "").casefold() and any(word in obj.get("title", "").casefold() for word in ("portrait", "study", "mrs", "miss")):
        family = "photographic-portrait"
    else:
        family = ""
    return {"creator": creator, "visual_family": family, "image": obj.get("primaryImageSmall", ""),
            "work_key": creator + "|" + obj.get("title", "").strip().casefold()}


def diverse_enough(post, state):
    recent = state.get("recent_content", [])[-100:]
    if any(post.get("images", [None])[0] == item.get("image") for item in recent):
        return False
    if post.get("work_key") and any(post["work_key"] == item.get("work_key") for item in recent):
        return False
    if post.get("visual_family") == "cyanotype" and any(item.get("visual_family") == "cyanotype" for item in recent):
        return False
    creator = post.get("creator")
    if creator and sum(item.get("creator") == creator for item in recent) >= 2:
        return False
    if creator and recent and recent[-1].get("creator") == creator:
        return False
    return True


def quiz_medium(obj):
    kind = (obj.get("objectName", "") + " " + obj.get("classification", "")).casefold()
    for words, answer in [(("photograph",), "Фотография"), (("painting",), "Живопись"),
                          (("print", "engraving", "etching"), "Печатная графика"),
                          (("sculpture", "bust"), "Скульптура")]:
        if any(word in kind for word in words):
            return answer
    return None


def least_recent_variant(choices, recent, field, seed):
    counts = {choice: 0 for choice in choices}
    last = None
    for item in recent:
        value = item.get(field)
        if value in counts:
            counts[value] += 1
            last = value
    order = choices[seed % len(choices):] + choices[:seed % len(choices)]
    return min(order, key=lambda choice: (counts[choice], choice == last))


def make_quiz_question(obj, recent=()):
    answer = quiz_medium(obj)
    if not answer:
        raise PublishError("Нет проверяемого ответа для викторины")
    variants = {
        "medium": ("К какому виду искусства относится показанная работа?",
                   ["Фотография", "Живопись", "Печатная графика", "Скульптура"],
                   answer, "Вид искусства в музейном каталоге — " + answer.lower() + "."),
    }
    creator = (obj.get("artistDisplayName") or "").strip()
    if 2 <= len(creator) <= 100 and not re.search(r"unknown|anonymous|unidentified|attributed|workshop|school of|after ", creator, re.I):
        wrong = [name for name in ("Claude Monet", "Berthe Morisot", "Katsushika Hokusai", "Mary Cassatt", "Edgar Degas", "Albrecht Dürer") if name.casefold() != creator.casefold()]
        random.Random(str(obj["objectID"])).shuffle(wrong)
        variants["artist"] = ("Кто автор показанной работы?", [creator] + wrong[:3], creator,
                              "Автор — " + creator + ".")
    begin, end = obj.get("objectBeginDate"), obj.get("objectEndDate")
    if isinstance(begin, int) and isinstance(end, int) and 0 < begin <= end and (begin - 1) // 100 == (end - 1) // 100:
        century = (begin - 1) // 100 + 1
        start = max(1, century - 1)
        answer = str(century) + "-й век"
        variants["century"] = ("В каком веке создана показанная работа?",
                               [str(value) + "-й век" for value in range(start, start + 4)], answer,
                               "Музейная датировка — " + obj.get("objectDate", "") + ". Это " + answer + ".")
    techniques = {"etching": "Офорт", "engraving": "Гравюра резцом", "lithograph": "Литография",
                  "woodcut": "Ксилография"}
    technique = techniques.get((obj.get("medium") or "").strip().casefold())
    if technique:
        variants["technique"] = ("Какой техникой выполнен этот оттиск?",
                                 list(techniques.values()), technique,
                                 "В музейном каталоге указана техника «" + technique.lower() + "».")
    choices = list(variants)
    preferred = ("medium", "artist", "century")[int(obj["objectID"]) % 3]
    seed = choices.index(preferred) if preferred in choices else 0
    quiz_format = least_recent_variant(choices, [item for item in recent if item.get("quiz_format")][-12:], "quiz_format", seed)
    question, options, answer, explanation = variants[quiz_format]
    title = obj.get("title", "").strip()
    if title and len(explanation) + len(title) + 3 <= 200 and quiz_format != "artist":
        explanation += " «" + title + "»."
    random.Random(str(obj["objectID"])).shuffle(options)
    return {"kind": "quiz", "quiz_format": quiz_format, "question": question, "options": options,
            "correct_option_ids": [options.index(answer)], "explanation": explanation[:200]}


def make_card(obj, rubric="painting", recent=()):
    if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall"):
        raise PublishError("Нет открытого музейного изображения")
    artist = obj.get("artistDisplayName") or obj.get("culture") or "Автор не указан в каталоге"
    artist = ARTISTS.get(artist, artist)
    title = obj.get("title", "").strip()
    date = obj.get("objectDate", "").strip()
    if not title or not date:
        raise PublishError("Недостаточно подтверждённых сведений")
    try:
        if int(obj.get("artistBeginDate") or 0) > 1500 and int(obj.get("objectEndDate") or 9999) < 1500:
            raise PublishError("Датировку оригинала и современной копии должен проверить редактор")
    except (ValueError, TypeError):
        pass
    # Preserve original catalogue titles instead of inventing translations or facts.
    config = RUBRICS[rubric]
    prompts = {
        "fashion": ["Идея для своего гардероба: выбрать одну деталь исторической вещи, которую можно переосмыслить сегодня.", "Силуэт или отделка? Что вам хотелось бы позаимствовать у этого предмета, сохранив собственный стиль?", "Сохраните как вдохновение для сочетания фактур — без необходимости повторять исторический наряд."],
        "architecture": ["Идея для следующей прогулки: искать ритм окон, колонн и свободных промежутков.", "Поменяйте масштаб взгляда: сначала общая форма, затем одна конструктивная деталь.", "Что можно взять для своего пространства: ритм, пропорции или сочетание материалов?"],
        "travel": ["Исторический образ места, не современная туристическая рекомендация. Повод узнать город через взгляд художника.", "Вдохновение для неспешной прогулки: один вид вместо длинного списка достопримечательностей. Перед вами историческое изображение.", "Сравните с образом города, который хранится в вашей памяти. Это историческая работа, а не фотография сегодняшнего маршрута."],
        "women": ["Работа художницы — повод познакомиться с её собственным визуальным языком.", "В личную коллекцию вдохновения: работа женщины, чьё авторство указано в музейном каталоге.", "Начнём знакомство с художницей с одной работы, без готового списка обязательных впечатлений."],
        "painting": ["Идея для личного мудборда: взять из картины два оттенка и одну небольшую деталь.", "Можно сохранить ради палитры, необычного ракурса или просто настроения — свой выбор не нужно оправдывать.", "Небольшая творческая пауза: выберите фрагмент, который хотелось бы зарисовать или описать."],
        "books": ["Книжная история получает визуальный образ. Что вы представляли иначе, когда читали?", "Идея для книжного клуба: сопоставить текст и изображение, не выбирая единственное правильное прочтение.", "От текста к кадру: какие решения художника меняют ваше представление о знакомой истории?"],
        "quiz": ["Проверьте наблюдательность: ответ подтверждён музейным каталогом."],
    }
    angles = prompts[rubric] + EXTRA_ANGLES.get(rubric, [])
    recent_in_rubric = [item for item in recent if item.get("rubric") == rubric][-8:]
    angle = least_recent_variant(list(range(len(angles))), recent_in_rubric, "angle", int(obj["objectID"]))
    prompt = angles[angle]
    medium = MEDIUM_LABELS.get((obj.get("medium") or "").strip().casefold())
    catalogue_line = artist + " · " + date
    if medium:
        catalogue_line += " · " + medium
    caption = (config["label"] + "\n\n" + title + "\n" + catalogue_line +
               "\n\n" + prompt + "\n\nИсточник и изображение: Метрополитен-музей, Public Domain.\n" + config["tag"])
    post = {"topic": title, "caption": caption, "images": [obj["primaryImageSmall"]],
            "source_url": "https://www.metmuseum.org/art/collection/search/" + str(obj["objectID"]),
            "object_id": obj["objectID"], "rubric": rubric, "angle": angle}
    post.update(content_signature(obj))
    if rubric == "quiz":
        post.update(make_quiz_question(obj, recent))
    for image in obj.get("additionalImages", []):
        if image not in post["images"] and urllib.parse.urlparse(image).hostname == "images.metmuseum.org":
            post["images"].append(image)
        if len(post["images"]) == 3:
            break
    if rubric == "quiz":
        post["images"] = post["images"][:1]
    validate_post(post)
    return post


def snapshot_object(post, now=None):
    """Use a recently checked official catalogue response stored with a reviewed card."""
    obj = post.get("verified_object")
    if post.get("editorial_reviewed") is not True or not isinstance(obj, dict):
        return None
    try:
        checked = dt.datetime.fromisoformat(post["source_verified_at"])
        photo_checked = dt.datetime.fromisoformat(post["photo_verified_at"])
        current = now or dt.datetime.now(dt.timezone.utc)
        for stamp in (checked, photo_checked):
            if stamp.tzinfo is None or not dt.timedelta(0) <= current - stamp <= dt.timedelta(days=7):
                return None
        if obj.get("objectID") != post.get("object_id") or obj.get("isPublicDomain") is not True:
            return None
        if post.get("source_url") != "https://www.metmuseum.org/art/collection/search/" + str(obj["objectID"]):
            return None
        approved = [obj.get("primaryImageSmall")] + obj.get("additionalImages", [])
        if not post.get("images") or any(image not in approved for image in post["images"]):
            return None
        if hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest() != post.get("catalogue_sha256"):
            return None
        return obj
    except (KeyError, TypeError, ValueError):
        return None


def choose_post(state, fetch=request, rubric=None):
    rubric = rubric or RUBRIC_ORDER[state.get("next_rubric_index", 0) % len(RUBRIC_ORDER)]
    used = {int(x) for x in state.get("used_object_ids", [])}
    recent = state.get("recent_content", [])
    # Prepared cards have individual editorial captions. Automatic discovery keeps
    # the channel supplied after this queue is consumed, without API subscriptions.
    for path in sorted((ROOT / "cloud" / "queue").glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8-sig"))
        if post.get("rubric", "painting") == rubric and int(post["object_id"]) not in used:
            try:
                obj = snapshot_object(post) or fetch(MET + "/objects/" + str(post["object_id"]))
            except PublishError:
                continue
            if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall") or not matches_rubric(obj, rubric):
                continue
            approved = [obj["primaryImageSmall"]] + obj.get("additionalImages", [])
            selected = [image for image in post.get("images", []) if image in approved]
            post["images"] = selected[:3] or [obj["primaryImageSmall"]]
            post["rubric"] = rubric
            post.update(content_signature(obj))
            validate_post(post)
            if diverse_enough(post, state):
                return post
    # Known catalogue IDs provide a small reserve when broad search is noisy.
    # Every candidate still needs live metadata, public-domain rights and a new ID.
    for object_id in RESERVE_OBJECT_IDS.get(rubric, []):
        if object_id in used:
            continue
        try:
            obj = fetch(MET + "/objects/" + str(object_id))
            if matches_rubric(obj, rubric):
                card = make_card(obj, rubric, recent)
                if diverse_enough(card, state):
                    return card
        except PublishError:
            continue
    queries = RUBRICS[rubric].get("queries", QUERIES)
    week = dt.datetime.now(dt.timezone.utc).date().toordinal() // 7
    start = (len(used) + week) % len(queries)
    for offset in range(len(queries)):
        query = queries[(start + offset) % len(queries)]
        for offset_in_results in range(0, 300, 100):
            try:
                params = {"hasImages": "true", "q": query, "limit": 100, "offset": offset_in_results}
                if rubric == "painting":
                    params["departmentId"] = 11  # European Paintings; exclude costume and object matches.
                results = fetch(MET_SEARCH + "?" + urllib.parse.urlencode(params))
            except PublishError:
                break
            ids = [x for x in results.get("objectIDs", []) or [] if int(x) not in used]
            random.Random(len(used)).shuffle(ids)
            for object_id in ids[:20]:
                try:
                    obj = fetch(MET + "/objects/" + str(object_id))
                    if matches_rubric(obj, rubric):
                        card = make_card(obj, rubric, recent)
                        if diverse_enough(card, state):
                            return card
                except PublishError:
                    continue
            if offset_in_results + 100 >= results.get("total", 0):
                break
    raise PublishError("Не найден новый материал рубрики " + RUBRICS[rubric]["label"] + " с открытыми официальными изображениями")


def ensure_ready(state, now):
    if state.get("pending"):
        raise PublishError("Есть неподтверждённая отправка: требуется проверка канала, автоповтор запрещён")
    last = state.get("last_published_at")
    # A delayed GitHub run must not cause the next scheduled slot to be skipped.
    # Suppress duplicate manual/watchdog runs in the same hourly UTC slot.
    if last and int(now.timestamp()) // 3600 <= int(dt.datetime.fromisoformat(last).timestamp()) // 3600:
        return False
    return True


class StateStore:
    def __init__(self, token):
        self.token = token
        self.url = "https://api.github.com/repos/" + REPO + "/contents/cloud/state.json"

    def load(self):
        result = request(self.url + "?ref=main", token=self.token)
        self.sha = result["sha"]
        return json.loads(base64.b64decode(result["content"]))

    def save(self, state, message):
        content = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
        reply = request(self.url, {"message": message, "sha": self.sha, "branch": "main",
                                 "content": base64.b64encode(content).decode()}, token=self.token, method="PUT")
        self.sha = reply["content"]["sha"]


def publish(post, token, fetch=request):
    validate_post(post)
    if post.get("kind") == "quiz":
        method = "sendPoll"
        payload = {"chat_id": CHANNEL, "question": post["question"],
                   "options": [{"text": option} for option in post["options"]],
                   "type": "quiz", "correct_option_ids": post["correct_option_ids"],
                   "explanation": post["explanation"], "shuffle_options": True,
                   "media": {"type": "photo", "media": post["images"][0]},
                   "description": "Изображение: Метрополитен-музей · Public Domain.", "disable_notification": True}
    elif len(post["images"]) == 1:
        method = "sendPhoto"
        payload = {"chat_id": CHANNEL, "photo": post["images"][0], "caption": post["caption"], "disable_notification": True}
    else:
        method = "sendMediaGroup"
        media = [{"type": "photo", "media": image} for image in post["images"]]
        media[0]["caption"] = post["caption"]
        payload = {"chat_id": CHANNEL, "media": media, "disable_notification": True}
    reply = fetch("https://api.telegram.org/bot" + token + "/" + method, payload)
    if not reply.get("ok"):
        raise PublishError("Нет подтверждения Telegram")
    if method == "sendPoll":
        message = reply.get("result", {})
        if not message.get("message_id") or message.get("poll", {}).get("type") != "quiz" or not message.get("poll", {}).get("media", {}).get("photo"):
            raise PublishError("Telegram не подтвердил викторину с фото")
        return [message["message_id"]]
    results = reply["result"] if isinstance(reply["result"], list) else [reply["result"]]
    if len(results) != len(post["images"]) or any(not x.get("photo") or not x.get("message_id") for x in results):
        raise PublishError("Неполное подтверждение Telegram")
    return [x["message_id"] for x in results]


def execute_one(dry_run=False, batch_date=None):
    token = os.environ.get("JOURNAL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    store = StateStore(token) if token else None
    state = store.load() if store else json.loads((ROOT / "cloud" / "state.json").read_text(encoding="utf-8-sig"))
    now = dt.datetime.now(dt.timezone.utc)
    if batch_date:
        if state.get("pending"):
            raise PublishError("Есть неподтверждённая отправка: требуется проверка канала")
        if state.get("daily_batches", {}).get(batch_date, {}).get("completed", 0) >= DAILY_TARGET:
            return {"status": "batch_complete"}
    elif not ensure_ready(state, now) and not dry_run:
        return {"status": "already_published"}
    post = choose_post(state)
    if dry_run:
        telegram_verified = False
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if telegram_token:
            endpoint = "https://api.telegram.org/bot" + telegram_token
            me = request(endpoint + "/getMe", {})
            if not me.get("ok") or not me.get("result", {}).get("id"):
                raise PublishError("Telegram не подтвердил токен бота")
            member = request(endpoint + "/getChatMember", {"chat_id": CHANNEL, "user_id": me["result"]["id"]})
            rights = member.get("result", {})
            if not member.get("ok") or not (rights.get("status") == "creator" or rights.get("status") == "administrator" and rights.get("can_post_messages")):
                raise PublishError("У бота нет права публиковать в канале")
            telegram_verified = True
        return {"validated": True, "telegram_verified": telegram_verified, "topic": post["topic"], "images": len(post["images"]), "caption": post["caption"]}
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not store or not telegram_token:
        raise PublishError("Не настроены секреты облачного запуска")
    state["pending"] = {"object_id": post["object_id"], "attempted_at": now.isoformat(),
                        "post": post, "batch_date": batch_date, "sha256": hashlib.sha256(json.dumps(post, sort_keys=True).encode()).hexdigest()}
    store.save(state, "Reserve museum publication before Telegram send")
    message_ids = publish(post, telegram_token)
    state.setdefault("used_object_ids", []).append(post["object_id"])
    state["last_published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state.setdefault("publications", []).append({"object_id": post["object_id"], "message_ids": message_ids,
                                               "source_url": post["source_url"], "published_at": state["last_published_at"]})
    state["next_rubric_index"] = (state.get("next_rubric_index", 0) + 1) % len(RUBRIC_ORDER)
    state["publications"][-1]["rubric"] = post["rubric"]
    state.setdefault("recent_content", []).append({key: post[key] for key in
        ("creator", "visual_family", "image", "work_key", "rubric", "angle", "quiz_format") if key in post})
    state["recent_content"] = state["recent_content"][-100:]
    if batch_date:
        batch = state.setdefault("daily_batches", {}).setdefault(batch_date, {"completed": 0, "target": DAILY_TARGET})
        batch["target"] = DAILY_TARGET
        batch["completed"] += 1
        state["publications"][-1]["batch_date"] = batch_date
    state["pending"] = None
    store.save(state, "Record confirmed Telegram publication")
    return {"status": "published", "message_ids": message_ids, "url": "https://t.me/lartdevivre_journal/" + str(message_ids[0])}


def execute(dry_run=False, now=None, scheduled=False):
    """One daily block; confirmed items survive interruptions and retries."""
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone(dt.timedelta(hours=3)))
    day = local.date().isoformat()
    if dry_run:
        preview = execute_one(dry_run=True, batch_date=day)
        token = os.environ.get("JOURNAL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        state = StateStore(token).load() if token else json.loads((ROOT / "cloud" / "state.json").read_text(encoding="utf-8-sig"))
        audit = {}
        for rubric in RUBRICS:
            card = choose_post(state, rubric=rubric)
            audit[rubric] = {"validated": True, "topic": card["topic"], "images": len(card["images"]), "kind": card.get("kind", "article")}
        return {"daily_target": DAILY_TARGET, "time_moscow": "22:30", "preview": preview, "rubrics": audit}
    if (local.hour, local.minute) < (22, 30):
        if not scheduled:
            return {"status": "not_due", "time_moscow": "22:30"}
        day = (local.date() - dt.timedelta(days=1)).isoformat()
    results = []
    for _ in range(DAILY_TARGET):
        result = execute_one(batch_date=day)
        if result.get("status") != "published":
            return {"status": result["status"], "published_this_run": len(results), "date_moscow": day}
        results.append(result)
        if len(results) < DAILY_TARGET:
            time.sleep(1.1)
    return {"status": "batch_complete", "published_this_run": len(results), "date_moscow": day, "posts": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(dry_run=args.dry_run, scheduled=args.scheduled), ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except PublishError as error:
        print("Ошибка: " + str(error), file=sys.stderr)
        sys.exit(1)
