import json
import logging
import os
import sqlite3
import threading

import config

log = logging.getLogger("db")


def _flag(value):
    """None означает «такой вопрос модели не задавали» — это не то же самое,
    что «нет». Поэтому None в базу и уходит как NULL."""
    return None if value is None else int(bool(value))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    camera           TEXT,
    identity         TEXT,
    identity_source  TEXT,
    identity_score   REAL,
    start_time       REAL,
    end_time         REAL,
    started_at       TEXT,
    day_part         TEXT,
    duration_s       REAL,
    moving_s         REAL,
    still_s          REAL,
    zone_route       TEXT,
    heading_deg      INTEGER,
    path_length      REAL,
    displacement     REAL,
    frame_speed      REAL,
    wandering        INTEGER,
    near_frame_edge  TEXT,
    tracker_glitches INTEGER,
    folder           TEXT,
    clip_path        TEXT,
    frames_dir       TEXT,
    clip_url         TEXT,
    frame_url        TEXT,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(event_id),
    model           TEXT,
    prompt_sha      TEXT,
    frames_used     INTEGER,
    frame_width     INTEGER,
    reference_count INTEGER,
    subject_found   INTEGER,
    posture         TEXT,      -- в чём остался под конец события
    postures        TEXT,      -- JSON: все положения по порядку времени
    on_what         TEXT,
    action          TEXT,
    objects         TEXT,
    visible         TEXT,
    quality         TEXT,
    danger          TEXT,
    summary         TEXT,
    raw_answer      TEXT,
    seconds         REAL,
    -- ответы отдельных шагов цепочки: каждый вопрос задавался своим запросом
    moving          INTEGER,
    gait            TEXT,
    gait_risk       TEXT,
    gait_support    TEXT,
    sleeping        INTEGER,
    sleep_moves     TEXT,      -- шевелится / неподвижен / не видно
    eating          INTEGER,
    drinking        INTEGER,
    meds            INTEGER,
    toilet          TEXT,
    fallen          INTEGER,
    fall_surface    TEXT,
    danger_reason   TEXT,
    steps           TEXT,      -- полный след цепочки: вопрос, ответ, секунды
    steps_asked     INTEGER,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id   INTEGER NOT NULL REFERENCES analyses(id),
    verdict       TEXT NOT NULL CHECK (verdict IN ('correct','incorrect')),
    subject_found INTEGER,
    posture       TEXT,
    on_what       TEXT,
    action        TEXT,
    objects       TEXT,
    note          TEXT,
    author        TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

-- Очередь переоценок. Она же — то самое временное хранилище, по которому
-- журнал понимает, какие карточки сейчас пересматриваются: пока у строки
-- нет done_at, событие в работе. Закрылась строка — карточка вернулась в
-- обычный вид. Отдельного списка id держать не надо, он уже есть.
CREATE TABLE IF NOT EXISTS recheck_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL REFERENCES events(event_id),
    requested_at TEXT DEFAULT (datetime('now','localtime')),
    started_at   TEXT,      -- взято воркером: «стоит в очереди» -> «идёт»
    done_at      TEXT,
    author       TEXT
);

-- Где наблюдаемого видели в последний раз. Держим ОТДЕЛЬНО от истории:
-- одна строка на человека, всегда перезаписывается. Это не журнал (журнал —
-- таблица events), а быстрый ответ на вопрос «где он сейчас может быть».
CREATE TABLE IF NOT EXISTS last_seen (
    identity     TEXT PRIMARY KEY,
    event_id     TEXT,
    analysis_id  INTEGER,
    seen_at      TEXT,      -- время начала события, человеческое
    seen_ts      REAL,      -- оно же unix: по нему считаем «сколько назад»
    camera       TEXT,
    zone         TEXT,      -- последняя зона маршрута, как в Frigate
    zone_name    TEXT,      -- она же по-русски
    zone_route   TEXT,
    posture      TEXT,
    on_what      TEXT,
    action       TEXT,
    sleeping     INTEGER,
    toilet       TEXT,
    danger       TEXT,
    summary      TEXT,
    folder       TEXT,
    frame_url    TEXT,
    card         TEXT,      -- вся карточка целиком, на случай если понадобится ещё
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_recheck_pending ON recheck_queue(done_at);
CREATE INDEX IF NOT EXISTS idx_analyses_event ON analyses(event_id);
CREATE INDEX IF NOT EXISTS idx_labels_analysis ON labels(analysis_id);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_time);
"""

# Представление создаётся отдельно и после миграции:
# оно читает колонки, которых в старой базе ещё нет.
VIEW = """
DROP VIEW IF EXISTS dataset;
CREATE VIEW dataset AS
SELECT
    a.id                                    AS analysis_id,
    a.event_id                              AS event_id,
    e.folder                                AS folder,
    e.frames_dir                            AS frames_dir,
    e.camera                                AS camera,
    e.started_at                            AS started_at,
    e.day_part                              AS day_part,
    e.zone_route                            AS zone_route,
    a.model                                 AS model,
    a.prompt_sha                            AS prompt_sha,
    a.frames_used                           AS frames_used,
    COALESCE(l.subject_found, a.subject_found) AS subject_found,
    COALESCE(l.posture, a.posture)          AS posture,
    a.postures                              AS postures,
    COALESCE(l.on_what, a.on_what)          AS on_what,
    COALESCE(l.action,  a.action)           AS action,
    COALESCE(l.objects, a.objects)          AS objects,
    a.danger                                AS danger,
    a.danger_reason                         AS danger_reason,
    a.summary                               AS summary,
    a.moving                                AS moving,
    a.gait                                  AS gait,
    a.gait_risk                             AS gait_risk,
    a.gait_support                          AS gait_support,
    a.sleeping                              AS sleeping,
    a.sleep_moves                           AS sleep_moves,
    a.eating                                AS eating,
    a.drinking                              AS drinking,
    a.meds                                  AS meds,
    a.toilet                                AS toilet,
    a.fallen                                AS fallen,
    a.fall_surface                          AS fall_surface,
    a.steps                                 AS steps,
    a.steps_asked                           AS steps_asked,
    e.deleted_at                            AS deleted_at,
    l.verdict                               AS verdict,
    l.note                                  AS note,
    CASE WHEN l.verdict = 'correct' THEN 1
         WHEN l.verdict = 'incorrect' THEN -1 END AS vote,
    CASE WHEN COALESCE(l.posture, l.on_what, l.action) IS NOT NULL
         THEN 1 ELSE 0 END                  AS has_corrections,
    CASE
        WHEN l.id IS NULL          THEN 'не размечено'
        WHEN l.verdict = 'correct' THEN 'подтверждено'
        ELSE 'исправлено'
    END                                     AS label_status
FROM analyses a
JOIN events e ON e.event_id = a.event_id
LEFT JOIN labels l ON l.id = (
    SELECT id FROM labels WHERE analysis_id = a.id ORDER BY id DESC LIMIT 1
);
"""


class Database:
    """Одна база на двоих: аддон пишет, API читает и ставит метки.

    Режим WAL разрешает одного писателя и сколько угодно читателей
    одновременно. busy_timeout нужен на случай, когда писателей всё же
    двое: соединение подождёт освобождения вместо мгновенной ошибки
    "database is locked".
    """

    def __init__(self, path=None, readonly=False):
        self.path = path or config.DB_PATH
        self.readonly = readonly
        self.lock = threading.Lock()

        if readonly:
            uri = f"file:{os.path.abspath(self.path)}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self.conn = sqlite3.connect(self.path, check_same_thread=False)

        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout={config.DB_BUSY_TIMEOUT_MS}")
        if not readonly:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            with self.lock:
                self.conn.executescript(SCHEMA)
                self.conn.commit()
            self._migrate()
            with self.lock:
                self.conn.executescript(VIEW)
                self.conn.commit()
        log.info("база готова: %s%s", os.path.abspath(self.path),
                 " (только чтение)" if readonly else "")

    # колонки, появившиеся позже первой версии базы
    LATER_COLUMNS = {
        "events": {"deleted_at": "TEXT"},
        "analyses": {
            "moving": "INTEGER", "gait": "TEXT", "gait_risk": "TEXT",
            "gait_support": "TEXT", "sleeping": "INTEGER", "eating": "INTEGER",
            "drinking": "INTEGER", "meds": "INTEGER", "toilet": "TEXT",
            "fallen": "INTEGER", "fall_surface": "TEXT", "danger_reason": "TEXT",
            "steps": "TEXT", "steps_asked": "INTEGER",
            "postures": "TEXT", "sleep_moves": "TEXT",
        },
    }

    LATER_COLUMNS_QUEUE = {"recheck_queue": {"started_at": "TEXT"}}

    def _migrate(self):
        """Добавляет колонки, появившиеся позже. SQLite не умеет ADD COLUMN IF NOT EXISTS."""
        tables = dict(self.LATER_COLUMNS)
        tables.update(self.LATER_COLUMNS_QUEUE)
        for table, columns in tables.items():
            have = {row["name"] for row in
                    self.conn.execute(f"PRAGMA table_info({table})")}
            added = [name for name in columns if name not in have]
            if not added:
                continue
            with self.lock:
                for name in added:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {columns[name]}")
                self.conn.commit()
            log.info("в таблицу %s добавлены колонки: %s", table, ", ".join(added))

    def _guard(self):
        if self.readonly:
            raise RuntimeError("база открыта только на чтение")

    def close(self):
        with self.lock:
            self.conn.close()

    # ---------- запись ----------

    def save_event(self, person, folder, day_part, clip_path=None, frames_dir=None):
        self._guard()
        data = person.vlm_payload()
        row = {
            "event_id": person.event_id,
            "camera": person.camera,
            "identity": person.identity,
            "identity_source": person.identity_source,
            "identity_score": person.identity_score,
            "start_time": person.start_time,
            "end_time": person.end_time,
            "started_at": data["started"],
            "day_part": day_part,
            "duration_s": person.duration,
            "moving_s": person.path_seconds,
            "still_s": person.still_seconds,
            "zone_route": person.zone_route,
            "heading_deg": person.heading,
            "path_length": person.path_length,
            "displacement": person.displacement,
            "frame_speed": person.frame_speed,
            "wandering": int(person.is_wandering),
            "near_frame_edge": person.near_edge,
            "tracker_glitches": person.glitches,
            "folder": folder,
            "clip_path": clip_path,
            "frames_dir": frames_dir,
            "clip_url": person.clip_url,
            "frame_url": person.snapshot_clean_url,
        }
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO events ({columns}) VALUES ({marks})",
                list(row.values()),
            )
            self.conn.commit()
        return person.event_id

    def save_analysis(self, event_id, result, raw_answer, model, prompt_sha,
                      frame_width, reference_count):
        self._guard()
        row = {
            "event_id": event_id,
            "model": model,
            "prompt_sha": prompt_sha,
            "frames_used": result.get("frames_used"),
            "frame_width": frame_width,
            "reference_count": reference_count,
            "subject_found": 1,
            "posture": result.get("posture"),
            "postures": json.dumps(result.get("postures") or [], ensure_ascii=False),
            "on_what": result.get("on_what"),
            "action": result.get("action"),
            "objects": json.dumps(result.get("objects") or [], ensure_ascii=False),
            "visible": result.get("visible"),
            "quality": result.get("quality"),
            "danger": result.get("danger"),
            "summary": result.get("summary"),
            "raw_answer": json.dumps(raw_answer, ensure_ascii=False),
            "seconds": result.get("analyzed_in_s"),
            # ответы отдельных шагов цепочки
            "moving": _flag(result.get("moving")),
            "gait": result.get("gait"),
            "gait_risk": result.get("gait_risk"),
            "gait_support": result.get("gait_support"),
            "sleeping": _flag(result.get("sleeping")),
            "sleep_moves": result.get("sleep_moves"),
            "eating": _flag(result.get("eating")),
            "drinking": _flag(result.get("drinking")),
            "meds": _flag(result.get("meds")),
            "toilet": result.get("toilet"),
            "fallen": _flag(result.get("fallen")),
            "fall_surface": result.get("fall_surface"),
            "danger_reason": result.get("danger_reason"),
            "steps": json.dumps(result.get("steps") or [], ensure_ascii=False),
            "steps_asked": result.get("steps_asked"),
        }
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.lock:
            cursor = self.conn.execute(
                f"INSERT INTO analyses ({columns}) VALUES ({marks})", list(row.values())
            )
            self.conn.commit()
            return cursor.lastrowid

    # ---------- где его видели в последний раз ----------

    def save_last_seen(self, card):
        """Перезаписывает единственную строку про этого человека.

        Разбор идёт из стека последним-пришёл-первым-ушёл, поэтому события
        приходят не по порядку времени. Более старое событие память не трогает:
        иначе «последний раз» уехал бы в прошлое."""
        self._guard()
        identity = card.get("identity") or config.TARGET_IDENTITY
        known = self.last_seen(identity)
        if known and (known["seen_ts"] or 0) >= (card.get("seen_ts") or 0):
            log.info("память не тронута: событие %s старше того, что уже записано "
                     "(%s)", card.get("event_id"), known["seen_at"])
            return False

        row = {"identity": identity,
               "card": json.dumps(card, ensure_ascii=False),
               "updated_at": __import__("datetime").datetime.now()
                             .strftime("%Y-%m-%d %H:%M:%S")}
        for name in ("event_id", "analysis_id", "seen_at", "seen_ts", "camera",
                     "zone", "zone_name", "zone_route", "posture", "on_what",
                     "action", "toilet", "danger", "summary", "folder", "frame_url"):
            row[name] = card.get(name)
        row["sleeping"] = _flag(card.get("sleeping"))

        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO last_seen ({columns}) VALUES ({marks})",
                list(row.values()))
            self.conn.commit()
        return True

    def last_seen(self, identity=None):
        rows = self._rows("SELECT * FROM last_seen WHERE identity = ?",
                          (identity or config.TARGET_IDENTITY,))
        return rows[0] if rows else None

    def forget_event(self, event_id):
        """Проверка не подтвердила наблюдаемого — карточки не должно остаться.

        Чистим начисто и в правильном порядке: сначала дети (метки, разборы,
        очередь перепроверки), потом сама строка события. Внешние ключи включены,
        обратный порядок база не пропустит."""
        self._guard()
        with self.lock:
            self.conn.execute(
                "DELETE FROM labels WHERE analysis_id IN "
                "(SELECT id FROM analyses WHERE event_id = ?)", (event_id,))
            self.conn.execute("DELETE FROM analyses WHERE event_id = ?", (event_id,))
            self.conn.execute("DELETE FROM recheck_queue WHERE event_id = ?", (event_id,))
            cursor = self.conn.execute("DELETE FROM events WHERE event_id = ?",
                                       (event_id,))
            self.conn.commit()
        self.rebuild_last_seen_after(event_id)
        return cursor.rowcount

    def rebuild_last_seen_after(self, gone_event_id):
        """Если память указывала на стёртое событие, переводим её на самое
        свежее из оставшихся подтверждённых появлений. Иначе «где он был»
        ссылалось бы на карточку, которой больше нет."""
        known = self.last_seen()
        if not known or known["event_id"] != gone_event_id:
            return None
        identity = known["identity"]
        rows = self._rows(
            "SELECT e.event_id, e.camera, e.zone_route, e.started_at, e.start_time, "
            "       e.folder, e.frame_url, a.id AS analysis_id, a.posture, a.on_what, "
            "       a.action, a.sleeping, a.toilet, a.danger, a.summary "
            "FROM analyses a JOIN events e ON e.event_id = a.event_id "
            "WHERE a.subject_found = 1 AND e.identity = ? "
            "ORDER BY e.start_time DESC, a.id DESC LIMIT 1", (identity,))
        with self.lock:
            self.conn.execute("DELETE FROM last_seen WHERE identity = ?", (identity,))
            self.conn.commit()
        if not rows:
            log.warning("память очищена: подтверждённых появлений больше не осталось")
            return None

        row = rows[0]
        route = [z for z in (row["zone_route"] or "").split(" → ") if z]
        zone = route[-1] if route else None
        card = {
            "identity": identity, "event_id": row["event_id"],
            "analysis_id": row["analysis_id"], "seen_at": row["started_at"],
            "seen_ts": row["start_time"], "camera": row["camera"], "zone": zone,
            "zone_name": config.ZONE_NAMES.get(zone, zone) if zone else None,
            "zone_route": " -> ".join(config.ZONE_NAMES.get(z, z) for z in route),
            "posture": row["posture"], "on_what": row["on_what"],
            "action": row["action"],
            "sleeping": None if row["sleeping"] is None else bool(row["sleeping"]),
            "toilet": row["toilet"], "danger": row["danger"],
            "summary": row["summary"], "folder": row["folder"],
            "frame_url": row["frame_url"],
        }
        self.save_last_seen(card)
        log.info("память переведена на предыдущее появление: %s, %s",
                 card["seen_at"], card["summary"])
        return card

    def purge_before(self, cutoff_ts):
        """Срок хранения: стирает события, начавшиеся раньше cutoff_ts, вместе
        с разборами, метками и задачами переоценки. Возвращает список папок
        стёртых событий — их файлы удаляет вызывающий.

        Порядок тот же, что в forget_event: сначала дети, потом строка
        события, иначе внешние ключи не пропустят.

        Событие, которое переоценивается ПРЯМО СЕЙЧАС (задача взята, но не
        закрыта), не трогаем: воркер дописывает в него разбор, и удалённая
        из-под него строка обернулась бы ошибкой внешнего ключа. Оно уйдёт
        при следующей проверке.

        Память «где его видели в последний раз» не трогаем: это одна строка
        с целой карточкой, и она должна отвечать, даже если он не
        показывался дольше срока хранения.

        Файл базы после удаления не сжимаем (VACUUM держал бы базу
        заблокированной, пока API читает журнал). Освободившиеся страницы
        SQLite сам займёт под новые записи, и база перестанет расти.
        """
        self._guard()
        old = ("SELECT event_id FROM events WHERE start_time < ? AND event_id NOT IN "
               "(SELECT event_id FROM recheck_queue "
               " WHERE started_at IS NOT NULL AND done_at IS NULL)")
        with self.lock:
            folders = [row["folder"] for row in
                       self.conn.execute(f"SELECT folder FROM events WHERE event_id IN ({old})",
                                         (cutoff_ts,)).fetchall()]
            self.conn.execute(
                "DELETE FROM labels WHERE analysis_id IN "
                f"(SELECT id FROM analyses WHERE event_id IN ({old}))", (cutoff_ts,))
            self.conn.execute(f"DELETE FROM analyses WHERE event_id IN ({old})",
                              (cutoff_ts,))
            self.conn.execute(f"DELETE FROM recheck_queue WHERE event_id IN ({old})",
                              (cutoff_ts,))
            self.conn.execute(f"DELETE FROM events WHERE event_id IN ({old})",
                              (cutoff_ts,))
            self.conn.commit()
        return folders

    def set_label(self, analysis_id, verdict, *, subject_found=None, posture=None,
                  on_what=None, action=None, objects=None, note=None, author=None):
        """verdict='correct' — ответ модели становится эталоном, правки не нужны.
        verdict='incorrect' — эталоном становится то, что передали в правках."""
        self._guard()
        if verdict not in ("correct", "incorrect"):
            raise ValueError("verdict должен быть 'correct' или 'incorrect'")
        with self.lock:
            cursor = self.conn.execute(
                "INSERT INTO labels (analysis_id, verdict, subject_found, posture, "
                "on_what, action, objects, note, author) VALUES (?,?,?,?,?,?,?,?,?)",
                (analysis_id, verdict,
                 None if subject_found is None else int(subject_found),
                 posture, on_what, action,
                 None if objects is None else json.dumps(objects, ensure_ascii=False),
                 note, author),
            )
            self.conn.commit()
            return cursor.lastrowid

    def soft_delete(self, event_id, deleted=True):
        """Прячем запись, но не теряем: набору данных нужны и неудачные примеры."""
        self._guard()
        with self.lock:
            self.conn.execute(
                "UPDATE events SET deleted_at = ? WHERE event_id = ?",
                (None if not deleted else __import__("datetime").datetime.now()
                 .strftime("%Y-%m-%d %H:%M:%S"), event_id))
            self.conn.commit()

    def request_recheck(self, event_id, author=None):
        self._guard()
        with self.lock:
            pending = self.conn.execute(
                "SELECT id FROM recheck_queue WHERE event_id = ? AND done_at IS NULL",
                (event_id,)).fetchone()
            if pending:
                return pending["id"]
            cursor = self.conn.execute(
                "INSERT INTO recheck_queue (event_id, author) VALUES (?,?)",
                (event_id, author))
            self.conn.commit()
            return cursor.lastrowid

    def pending_rechecks(self, limit=5):
        return self._rows(
            "SELECT * FROM recheck_queue WHERE done_at IS NULL ORDER BY id LIMIT ?",
            (limit,))

    def start_recheck(self, queue_id):
        """Задача взята в работу. Нужно, чтобы журнал различал «стоит в
        очереди» и «идёт прямо сейчас»: на глаз это разные вещи, и человек,
        нажавший кнопку, должен видеть, что дело сдвинулось."""
        self._guard()
        with self.lock:
            self.conn.execute(
                "UPDATE recheck_queue SET started_at = datetime('now','localtime') "
                "WHERE id = ?", (queue_id,))
            self.conn.commit()

    def finish_recheck(self, queue_id):
        self._guard()
        with self.lock:
            self.conn.execute(
                "UPDATE recheck_queue SET done_at = datetime('now','localtime') WHERE id = ?",
                (queue_id,))
            self.conn.commit()

    def recheck_state(self, event_id):
        """queued — стоит в очереди, running — разбирается сейчас,
        done — закончена. None — этой записи не переоценивали."""
        rows = self._rows(
            "SELECT requested_at, started_at, done_at FROM recheck_queue "
            "WHERE event_id = ? ORDER BY id DESC LIMIT 1", (event_id,))
        if not rows:
            return None
        row = rows[0]
        if row["done_at"]:
            return {"state": "done", "at": row["done_at"]}
        if row["started_at"]:
            return {"state": "running", "at": row["started_at"]}
        return {"state": "queued", "at": row["requested_at"]}

    # ---------- чтение ----------

    def _rows(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def event(self, event_id):
        rows = self._rows("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return rows[0] if rows else None

    def analyses(self, event_id):
        return self._rows(
            "SELECT * FROM analyses WHERE event_id = ? ORDER BY id", (event_id,))

    def recent(self, limit=50):
        return self._rows(
            "SELECT * FROM dataset ORDER BY analysis_id DESC LIMIT ?", (limit,))

    def unlabeled(self, limit=50):
        return self._rows(
            "SELECT * FROM dataset WHERE verdict IS NULL "
            "ORDER BY analysis_id DESC LIMIT ?", (limit,))

    def stats(self):
        counts = self._rows(
            "SELECT label_status AS status, COUNT(*) AS n FROM dataset GROUP BY label_status")
        postures = self._rows(
            "SELECT posture, COUNT(*) AS n FROM dataset WHERE subject_found = 1 "
            "GROUP BY posture ORDER BY n DESC")
        return {
            "событий": self._rows("SELECT COUNT(*) AS n FROM events")[0]["n"],
            "разборов": self._rows("SELECT COUNT(*) AS n FROM analyses")[0]["n"],
            "разметка": {r["status"]: r["n"] for r in counts},
            "позы": {r["posture"]: r["n"] for r in postures},
        }
