"""Поиск последнего появления наблюдаемого в архиве Frigate.

Зачем. Система знает, где человек был, только из событий, которые пришли по
MQTT при её жизни. После перезапуска — или при первом запуске на доме, где
Frigate работает давно, — память пуста, и на вопрос «где он сейчас» ответить
нечем, хотя ответ лежит в архиве Frigate.

Поэтому один раз при старте, и только если память пуста, мы спрашиваем
Frigate: когда ты последний раз видел этого человека? Найденное событие
проходит обычную цепочку разбора и становится первой записью памяти.

Опознание у Frigate двухканальное, и мы смотрим оба: sub_label — это лицо,
data.person_by_appearance — внешность. Первый точнее, второй срабатывает
чаще (лица со спины не видно).
"""
import json
import logging
import time
import urllib.parse
import urllib.request

import config
from .person import TrackedPerson

log = logging.getLogger("lookback")


def _identity(event):
    """Кем Frigate считает человека в этом событии и насколько уверен."""
    name = (event.get("sub_label") or "").strip()
    if name:
        return name, "face", None
    data = event.get("data") or {}
    name = (data.get(config.ATTRIBUTE_FIELD) or "").strip()
    if name:
        return name, "attribute", data.get(f"{config.ATTRIBUTE_FIELD}_score")
    return None, None, None


def _fetch(hours):
    """События с человеком и клипом за последние hours часов."""
    query = urllib.parse.urlencode({
        "limit": config.LOOKBACK_LIMIT,
        "label": "person",
        "has_clip": 1,
        "after": time.time() - hours * 3600,
    })
    url = f"{config.FRIGATE_URL}/api/events?{query}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def last_appearance(target=None, hours=None):
    """Самое свежее событие, где Frigate видел наблюдаемого. None — не нашлось."""
    target = (target or config.TARGET_IDENTITY).strip().casefold()
    hours = hours or config.LOOKBACK_HOURS
    try:
        events = _fetch(hours)
    except Exception as error:
        log.error("АРХИВ FRIGATE НЕ ОТВЕТИЛ (%s): %s: %s. Память останется "
                  "пустой до первого живого события",
                  config.FRIGATE_URL, type(error).__name__, error)
        return None

    mine = []
    for event in events:
        name, source, score = _identity(event)
        if name and name.strip().casefold() == target:
            mine.append((event, source, score))
    log.info("в архиве Frigate за %d ч: событий с человеком %d, из них "
             "с наблюдаемым %d", hours, len(events), len(mine))
    if not mine:
        return None

    event, source, score = max(mine, key=lambda item: item[0]["start_time"])
    return _to_person(event, source, score)


def _to_person(event, source, score):
    """Событие из архива — в ту же карточку, что приходит по MQTT.

    Разбор не должен знать, откуда взялось событие: из живого потока или из
    архива. Поэтому собираем ровно тот же TrackedPerson.
    """
    person = TrackedPerson(event["id"])
    person.update({
        "camera": event.get("camera"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "current_zones": [],
        # В архиве список зон лежит в zones, порядка входа в них нет —
        # маршрут будет без порядка. Для ответа «где он был» этого хватает.
        "entered_zones": event.get("zones") or [],
        "path_data": (event.get("data") or {}).get("path_data") or [],
        "has_clip": event.get("has_clip"),
        "has_snapshot": event.get("has_snapshot"),
    }, event_type="end")
    person.identity = (event.get("sub_label")
                       or (event.get("data") or {}).get(config.ATTRIBUTE_FIELD))
    person.identity_source = source
    person.identity_score = score
    return person
