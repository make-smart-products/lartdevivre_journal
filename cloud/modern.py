"""Reviewed XXI-century editorial queue, with independently credited images."""
import datetime as dt
import json
import re
from urllib.parse import urlsplit

RUBRICS = {"fashion", "architecture", "painting", "women"}
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
}


def validate(post, now=None):
    current = now or dt.datetime.now(dt.timezone.utc)
    if post.get("rubric") not in RUBRICS or post.get("editorial_reviewed") is not True:
        raise ValueError("Нужна отредактированная современная карточка")
    year = post.get("modern_year")
    if not isinstance(year, int) or not 2001 <= year <= current.year + 1:
        raise ValueError("Нужен подтверждённый год XXI века")
    if post["rubric"] == "fashion" and year < current.year - 1:
        raise ValueError("Нужно обновить сезон модной подборки")
    reviewed = dt.datetime.fromisoformat(post.get("source_verified_at", ""))
    freshness = dt.timedelta(days=60 if post["rubric"] == "fashion" else 365)
    if reviewed.tzinfo is None or not dt.timedelta(0) <= current - reviewed <= freshness:
        raise ValueError("Нужно обновить проверку первоисточника")
    source = urlsplit(post.get("source_url", ""))
    artist_source = (post.get("rubric") in {"painting", "women"}
                     and post.get("source_kind") == "artist-original"
                     and post.get("artwork_rights_verified") is True
                     and source.hostname == "commons.wikimedia.org"
                     and source.path.startswith("/wiki/File:"))
    if source.scheme != "https" or not (artist_source or (source.hostname or "").removeprefix("www.") in SOURCE_HOSTS):
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
        if artist_source and not (asset.get("credit") == "Own work" or "copyright holder" in asset.get("permission", "")):
            raise ValueError("Не подтверждена лицензия самого произведения")
    if post.get("rubric") == "women" and post.get("creator_gender_verified") is not True:
        raise ValueError("Не подтверждено авторство художницы")
    profile = post.get("content_kind") == "artist-profile"
    if profile and (post.get("rubric") != "women" or artist_source or
                    any(asset.get("image_role") != "portrait" for asset in assets)):
        raise ValueError("Портрет художницы требует официального источника и явной маркировки")
    if post.get("rubric") in {"painting", "women"} and not profile and post.get("artwork_rights_verified") is not True:
        raise ValueError("Не подтверждены права на произведение")


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
