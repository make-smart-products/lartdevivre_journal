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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MET = "https://collectionapi.metmuseum.org/public/collection/v1"
REPO = "make-smart-products/lartdevivre_journal"
CHANNEL = "@lartdevivre_journal"
QUERIES = ["Mary Cassatt", "Katsushika Hokusai", "Vincent van Gogh", "dress", "Edgar Degas", "Paul Cezanne", "textile", "Edouard Manet", "Berthe Morisot", "vase", "Albrecht Durer", "landscape"]
ARTISTS = {"Vincent van Gogh": "Винсент ван Гог", "Katsushika Hokusai": "Кацусика Хокусай", "Edgar Degas": "Эдгар Дега", "Paul Cézanne": "Поль Сезанн", "Edouard Manet": "Эдуард Мане", "Mary Cassatt": "Мэри Кассат", "Berthe Morisot": "Берта Моризо", "Albrecht Dürer": "Альбрехт Дюрер"}


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


def make_card(obj):
    if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall"):
        raise PublishError("Нет открытого музейного изображения")
    artist = obj.get("artistDisplayName") or obj.get("culture") or "Автор не указан в каталоге"
    artist = ARTISTS.get(artist, artist)
    title = obj.get("title", "").strip()
    date = obj.get("objectDate", "").strip()
    if not title or not date:
        raise PublishError("Недостаточно подтверждённых сведений")
    # Preserve original catalogue titles instead of inventing translations or facts.
    caption = ("🏛 Музейная находка\n\n" + title + "\n" + artist + " · " + date +
               "\n\nПеред вами произведение из открытой коллекции Метрополитен-музея. "
               "Название и датировка приведены по музейному каталогу. "
               "Уделите изображению минуту: что первым привлекает ваше внимание — цвет, линия или фактура? "
               "Выберите одну деталь и попробуйте описать её тремя словами.\n\n"
               "Источник и изображение: Метрополитен-музей, Public Domain.\n#ИскусствоЖить")
    post = {"topic": title, "caption": caption, "images": [obj["primaryImageSmall"]],
            "source_url": "https://www.metmuseum.org/art/collection/search/" + str(obj["objectID"]),
            "object_id": obj["objectID"]}
    for image in obj.get("additionalImages", []):
        if image not in post["images"] and urllib.parse.urlparse(image).hostname == "images.metmuseum.org":
            post["images"].append(image)
        if len(post["images"]) == 3:
            break
    validate_post(post)
    return post


def choose_post(state, fetch=request):
    used = {int(x) for x in state.get("used_object_ids", [])}
    # Prepared cards have individual editorial captions. Automatic discovery keeps
    # the channel supplied after this queue is consumed, without API subscriptions.
    for path in sorted((ROOT / "cloud" / "queue").glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8-sig"))
        if int(post["object_id"]) not in used:
            obj = fetch(MET + "/objects/" + str(post["object_id"]))
            if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall"):
                continue
            post["images"] = [obj["primaryImageSmall"]]
            validate_post(post)
            return post
    start = len(used) % len(QUERIES)
    for offset in range(len(QUERIES)):
        query = QUERIES[(start + offset) % len(QUERIES)]
        results = fetch(MET + "/search?" + urllib.parse.urlencode({"hasImages": "true", "q": query}))
        ids = [x for x in results.get("objectIDs", []) or [] if int(x) not in used]
        random.Random(len(used)).shuffle(ids)
        for object_id in ids[:20]:
            try:
                return make_card(fetch(MET + "/objects/" + str(object_id)))
            except PublishError:
                continue
    raise PublishError("Не найден новый материал с открытыми официальными изображениями")


def ensure_ready(state, now):
    if state.get("pending"):
        raise PublishError("Есть неподтверждённая отправка: требуется проверка канала, автоповтор запрещён")
    last = state.get("last_published_at")
    if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < 3 * 3600 - 120:
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
    if len(post["images"]) == 1:
        method = "sendPhoto"
        payload = {"chat_id": CHANNEL, "photo": post["images"][0], "caption": post["caption"]}
    else:
        method = "sendMediaGroup"
        media = [{"type": "photo", "media": image} for image in post["images"]]
        media[0]["caption"] = post["caption"]
        payload = {"chat_id": CHANNEL, "media": media}
    reply = fetch("https://api.telegram.org/bot" + token + "/" + method, payload)
    if not reply.get("ok"):
        raise PublishError("Нет подтверждения Telegram")
    results = reply["result"] if isinstance(reply["result"], list) else [reply["result"]]
    if len(results) != len(post["images"]) or any(not x.get("photo") or not x.get("message_id") for x in results):
        raise PublishError("Неполное подтверждение Telegram")
    return [x["message_id"] for x in results]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    store = StateStore(token) if token else None
    state = store.load() if store else json.loads((ROOT / "cloud" / "state.json").read_text(encoding="utf-8-sig"))
    now = dt.datetime.now(dt.timezone.utc)
    if not ensure_ready(state, now) and not args.dry_run:
        print("Ещё не прошло 3 часа после предыдущей публикации.")
        return
    post = choose_post(state)
    if args.dry_run:
        print(json.dumps({"validated": True, "topic": post["topic"], "images": len(post["images"]), "caption": post["caption"]}, ensure_ascii=False))
        return
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not store or not telegram_token:
        raise PublishError("Не настроены секреты облачного запуска")
    state["pending"] = {"object_id": post["object_id"], "attempted_at": now.isoformat(),
                        "post": post, "sha256": hashlib.sha256(json.dumps(post, sort_keys=True).encode()).hexdigest()}
    store.save(state, "Reserve museum publication before Telegram send")
    message_ids = publish(post, telegram_token)
    state.setdefault("used_object_ids", []).append(post["object_id"])
    state["last_published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state.setdefault("publications", []).append({"object_id": post["object_id"], "message_ids": message_ids,
                                               "source_url": post["source_url"], "published_at": state["last_published_at"]})
    state["pending"] = None
    store.save(state, "Record confirmed Telegram publication")
    print("Опубликовано: https://t.me/lartdevivre_journal/" + str(message_ids[0]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except PublishError as error:
        print("Ошибка: " + str(error), file=sys.stderr)
        sys.exit(1)
