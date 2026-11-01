"""Где именно в кадре находится наблюдаемый.

Камеры в доме снимают 2880x1620 с широким углом. Человек занимает в таком
кадре десятую часть ширины, а после сжатия до 640 пикселей для модели —
шестьдесят пикселей на всего человека. Спрашивать по такой картинке «сидит
он или лежит», «держится ли за стену», «упал или сел сам» бессмысленно:
там нечего разглядывать.

Поэтому кадры режутся не целиком, а по человеку. Геометрию даёт сам Frigate:

    data.box       [x, y, w, h] в долях кадра — рамка объекта
    data.path_data [[x, y], время] — след: X это центр рамки, Y это её НИЗ
                   (проверено на боевых событиях: box y 0.374..0.999,
                   а точка следа y = 0.998)

Из размера рамки и следа восстанавливается, где человек был в каждый момент
события. Дальше vlm/crop.py собирает из этого прямоугольник.
"""
import json
import logging
import urllib.request

import config

log = logging.getLogger("geometry")


def event_geometry(event_id, timeout=10):
    """Геометрия события: (рамка, след). Оба — в долях кадра.

        рамка  (x, y, w, h) или None
        след   [(x, y, время), ...] или []

    Берём из архива по HTTP, а не из MQTT, по двум причинам. В MQTT рамка
    приходит в пикселях detect-потока, а клип пишется record-потоком другого
    разрешения — такая рамка легла бы мимо. И по HTTP это доступно не только
    живому событию, но и переоценке старой записи, когда MQTT-сообщения
    давно нет.
    """
    url = f"{config.FRIGATE_URL}/api/events/{event_id}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            data = (json.load(answer) or {}).get("data") or {}
    except Exception as error:
        log.debug("геометрия %s не пришла: %s: %s",
                  event_id, type(error).__name__, error)
        return None, []

    return _box(data.get("box")), _points(data.get("path_data"))


def _box(box):
    if not box or len(box) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def _points(path):
    """След: [[x, y], время] -> [(x, y, время)]. X это центр рамки,
    Y — её НИЗ, ноги (проверено на боевых событиях)."""
    out = []
    for item in path or []:
        try:
            (x, y), t = item
            out.append((float(x), float(y), float(t)))
        except (TypeError, ValueError):
            continue
    return out
