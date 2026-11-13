"""Reviewed XXI-century editorial queue, with independently credited images."""
import datetime as dt
import json
import re
from urllib.parse import urlsplit

RUBRICS = {"fashion", "architecture", "painting", "celebrities"}
SOURCE_HOSTS = {
    "pradagroup.com", "miumiu.com", "chanel.com", "cos.com", "uniqlo.com",
    "stellamccartney.com", "loewe.com", "bottegaveneta.com", "hermes.com",
    "fondationlouisvuitton.fr", "louvreabudhabi.ae", "presse.louvre.fr",
    "henninglarsen.com", "elbphilharmonie.de", "big.dk", "img.big.dk",
    "kistefosmuseum.com", "zaha-hadid.com", "vam.ac.uk", "mplus.org.hk",
    "herzogdemeuron.com", "snohetta.com", "old.snohetta.com", "ala.fi",
    "oodihelsinki.fi", "centrepompidou-metz.fr", "shigerubanarchitects.com",
    "zpk.org", "rpbw.com", "fondazioneprada.org", "oma.com", "qm.org.qa",
    "jeannouvel.com", "lvmh.com",
    "sitasingstheblues.com", "gazabiennale.org",
    "icaboston.org",
    "dior.com", "chloe.com", "louisvuitton.com", "nsw.gov.au",
    "infrastructure.nsw.gov.au", "artgallery.nsw.gov.au", "whitearkitekter.com",
    "mvrdv.com", "boijmans.nl", "heatherwick.com", "museodelprado.es",
    "uffizi.it", "nationalgallery.org.uk", "belvedere.at", "sammlung.belvedere.at",
    "pero.co.in", "seoulfashionweek.org", "english.seoul.go.kr", "guestinresidence.com",
    "fdci.org", "heruniverse.com", "lego.com", "museum-joanneum.at", "apple.com",
    "nasjonalmuseet.no", "musee-orsay.fr", "wallacecollection.org", "chopard.com", "oscars.org",
}
OFFICIAL_MEDIA = {"https://www.youtube.com/watch?v=sE6Rk9TJcSk"}  # Louis Vuitton's verified channel.


def validate(post, now=None):
    current = now or dt.datetime.now(dt.timezone.utc)
    if post.get("rubric") not in RUBRICS or post.get("editorial_reviewed") is not True:
        raise ValueError("Нужна отредактированная современная карточка")
    if post.get("kind") != "article":
        raise ValueError("Викторины и опросы отключены")
    year = post.get("modern_year")
    if not isinstance(year, int) or not 1 <= year <= current.year + 1:
        raise ValueError("Нужен подтверждённый год")
    if post["rubric"] in {"fashion", "architecture"} and year < 2001:
        raise ValueError("Нужен подтверждённый год XXI века")
    if post["rubric"] == "fashion" and year < current.year - 1:
        raise ValueError("Нужно обновить сезон модной подборки")
    reviewed = dt.datetime.fromisoformat(post.get("source_verified_at", ""))
    freshness = dt.timedelta(days=60 if post["rubric"] == "fashion" else 365)
    if reviewed.tzinfo is None or not dt.timedelta(0) <= current - reviewed <= freshness:
        raise ValueError("Нужно обновить проверку первоисточника")
    source = urlsplit(post.get("source_url", ""))
    if source.scheme != "https" or ((source.hostname or "").removeprefix("www.") not in SOURCE_HOSTS and post.get("source_url") not in OFFICIAL_MEDIA):
        raise ValueError("Нужен официальный первоисточник бренда, бюро или здания")
    if not isinstance(post.get("object_id"), int) or post["object_id"] >= 0:
        raise ValueError("Неверный идентификатор редакционной карточки")
    assets = post.get("licensed_images", [])
    if [asset.get("image") for asset in assets] != post.get("images"):
        raise ValueError("Не подтверждены права на каждое изображение")
    for asset in assets:
        photo = urlsplit(asset["image"])
        if photo.scheme != "https" or photo.hostname not in {"upload.wikimedia.org", "thumb.wikimedia.org"}:
            raise ValueError("Нужна проверенная фотография Wikimedia Commons")
        if not re.fullmatch(r"Public domain|CC0(?: 1\.0)?|CC BY(?:-SA)? [1-4]\.0", asset.get("license", "")):
            raise ValueError("Не подтверждена свободная лицензия фотографии")
        if not asset.get("artist") or asset["artist"] not in post["caption"]:
            raise ValueError("Не указан автор фотографии")
        if not asset.get("source_url", "").startswith("https://commons.wikimedia.org/wiki/File:"):
            raise ValueError("Нет исходной страницы фотографии")
        if asset["license"].startswith("CC BY"):
            links = {entity.get("url") for entity in post.get("caption_entities", [])}
            licence_url = asset.get("license_url", "")
            if not licence_url.startswith("https://creativecommons.org/licenses/") or asset["source_url"] not in links or licence_url not in links:
                raise ValueError("Нужно указать авторство и лицензию фотографии")
    if post["rubric"] == "painting":
        if not post.get("famous_work_verified") or not post.get("artwork_public_domain_verified"):
            raise ValueError("Нужна известная картина с подтверждёнными правами")
        if any(a["license"] not in {"Public domain", "CC0", "CC0 1.0"} for a in assets):
            raise ValueError("Нужна свободная репродукция известной картины")
    if post["rubric"] == "celebrities" and (not post.get("person_verified") or any(a.get("image_role") != "portrait" for a in assets)):
        raise ValueError("Нужен проверенный публичный профиль и подписанный портрет")


def choose(root, state, rubric, diverse_enough):
    used = set(state.get("used_object_ids", []))
    for path in sorted((root / "cloud" / "queue").glob("modern-*.json")):
        post = json.loads(path.read_text(encoding="utf-8"))
        if post.get("rubric") != rubric or post.get("object_id") in used:
            continue
        try:
            validate(post)
        except (ValueError, TypeError):
            continue
        if diverse_enough(post, state):
            return post
    raise ValueError("Нужно пополнить современную очередь рубрики " + rubric)
