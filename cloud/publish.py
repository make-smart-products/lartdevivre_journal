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
MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
REPO = "make-smart-products/lartdevivre_journal"
CHANNEL = "@lartdevivre_journal"
QUERIES = ["Mary Cassatt", "Katsushika Hokusai", "Vincent van Gogh", "dress", "Edgar Degas", "Paul Cezanne", "textile", "Edouard Manet", "Berthe Morisot", "vase", "Albrecht Durer", "landscape"]
RUBRIC_ORDER = ["fashion", "architecture", "travel", "women", "painting"]
RUBRICS = {
    "fashion": {"label": "👗 История моды", "tag": "#ИсторияМоды", "queries": ["evening dress", "robe", "coat", "hat", "House of Worth"]},
    "architecture": {"label": "🏛 Архитектура", "tag": "#Архитектура", "queries": ["temple", "architectural", "column", "facade", "floor plan", "cathedral"]},
    "travel": {"label": "🧳 Культурные путешествия", "tag": "#КультурныеПутешествия", "queries": ["Venice", "Rome", "Paris", "Florence", "Kyoto", "Istanbul"]},
    "women": {"label": "🎨 Женщины в искусстве", "tag": "#ЖенщиныВИскусстве", "queries": ["Berthe Morisot", "Mary Cassatt", "Rosa Bonheur", "Judith Leyster", "Elisabeth Louise Vigee Le Brun"]},
    "painting": {"label": "🖼 Картина дня", "tag": "#КартинаДня"},
}
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


def matches_rubric(obj, rubric):
    kind = (obj.get("objectName", "") + " " + obj.get("classification", "")).lower()
    title = obj.get("title", "").lower()
    if rubric == "women":
        # A portrait OF a woman is not necessarily a work BY a woman.
        return obj.get("artistGender", "").lower() == "female"
    if rubric == "fashion":
        return obj.get("department") == "Costume Institute" and any(word in kind for word in ("dress", "robe", "coat", "gown", "hat", "shoe", "jacket", "ensemble", "suit"))
    if rubric == "architecture":
        return any(word in kind + " " + title for word in ("temple", "capital", "column", "architectural", "floor plan", "facade", "façade", "elevation", "portal", "church", "cathedral", "palace"))
    if rubric == "travel":
        return any(word in kind for word in ("painting", "watercolor", "print", "drawing", "photograph")) and any(place.lower() in title for place in RUBRICS["travel"]["queries"])
    return any(word in kind for word in ("painting", "watercolor"))


def make_card(obj, rubric="painting"):
    if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall"):
        raise PublishError("Нет открытого музейного изображения")
    artist = obj.get("artistDisplayName") or obj.get("culture") or "Автор не указан в каталоге"
    artist = ARTISTS.get(artist, artist)
    title = obj.get("title", "").strip()
    date = obj.get("objectDate", "").strip()
    if not title or not date:
        raise PublishError("Недостаточно подтверждённых сведений")
    # Preserve original catalogue titles instead of inventing translations or facts.
    config = RUBRICS[rubric]
    prompts = {
        "fashion": "Исторический костюм из коллекции Института костюма. Присмотритесь к силуэту, линии плеч и отделке: какая деталь кажется вам самой выразительной? Представьте, как меняется впечатление от наряда при движении и другом освещении.",
        "architecture": "Архитектура из музейной коллекции: сооружения, их фрагменты и проекты. Проследите за ритмом линий и пропорциями. Какая деталь задаёт характер всему пространству? Попробуйте мысленно изменить масштаб: что останется узнаваемым?",
        "travel": "Путешествие через искусство: перед вами исторический образ места, название которого сохранено в музейном каталоге. Посмотрите, как художник выстраивает пространство. Какая деталь помогает почувствовать атмосферу? Это взгляд автора своего времени, а не современный путеводитель.",
        "women": "Автор этой работы — художница, указанная в музейном каталоге. Уделите изображению минуту: как она направляет ваше внимание? Начните с самого заметного элемента, затем найдите тихую деталь, которую легко пропустить при первом взгляде.",
        "painting": "Произведение из открытой музейной коллекции. Уделите изображению минуту: что первым привлекает ваше внимание — цвет, линия или фактура? Выберите одну деталь и попробуйте описать её тремя словами. Затем посмотрите на картину целиком ещё раз.",
    }
    caption = (config["label"] + "\n\n" + title + "\n" + artist + " · " + date +
               "\n\n" + prompts[rubric] + "\n\nИсточник и изображение: Метрополитен-музей, Public Domain.\n" + config["tag"])
    post = {"topic": title, "caption": caption, "images": [obj["primaryImageSmall"]],
            "source_url": "https://www.metmuseum.org/art/collection/search/" + str(obj["objectID"]),
            "object_id": obj["objectID"], "rubric": rubric}
    for image in obj.get("additionalImages", []):
        if image not in post["images"] and urllib.parse.urlparse(image).hostname == "images.metmuseum.org":
            post["images"].append(image)
        if len(post["images"]) == 3:
            break
    validate_post(post)
    return post


def choose_post(state, fetch=request, rubric=None):
    rubric = rubric or RUBRIC_ORDER[state.get("next_rubric_index", 0) % len(RUBRIC_ORDER)]
    used = {int(x) for x in state.get("used_object_ids", [])}
    # Prepared cards have individual editorial captions. Automatic discovery keeps
    # the channel supplied after this queue is consumed, without API subscriptions.
    for path in sorted((ROOT / "cloud" / "queue").glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8-sig"))
        if post.get("rubric", "painting") == rubric and int(post["object_id"]) not in used:
            try:
                obj = fetch(MET + "/objects/" + str(post["object_id"]))
            except PublishError:
                continue
            if not obj.get("isPublicDomain") or not obj.get("primaryImageSmall") or not matches_rubric(obj, rubric):
                continue
            approved = [obj["primaryImageSmall"]] + obj.get("additionalImages", [])
            selected = [image for image in post.get("images", []) if image in approved]
            post["images"] = selected[:3] or [obj["primaryImageSmall"]]
            post["rubric"] = rubric
            validate_post(post)
            return post
    queries = RUBRICS[rubric].get("queries", QUERIES)
    start = len(used) % len(queries)
    for offset in range(len(queries)):
        query = queries[(start + offset) % len(queries)]
        for offset_in_results in range(0, 300, 100):
            try:
                results = fetch(MET_SEARCH + "?" + urllib.parse.urlencode({
                    "hasImages": "true", "q": query, "limit": 100, "offset": offset_in_results}))
            except PublishError:
                break
            ids = [x for x in results.get("objectIDs", []) or [] if int(x) not in used]
            random.Random(len(used)).shuffle(ids)
            for object_id in ids[:20]:
                try:
                    obj = fetch(MET + "/objects/" + str(object_id))
                    if matches_rubric(obj, rubric):
                        return make_card(obj, rubric)
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


def execute_one(dry_run=False, batch_date=None):
    token = os.environ.get("JOURNAL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    store = StateStore(token) if token else None
    state = store.load() if store else json.loads((ROOT / "cloud" / "state.json").read_text(encoding="utf-8-sig"))
    now = dt.datetime.now(dt.timezone.utc)
    if batch_date:
        if state.get("pending"):
            raise PublishError("Есть неподтверждённая отправка: требуется проверка канала")
        if state.get("daily_batches", {}).get(batch_date, {}).get("completed", 0) >= 15:
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
    if batch_date:
        batch = state.setdefault("daily_batches", {}).setdefault(batch_date, {"completed": 0, "target": 15})
        batch["completed"] += 1
        state["publications"][-1]["batch_date"] = batch_date
    state["pending"] = None
    store.save(state, "Record confirmed Telegram publication")
    return {"status": "published", "message_ids": message_ids, "url": "https://t.me/lartdevivre_journal/" + str(message_ids[0])}


def execute(dry_run=False, now=None):
    """One daily block; confirmed items survive interruptions and retries."""
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone(dt.timedelta(hours=3)))
    day = local.date().isoformat()
    if dry_run:
        preview = execute_one(dry_run=True, batch_date=day)
        return {"daily_target": 15, "time_moscow": "22:30", "preview": preview}
    if (local.hour, local.minute) < (22, 30):
        return {"status": "not_due", "time_moscow": "22:30"}
    results = []
    for _ in range(15):
        result = execute_one(batch_date=day)
        if result.get("status") != "published":
            return {"status": result["status"], "published_this_run": len(results), "date_moscow": day}
        results.append(result)
    return {"status": "batch_complete", "published_this_run": len(results), "date_moscow": day, "posts": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except PublishError as error:
        print("Ошибка: " + str(error), file=sys.stderr)
        sys.exit(1)
