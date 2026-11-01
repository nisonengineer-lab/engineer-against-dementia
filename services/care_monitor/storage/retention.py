"""Срок хранения: всё старше RETENTION_DAYS суток удаляется — и из базы, и с диска.

ЗАЧЕМ. До 2.5.0 не удалялось ничего. Видео одного события весит мегабайты,
событий сотни в день, и за две с половиной недели малина забилась до 950 МБ
свободного места из 119 ГБ. Когда место кончится, перестанет писаться всё:
и база, и видео, и Home Assistant вместе с ними.

КАК. Отдельный поток раз в RETENTION_EVERY_MIN минут:
    1. стирает из базы события старше срока (storage.db.purge_before);
    2. удаляет их папки с видео и кадрами;
    3. подбирает папки, у которых строки в базе нет вовсе: событие, которое
       проверка признала чужим, разбор, сорвавшийся до записи, и всё, что
       лежало на диске до этой версии;
    4. убирает опустевшие папки дней.

ЧЕГО НЕ ТРОГАЕТ.
    - событие, которое прямо сейчас переоценивается (см. purge_before);
    - папку, в которую писали последний час: её может разбирать живой
      разбор — из стека может прийти и старое событие, если модель долго
      лежала;
    - всё, что не похоже на папку события (ГГГГ-ММ-ДД/ЧЧ-ММ-СС_камера_id):
      чужие файлы в DATA_DIR не удаляются никогда;
    - память «где его видели в последний раз», эталоны, промпты, журнал.

Любая ошибка здесь пишется в лог и не выходит наружу: уборка — не повод
останавливать разбор событий.
"""
import logging
import os
import re
import shutil
import threading
import time

import config

log = logging.getLogger("retention")

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT = re.compile(r"^(\d{2})-(\d{2})-(\d{2})_")
BUSY_SECONDS = 3600          # в папку писали недавно — не трогаем


class Retention:
    def __init__(self, db):
        self.db = db
        self.stopping = False
        self.thread = None

    def start(self):
        if not self.db or config.RETENTION_DAYS <= 0:
            log.info("срок хранения не задан — старые события не удаляются")
            return
        self.thread = threading.Thread(target=self._loop, name="retention", daemon=True)
        self.thread.start()
        log.info("срок хранения %d сут., проверка раз в %d мин",
                 config.RETENTION_DAYS, config.RETENTION_EVERY_MIN)

    def stop(self):
        self.stopping = True

    def _loop(self):
        # Первая уборка — через минуту после старта, а не сразу: старт и так
        # занят подключением к MQTT, базе и модели.
        wait = 60
        while not self.stopping:
            time.sleep(wait)
            wait = max(1, config.RETENTION_EVERY_MIN) * 60
            try:
                self.run_once()
            except Exception:
                log.exception("уборка старых событий сорвалась — попробую в следующий раз")

    def run_once(self, now=None):
        cutoff = (now or time.time()) - config.RETENTION_DAYS * 86400
        folders = self.db.purge_before(cutoff)
        if folders:
            log.info("из базы удалено событий старше %d сут.: %d",
                     config.RETENTION_DAYS, len(folders))

        removed = sum(self._remove(folder) for folder in folders if folder)
        removed += self._sweep(cutoff)
        if removed:
            log.info("с диска удалено папок событий: %d", removed)
        return len(folders), removed

    # ---------- диск ----------

    def _root(self):
        return os.path.abspath(config.DATA_DIR)

    def _remove(self, folder, quiet=False):
        """Удалить одну папку события. Только внутри DATA_DIR и только если
        в неё не писали последний час."""
        root = self._root()
        target = os.path.abspath(folder)
        if not target.startswith(root + os.sep) or not os.path.isdir(target):
            return 0
        if self._busy(target):
            (log.debug if quiet else log.info)(
                "папка %s свежая — оставляю до следующей уборки", target)
            return 0
        try:
            shutil.rmtree(target)
            return 1
        except Exception as error:
            log.error("папку %s удалить не вышло: %s: %s",
                      target, type(error).__name__, error)
            return 0

    def _sweep(self, cutoff):
        """Папки событий старше срока, у которых нет строки в базе, и
        опустевшие папки дней. Время события берём из имени папки — так же,
        как его туда записал разбор."""
        root = self._root()
        if not os.path.isdir(root):
            return 0
        kept = self._kept_folders(cutoff)
        removed = 0
        for day in sorted(os.listdir(root)):
            day_dir = os.path.join(root, day)
            if not DAY.match(day) or not os.path.isdir(day_dir):
                continue
            for name in os.listdir(day_dir):
                folder = os.path.join(day_dir, name)
                moment = self._moment(day, name)
                if (moment is None or moment >= cutoff or folder in kept
                        or not os.path.isdir(folder)):
                    continue
                removed += self._remove(folder, quiet=True)
            try:
                if not os.listdir(day_dir):
                    os.rmdir(day_dir)
            except OSError:
                pass
        return removed

    def _kept_folders(self, cutoff):
        """Папки ВСЕХ событий, что остались в базе. Уборка по диску их не
        трогает, как бы старо ни выглядело имя папки.

        По имени судить нельзя: часть событий разбиралась, когда аддон ещё
        жил в UTC, и их папки названы на три часа раньше настоящего
        времени. Строку база оставила, а папка по имени — «старше срока».
        Время из имени годится только для папок-сирот без строки в базе,
        и там ошибка на пару часов ничего не ломает."""
        rows = self.db._rows("SELECT folder FROM events")
        return {os.path.abspath(r["folder"]) for r in rows if r["folder"]}

    @staticmethod
    def _moment(day, name):
        match = EVENT.match(name)
        if not match:
            return None
        try:
            return time.mktime(time.strptime(
                f"{day} {match.group(1)}:{match.group(2)}:{match.group(3)}",
                "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return None

    @staticmethod
    def _busy(folder):
        """Писали ли в папку события за последний час. Смотрим саму папку и
        папки кадров: при нарезке меняется именно их время."""
        newest = 0
        for path in (folder, os.path.join(folder, "frames"),
                     os.path.join(folder, "frames_wide")):
            try:
                newest = max(newest, os.path.getmtime(path))
            except OSError:
                pass
        return time.time() - newest < BUSY_SECONDS
