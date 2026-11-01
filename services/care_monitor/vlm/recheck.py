#!/usr/bin/env python3
"""Переоценка записи: спросить модель заново по той же записи.

ЗАЧЕМ. Разбор ошибается. Человек, открывший журнал, видит «упал», хотя дед
просто сел на диван, — и у него должен быть способ сказать «пересмотри»,
а не только «неверно». Кнопка «Переоценить» в журнале ставит событие в
очередь, а разбирает очередь этот воркер.

Раньше кнопка была, очередь была, а разбирать её было некому: запись
навсегда оставалась «на переоценке». Это и чинится.

ЧЕМ ПЕРЕОЦЕНКА ОТЛИЧАЕТСЯ ОТ ПЕРВОГО РАЗБОРА.

Кадры режутся ЗАНОВО из сохранённого клипа, а не берутся старые. Переоценка
и нужна чаще всего потому, что настройки изменились: включили кроп по
человеку, подняли предел ширины, поправили промпт. Разбирать старые кадры
значило бы получить тот же ответ и не понять, почему.

Событие в базе не заводится заново и не удаляется: строка события уже есть,
меняется только разбор. Даже если проверка теперь скажет «это не он»,
карточку не стираем — человек просил пересмотреть, а не удалить.

Новый разбор ДОПИСЫВАЕТСЯ, а не заменяет старый. В таблице analyses
появляется ещё одна строка, журнал показывает свежую. Прошлый ответ модели
остаётся: по нему видно, что именно она передумала.
"""
import logging
import os
import threading
import time

import config

log = logging.getLogger("recheck")

WAIT = 5            # как часто заглядывать в очередь, секунд
BATCH = 3           # сколько записей брать за раз


class PersonFromRow:
    """Событие, восстановленное из базы.

    Разбор ждёт объект, который умеет отвечать на вопросы о датчиках:
    сколько шёл, сколько стоял, какой маршрут, куда смотрел. У живого
    события это TrackedPerson, собранный из MQTT. При переоценке MQTT-
    сообщения давно нет, зато всё то же самое лежит в таблице events —
    его туда и записали при первом разборе.
    """

    def __init__(self, row):
        self._row = row
        self.event_id = row["event_id"]
        self.camera = row["camera"]
        self.identity = row["identity"]
        self.identity_source = row["identity_source"]
        self.identity_score = row["identity_score"]
        self.start_time = row["start_time"]
        self.end_time = row["end_time"]
        self.duration = row["duration_s"] or 0
        self.path_seconds = row["moving_s"] or 0
        self.still_seconds = row["still_s"] or 0
        self.heading = row["heading_deg"]
        self.path_length = row["path_length"] or 0
        self.clean_length = row["path_length"] or 0
        self.displacement = row["displacement"] or 0
        self.frame_speed = row["frame_speed"] or 0
        self.is_wandering = bool(row["wandering"])
        self.near_edge = row["near_frame_edge"]
        self.glitches = row["tracker_glitches"] or 0
        self.clip_url = row["clip_url"]
        self.snapshot_clean_url = row["frame_url"]
        self.folder = row["folder"]
        self.zone_route = row["zone_route"] or ""
        # Геометрия следа в базе не лежит — её при нарезке кадров возьмут у
        # Frigate по event_id, там же, где и рамку.
        self.points = []
        self.posture = row["posture"] if "posture" in row.keys() else None

    @property
    def zone_route_list(self):
        return [z for z in self.zone_route.split(" → ") if z]

    @property
    def heading_text(self):
        return _HEADING.get(self.heading is not None and _side(self.heading), "—")

    def vlm_payload(self):
        return {
            "event_id": self.event_id,
            "person": self.identity,
            "identified_by": self.identity_source,
            "confidence": self.identity_score,
            "camera": self.camera,
            "started": self._row["started_at"],
            "duration_s": self.duration,
            "moving_s": self.path_seconds,
            "still_s": self.still_seconds,
            "zone_route": self.zone_route,
            "heading_deg": self.heading,
            "path_length": self.path_length,
            "displacement": self.displacement,
            "frame_speed": self.frame_speed,
            "wandering": self.is_wandering,
            "near_frame_edge": self.near_edge,
            "tracker_glitches": self.glitches,
            "clip": self.clip_url,
            "frame": self.snapshot_clean_url,
        }


_HEADING = {"вправо": "вправо", "влево": "влево", "вверх": "вверх",
            "вниз": "вниз", None: "—"}


def _side(deg):
    if deg is None:
        return None
    deg %= 360
    if deg < 45 or deg >= 315:
        return "вправо"
    if deg < 135:
        return "вниз"
    if deg < 225:
        return "влево"
    return "вверх"


class RecheckWorker:
    """Отдельный поток: смотрит очередь и переразбирает то, что в ней есть.

    Отдельный, а не в общем стеке разбора, намеренно. Живое событие — это
    тревога, и она не должна ждать, пока мы пересматриваем вчерашнюю запись
    по чьей-то просьбе. Переоценка идёт по одной и в своём темпе.
    """

    def __init__(self, analyzer, db):
        self.analyzer = analyzer
        self.db = db
        self.stopping = False
        self.thread = None

    def start(self):
        if not self.db:
            return
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, name="recheck",
                                       daemon=True)
        self.thread.start()
        log.info("переоценка запущена: очередь проверяется раз в %d с", WAIT)

    def stop(self):
        self.stopping = True

    def _loop(self):
        while not self.stopping:
            try:
                tasks = self.db.pending_rechecks(BATCH)
            except Exception:
                log.exception("не смог заглянуть в очередь переоценки")
                tasks = []
            if not tasks:
                time.sleep(WAIT)
                continue
            for task in tasks:
                if self.stopping:
                    return
                self._one(task)

    def _one(self, task):
        event_id = task["event_id"]
        short = event_id.split("-")[-1]
        began = time.time()
        try:
            self.db.start_recheck(task["id"])
            result = self.analyzer.reanalyze(event_id)
            if result:
                log.info("%s: ПЕРЕОЦЕНЕНО за %.1f с — %s (опасность %s)",
                         short, time.time() - began,
                         result.get("summary"), result.get("danger"))
            else:
                log.error("%s: переоценить не вышло", short)
        except Exception:
            log.exception("%s: переоценка сорвалась", short)
        finally:
            # Задачу закрываем в любом случае. Незакрытая крутилась бы в
            # очереди вечно, а карточка в журнале — вечно «на переоценке».
            try:
                self.db.finish_recheck(task["id"])
            except Exception:
                log.exception("%s: не смог закрыть задачу переоценки", short)


def frames_dir_of(folder):
    return os.path.join(folder or "", "frames")
