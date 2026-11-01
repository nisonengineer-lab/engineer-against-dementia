#!/usr/bin/env python3
"""API журнала наблюдения поверх monitor.db.

Отвечает по договору, который уже ждёт сайт: те же адреса и те же имена полей,
что были у версии 1.2, но данные берутся из новой базы (события + разборы +
метки). Голос «верно/ложно» с сайта пишется в таблицу labels и сразу становится
эталоном для дообучения.

База общая с аддоном Care Monitor: WAL и busy_timeout разводят двух писателей.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, make_response, request, send_file

import config
from storage import Database

app = Flask(__name__)
app.json.ensure_ascii = False
db = Database()

PORT = int(os.environ.get("PORT", 8092))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", 20))
OWNER_LOGIN = os.environ.get("OWNER_LOGIN", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
OWNER_NAME = os.environ.get("OWNER_NAME", "owner")
SECRET = (os.environ.get("SESSION_SECRET") or "care-monitor-dev-secret").encode()
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
SESSION_DAYS = 30

# Виды событий — словарь для сайта. Порядок важен: чем выше, тем важнее.
KINDS = [
    {"id": "exit",   "label": "Уход из дома",               "level": "crit"},
    {"id": "fall",   "label": "Падение",                    "level": "crit"},
    {"id": "unsteady", "label": "Шаткая походка",           "level": "warn"},
    {"id": "night",  "label": "Ночная активность",          "level": "warn"},
    {"id": "long",   "label": "Долгая неподвижность",       "level": "warn"},
    {"id": "toilet", "label": "Туалет",                     "level": "ok"},
    {"id": "sleep",  "label": "Сон",                        "level": "ok"},
    {"id": "meal",   "label": "Приём пищи",                 "level": "ok"},
    {"id": "meds",   "label": "Лекарства",                  "level": "ok"},
    {"id": "quiet",  "label": "Спокойно",                   "level": "ok"},
]
DANGER_TO_LEVEL = {"высокая": "crit", "средняя": "warn", "низкая": "warn", "норма": "ok"}
LEVEL_TO_DANGERS = {"crit": ["высокая"], "warn": ["средняя", "низкая"], "ok": ["норма"]}

MEAL_WORDS = ("ест", "еда", "кушает", "тарелк", "ложк", "чашк", "кружк", "пьёт", "пьет")
MEDS_WORDS = ("лекарств", "таблетк", "капл", "блистер")

VOCABULARY = {
    # Положений ровно четыре. Оттенки вроде «наклонился» и «на корточках»
    # убраны: решение — на ногах он или завалился — от них не зависело, а
    # размечать их было нечем.
    "posture": ["идёт", "стоит", "сидит", "лежит", "не видно"],
    "on_what": ["пол", "кровать", "диван", "кресло", "стул", "земля", "унитаз",
                "не видно"],
}


def rows(sql, params=()):
    return db._rows(sql, params)


# ─────────────────────────── вход ───────────────────────────

def make_token(name):
    payload = f"{name}:{int(time.time())}"
    signature = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def read_token(token):
    try:
        name, stamp, signature = base64.urlsafe_b64decode(token).decode().rsplit(":", 2)
    except Exception:
        return None
    payload = f"{name}:{stamp}"
    expected = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    if time.time() - float(stamp) > SESSION_DAYS * 86400:
        return None
    return name


def current_user():
    """Пароль не задан — считаем всех владельцем. Так удобно, пока API внутри дома."""
    if not OWNER_PASSWORD:
        return OWNER_NAME
    token = request.cookies.get("cm_session")
    return read_token(token) if token else None


@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


@app.get("/api/session")
def session_get():
    user = current_user()
    return jsonify({"authed": user is not None,
                    "name": user,
                    "guest": user is None})


@app.post("/api/session")
def session_login():
    body = request.get_json(silent=True) or {}
    if not OWNER_PASSWORD:
        return jsonify({"authed": True, "name": OWNER_NAME})
    if body.get("login") != OWNER_LOGIN or body.get("password") != OWNER_PASSWORD:
        return jsonify({"authed": False, "ошибка": "неверный логин или пароль"}), 401
    response = make_response(jsonify({"authed": True, "name": OWNER_NAME}))
    response.set_cookie("cm_session", make_token(OWNER_NAME), httponly=True,
                        samesite="Lax", secure=COOKIE_SECURE,
                        max_age=SESSION_DAYS * 86400)
    return response


@app.delete("/api/session")
def session_logout():
    response = make_response(jsonify({"authed": False}))
    response.delete_cookie("cm_session")
    return response


# ─────────────────────────── здоровье ───────────────────────────

def reachable(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True, None
    except urllib.error.HTTPError:
        return True, None            # ответил, пусть и ошибкой — значит живой
    except Exception as error:
        return False, str(error)[:80]


@app.get("/api/health")
def health():
    parts = []
    try:
        total = rows("SELECT COUNT(*) AS n FROM events")[0]["n"]
        parts.append({"name": "База", "ok": True, "why": f"событий {total}"})
    except Exception as error:
        parts.append({"name": "База", "ok": False, "why": str(error)[:80]})

    ok, why = reachable(f"{config.FRIGATE_URL}/api/version")
    parts.append({"name": "Frigate", "ok": ok, "why": why})

    ok, why = reachable(f"{config.VLM_URL}/api/version")
    parts.append({"name": "Модель", "ok": ok, "why": why})

    fresh = rows("SELECT MAX(created_at) AS last FROM analyses")
    last = fresh[0]["last"] if fresh else None
    if last:
        age = datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        parts.append({"name": "Сборщик", "ok": age < timedelta(hours=12),
                      "why": f"последняя запись {last}"})
    else:
        parts.append({"name": "Сборщик", "ok": None, "why": "записей ещё нет"})

    return jsonify({"ok": all(p["ok"] is not False for p in parts), "parts": parts})


@app.get("/api/ping.png")
def ping_png():
    """1×1 прозрачный PNG. Картинка не подчиняется CORS — сайт так проверяет,
    жив ли API вообще, когда обычный запрос заблокирован браузером."""
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    return Response(pixel, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/kinds")
def kinds():
    return jsonify(KINDS)


@app.get("/api/vocabulary")
def vocabulary():
    return jsonify(VOCABULARY)


# ─────────────────────────── форма события ───────────────────────────

def iso(text):
    """'2026-08-27 22:55:13' -> ISO с локальной зоной, как ждёт сайт."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").astimezone().isoformat()
    except ValueError:
        return text


def bare(value):
    """Ответ без подписи из образца.

    В образце JSON поле «support» описано строкой «за что держится: стена,
    мебель, трость…», и модель иногда возвращает подпись вместе с ответом:
    «за что держится: стена». На экране это читается как поломка. Образец в
    prompts/gait.txt поправлен, но в базе такие строки уже лежат — чистим их
    на выдаче, чтобы старые записи выглядели как новые."""
    if not value:
        return value
    _, sep, tail = str(value).partition(":")
    return tail.strip() if sep and tail.strip() else str(value).strip()


def column(row, name):
    """Колонки шагов появились в версии 2.1: в старых рядах их может не быть."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def zones_of(row):
    return [z for z in (row["zone_route"] or "").split(" → ") if z]


def route_text(row):
    """В базе зоны лежат как в Frigate (yard), человеку показываем по-русски (двор)."""
    return " → ".join(config.ZONE_NAMES.get(z, z) for z in zones_of(row))


KIND_ORDER = [k["id"] for k in KINDS]      # порядок важности, он же порядок показа


def derive_kinds(row):
    """Виды события списком — отдельной колонки для них нет, выводим из полей.

    ЗА ОДНО СОБЫТИЕ ИХ МОЖЕТ БЫТЬ НЕСКОЛЬКО. Дед вышел из туалета и тут же
    сел есть — это и «туалет», и «приём пищи», и терять одно ради другого
    нельзя. Раньше здесь стояла лестница из return: первое совпадение
    выигрывало, остальное пропадало молча, и такое событие показывалось как
    «Приём пищи», будто в туалет он не заходил.

    Сначала смотрим на ответы отдельных шагов цепочки: там прямые «да/нет».
    Поиск по словам в описании остаётся запасным путём для старых разборов,
    сделанных до того, как вопросы стали задаваться по отдельности.
    """
    action = (row["action"] or "").lower()
    route = zones_of(row)
    found = set()

    if route and route[-1] in config.EXIT_ZONES:
        found.add("exit")
    if column(row, "fallen"):
        found.add("fall")
    # На полу — и лёжа, и сидя: упасть можно и на попу.
    if row["posture"] in ("лежит", "сидит") and row["on_what"] in ("пол", "земля"):
        found.add("fall")
    if column(row, "gait_risk") in ("шатается", "вот-вот упадёт"):
        found.add("unsteady")
    if column(row, "meds") or any(word in action for word in MEDS_WORDS):
        found.add("meds")
    if column(row, "eating") or column(row, "drinking"):
        found.add("meal")
    if column(row, "sleeping"):
        found.add("sleep")
    if column(row, "toilet"):
        found.add("toilet")
    if not found and any(word in action for word in MEAL_WORDS):
        found.add("meal")

    if found:
        return [k for k in KIND_ORDER if k in found]

    # Запасные виды — только когда не случилось ничего названного выше.
    # «Ночная активность» и «долгая неподвижность» — это не событие, а
    # обстоятельство; вешать их вторым ярлыком на каждый ночной поход в
    # туалет значит превратить ленту в кашу.
    if row["day_part"] == "ночь":
        return ["night"]
    if (row["still_s"] or 0) > config.DANGER_STILL_MINUTES * 60:
        return ["long"]
    return ["quiet"]


def derive_kind(row):
    """Главный вид — первый по важности. Он остаётся для тех, кто читает
    одно поле: уведомления, выгрузка, старые версии сайта."""
    return derive_kinds(row)[0]


def postures_of(row):
    """Положения за событие списком. В базе лежат JSON-строкой; у записей,
    снятых до 2.3.3, колонки нет вовсе — тогда список из одной позы, той
    самой, что и была."""
    raw = column(row, "postures")
    if raw:
        try:
            found = json.loads(raw)
            if isinstance(found, list) and found:
                return [str(p) for p in found]
        except ValueError:
            pass
    one = row["posture"]
    return [one] if one and one != "не видно" else []


def _yes_no(value):
    """NULL в базе значит «вопрос не задавали». Превращать его в false нельзя:
    «не спрашивали» и «нет» — разные вещи, и на экране они выглядят по-разному."""
    return None if value is None else bool(value)


def chain_of(row):
    """След цепочки для показа: какой вопрос задали, что ответила модель,
    сколько это заняло. Сырой ответ отдаём целиком — по нему и размечают."""
    raw = column(row, "steps")
    if not raw:
        return []
    try:
        steps = json.loads(raw)
    except ValueError:
        return []
    out = []
    for step in steps if isinstance(steps, list) else []:
        out.append({
            "step": step.get("step"),
            "title": step.get("title") or step.get("step"),
            "seconds": step.get("seconds"),
            "frames": step.get("frames"),
            "failed": bool(step.get("failed")),
            "answer": step.get("answer") or {},
        })
    return out


def event_dict(row, owner):
    event_id = row["event_id"]
    base = {
        "id": event_id,
        "startedAt": iso(row["started_at"]),
        "endedAt": iso(row["started_at"]) if not row["duration_s"] else
                   (datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
                    + timedelta(seconds=row["duration_s"])).astimezone().isoformat(),
        "durationSec": round(row["duration_s"] or 0),
        "ongoing": False,          # в базу попадают только завершённые события
        "kinds": derive_kinds(row),
        "kind": derive_kinds(row)[0],
        "level": DANGER_TO_LEVEL.get(row["danger"] or "", "ok"),
    }

    # ─── что видит и посторонний ───
    #
    # Отметки разбора и улики открыты всем: гостю карточка без них — это
    # время, длительность и слово «спокойно», то есть ничего. Показываем
    # то же, что владельцу: положения, походку, сон, еду, туалет, маршрут,
    # камеру и строку о том, почему выставлен уровень.
    #
    # ЗАКРЫТО ОСТАЁТСЯ: кадр и запись (media), разбор цепочки (chain,
    # summary, stepsAsked), голос и удаление (vote, analysisId), имя
    # наблюдаемого (person) и правка человека — свободный текст, в который
    # можно написать что угодно.
    base["facts"] = {
        "moving": _yes_no(column(row, "moving")),
        # posture — в чём человек остался под конец события. postures — все
        # положения за событие по порядку: за восемнадцать минут он успевает
        # и пройти, и посидеть, и одно слово об этом не рассказывает.
        "posture": row["posture"],
        "postures": postures_of(row),
        "gait": column(row, "gait"),
        "gaitSupport": bare(column(row, "gait_support")),
        "gaitRisk": column(row, "gait_risk"),
        "sleeping": _yes_no(column(row, "sleeping")),
        # Шевелился ли. Лежать и бодрствовать — отдельное состояние, а не
        # просто «не спит»: у сидящего «не спит» ожидаемо, у лежащего это
        # то, что стоит заметить.
        "sleepMoves": column(row, "sleep_moves"),
        "eating": _yes_no(column(row, "eating")),
        "drinking": _yes_no(column(row, "drinking")),
        "meds": _yes_no(column(row, "meds")),
        "toilet": column(row, "toilet"),
        # Падение — один вопрос с двумя ответами. Ни поверхности, ни своей
        # оценки опасности модель больше не даёт: чем короче ответ, тем он
        # точнее, а уровень тревоги всё равно считает код.
        "fallen": _yes_no(column(row, "fallen")),
    }
    base["dangerReason"] = column(row, "danger_reason") or ""

    # В уликах остаётся только то, чего нет в facts: маршрут, камера, как
    # опознан, метрики движения. Поза, походка, сон и еда уехали в facts —
    # дублировать их списком незачем.
    evidence = []
    if row["zone_route"]:
        evidence.append({"k": "маршрут", "v": route_text(row)})
    if row["camera"]:
        evidence.append({"k": "камера", "v": row["camera"], "id": row["camera"]})
    if row["identity_source"]:
        evidence.append({"k": "опознан",
                         "v": "по лицу" if row["identity_source"] == "face"
                              else "по внешности"})
    if row["moving_s"] is not None:
        evidence.append({"k": "движение",
                         "v": f"шёл {row['moving_s']} с, стоял {row['still_s']} с"})
    base["evidence"] = evidence

    if not owner:
        return base

    # ─── дальше только владельцу ───

    # Правка человека — свободный текст, и он не для посторонних.
    if row["note"]:
        base["evidence"] = evidence + [{"k": "правка", "v": row["note"]}]

    folder = row["folder"] or ""
    has_clip = bool(folder) and os.path.isfile(os.path.join(folder, "clip.mp4"))
    # Лёгкая копия. Её кладёт разбор рядом с клипом; у событий, снятых до
    # версии 2.3.2, её нет — тогда поле пустое, и сайт честно предупреждает,
    # что откроет тяжёлый оригинал.
    has_preview = bool(folder) and os.path.isfile(os.path.join(folder, "preview.mp4"))
    base["person"] = {"id": "p1", "name": row["identity"] or "—"}
    base["media"] = {
        "type": "video" if has_clip else "photo",
        "hasAudio": False,
        "poster": f"/api/events/{event_id}/media/poster",
        "clip": f"/api/events/{event_id}/media/clip" if has_clip else None,
        "preview": f"/api/events/{event_id}/media/preview" if has_preview else None,
        "clipBytes": os.path.getsize(os.path.join(folder, "clip.mp4")) if has_clip else 0,
    }

    # Крупным текстом карточки — то же, что видит гость: что человек делал,
    # обычной фразой. Служебная сводка разбора (маршрут | поза | отметки
    # шагов | действие) уходит отдельным полем: сайт показывает её под
    # раскрытием «Что спросили у модели», а не вместо человеческой строки.
    base["summary"] = column(row, "summary") or ""

    base["stepsAsked"] = column(row, "steps_asked") or 0
    base["chain"] = chain_of(row)

    base["vote"] = row["vote"]
    base["analysisId"] = row["analysis_id"]
    base["corrected"] = bool(row["has_corrections"])

    state = db.recheck_state(event_id)
    if state:
        base["recheck"] = state
    return base


FULL_ROW = """
SELECT d.*, e.identity, e.identity_source, e.duration_s, e.moving_s, e.still_s,
       e.start_time AS start_time, e.deleted_at AS del
  FROM dataset d JOIN events e ON e.event_id = d.event_id
"""


# ─────────────────────────── лента ───────────────────────────

@app.get("/api/events")
def events():
    owner = current_user() is not None
    args = request.args
    where, params = [], []

    where.append("e.deleted_at IS NOT NULL" if args.get("deleted") == "true"
                 else "e.deleted_at IS NULL")

    # ОДНА СТРОКА НА СОБЫТИЕ — САМЫЙ СВЕЖИЙ РАЗБОР.
    # Представление dataset даёт строку на КАЖДЫЙ разбор, а разборов у
    # события может быть несколько: переоценка дописывает новый, не затирая
    # старый. Без этого условия переоценённая запись показывалась в ленте
    # дважды — со свежим ответом и со старым.
    where.append("d.analysis_id = (SELECT MAX(id) FROM analyses "
                 "WHERE event_id = d.event_id)")

    if args.get("from"):
        where.append("d.started_at >= ?")
        params.append(args["from"] + " 00:00:00")
    if args.get("to"):
        where.append("d.started_at <= ?")
        params.append(args["to"] + " 23:59:59")
    if args.get("q"):
        where.append("(d.action LIKE ? OR d.zone_route LIKE ?)")
        params += [f"%{args['q']}%"] * 2
    if args.get("camera"):
        where.append("d.camera = ?")
        params.append(args["camera"])
    if args.get("minDuration"):
        where.append("e.duration_s >= ?")
        params.append(float(args["minDuration"]))

    levels = [lv for lv in (args.get("level") or "").split(",") if lv]
    if levels:
        dangers = [d for lv in levels for d in LEVEL_TO_DANGERS.get(lv, [])]
        if dangers:
            where.append("d.danger IN (%s)" % ",".join("?" * len(dangers)))
            params += dangers
        else:
            where.append("0")

    # Курсор по ВРЕМЕНИ СОБЫТИЯ, а не по номеру разбора.
    # Раньше и порядок, и курсор шли по analysis_id. Пока разбор был один на
    # событие, это совпадало со временем. Но переоценка создаёт новый разбор
    # с бОльшим номером — и вчерашняя запись прыгала в самый верх ленты,
    # как будто она только что случилась.
    if args.get("cursor"):
        mark = args["cursor"].split("|", 1)
        where.append("(e.start_time < ? OR (e.start_time = ? AND d.event_id < ?))")
        params += [float(mark[0]), float(mark[0]),
                   mark[1] if len(mark) > 1 else ""]

    limit = max(1, min(50, int(args.get("limit", 10))))
    wanted = set(k for k in (args.get("kind") or "").split(",") if k)

    clause = " WHERE " + " AND ".join(where)
    found = rows(FULL_ROW + clause +
                 " ORDER BY e.start_time DESC, d.event_id DESC LIMIT ?",
                 params + [limit * 4 if wanted else limit + 1])

    items, last_id = [], None
    for row in found:
        shaped = event_dict(row, owner)
        # Фильтр смотрит на ВСЕ виды записи, а не только на главный: событие
        # «вышел из туалета и поел» должно находиться и по «туалету», и по
        # «приёму пищи».
        if wanted and not (wanted & set(shaped["kinds"])):
            continue
        if len(items) >= limit:
            break
        items.append(shaped)
        last_id = str(row["start_time"]) + "|" + str(row["event_id"])

    has_more = len(found) > len(items)
    total = rows("SELECT COUNT(*) AS n FROM dataset d "
                 "JOIN events e ON e.event_id = d.event_id" + clause, params)[0]["n"]
    return jsonify(items=items,
                   nextCursor=last_id if has_more and last_id else None,
                   total=total)


@app.get("/api/events/stream")
def stream():
    """Живая лента: сервер сам сообщает о новых разборах, не дожидаясь опроса."""
    owner = current_user() is not None

    def generate():
        last = rows("SELECT MAX(analysis_id) AS m FROM dataset")[0]["m"] or 0
        yield "retry: 5000\n\n"
        while True:
            fresh = rows(FULL_ROW + " WHERE d.analysis_id > ? AND e.deleted_at IS NULL"
                                    " ORDER BY d.analysis_id", (last,))
            for row in fresh:
                last = row["analysis_id"]
                payload = json.dumps(event_dict(row, owner), ensure_ascii=False)
                yield f"event: entry\ndata: {payload}\n\n"
            yield ": ping\n\n"
            time.sleep(5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/events/<path:event_id>")
def one_event(event_id):
    found = rows(FULL_ROW + " WHERE d.event_id = ? ORDER BY d.analysis_id DESC LIMIT 1",
                 (event_id,))
    if not found:
        return jsonify({"ошибка": "нет такой записи"}), 404
    return jsonify(event_dict(found[0], current_user() is not None))


# ─────────────────────────── оценка ───────────────────────────

@app.post("/api/events/<path:event_id>/vote")
def vote(event_id):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    body = request.get_json(silent=True) or {}
    value = body.get("vote")
    if value not in (1, -1, 0):
        return jsonify({"ошибка": "vote должен быть 1, -1 или 0"}), 422

    found = rows("SELECT analysis_id FROM dataset WHERE event_id = ? "
                 "ORDER BY analysis_id DESC LIMIT 1", (event_id,))
    if not found:
        return jsonify({"ошибка": "нет такой записи"}), 404
    analysis_id = found[0]["analysis_id"]

    if value == 0:
        with db.lock:
            db.conn.execute("DELETE FROM labels WHERE analysis_id = ?", (analysis_id,))
            db.conn.commit()
    else:
        db.set_label(analysis_id,
                     "correct" if value == 1 else "incorrect",
                     posture=body.get("posture"),
                     on_what=body.get("on_what"),
                     action=body.get("action") or body.get("text"),
                     note=body.get("note"),
                     author=current_user())

    updated = rows(FULL_ROW + " WHERE d.event_id = ? ORDER BY d.analysis_id DESC LIMIT 1",
                   (event_id,))[0]
    return jsonify(event_dict(updated, True))


@app.post("/api/events/<path:event_id>/recheck")
def recheck(event_id):
    """Попросить модель пересмотреть запись.

    Здесь только постановка в очередь — разбирает её Care Monitor: у него
    ffmpeg, промпты и доступ к модели, а у API нет ничего из этого. Очередь
    общая, таблица recheck_queue, и она же служит тем самым временным
    хранилищем: пока у строки нет done_at, событие пересматривается.
    Отдельного списка id держать не надо.

    Повторное нажатие не плодит задачи: request_recheck вернёт ту же самую,
    если незакрытая уже есть.
    """
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    if not db.event(event_id):
        return jsonify({"ошибка": "нет такой записи"}), 404
    db.request_recheck(event_id, author=current_user())
    return jsonify(db.recheck_state(event_id) or {"state": "queued"})


@app.get("/api/events/<path:event_id>/recheck")
def recheck_state(event_id):
    """Что с переоценкой: queued, running, done или none.

    Когда done — сразу отдаём и свежую карточку. Иначе сайту пришлось бы
    после каждого «готово» ходить ещё раз за списком, а он в этот момент
    держит карточку свёрнутой и ждёт именно её.
    """
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    state = db.recheck_state(event_id) or {"state": "none"}
    if state["state"] == "done":
        found = rows(FULL_ROW + " WHERE d.event_id = ? AND e.deleted_at IS NULL "
                                "ORDER BY d.analysis_id DESC LIMIT 1", (event_id,))
        if found:
            state["event"] = event_dict(found[0], True)
    return jsonify(state)


@app.delete("/api/events/<path:event_id>")
def delete_event(event_id):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    if not db.event(event_id):
        return jsonify({"ошибка": "нет такой записи"}), 404
    db.soft_delete(event_id, True)
    return jsonify({"id": event_id, "deleted": True})


@app.post("/api/events/<path:event_id>/restore")
def restore_event(event_id):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    db.soft_delete(event_id, False)
    return jsonify({"id": event_id, "deleted": False})


# ─────────────────────────── медиа ───────────────────────────

def event_folder(event_id):
    found = db.event(event_id)
    return found["folder"] if found else None


def media_file(path, mimetype=None):
    """Отдача медиа события.

    conditional=True обязателен: без него нет ответов 206, а плеер тянет
    файл целиком с самого начала при каждой перемотке.

    Кэш — на сутки и обязательно private. Кадр и запись события не меняются
    никогда, второй просмотр должен быть мгновенным. Но лежат они за
    сессией, и общий кэш (прокси, CDN) хранить их не должен: отдал бы
    чужому.
    """
    # abspath обязателен: send_file считает относительный путь от корня
    # приложения (папки journal_api.py), а проверки выше — от рабочего
    # каталога. В базе путь папки лежит таким, каким его записал разбор, и
    # у отладочного запуска он относительный — эти двое расходятся, и
    # вместо честного 404 прилетает 500 из недр werkzeug.
    answer = send_file(os.path.abspath(path), mimetype=mimetype, conditional=True)
    answer.headers["Cache-Control"] = "private, max-age=86400"
    return answer


@app.get("/api/events/<path:event_id>/media/poster")
def poster(event_id):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    folder = event_folder(event_id)
    frames = os.path.join(folder or "", "frames")
    if os.path.isdir(frames):
        names = sorted(n for n in os.listdir(frames) if n.endswith(".jpg"))
        if names:
            return media_file(os.path.join(frames, names[len(names) // 2]))
    return jsonify({"ошибка": "кадров нет"}), 404


@app.get("/api/events/<path:event_id>/media/preview")
def preview(event_id):
    """Лёгкая копия: H.264, узкая, без звука — та, что играет на странице.

    Оригинал с камеры для показа не годится: с уличной камеры это HEVC,
    который десктопный браузер не декодирует, и до ста мегабайт на событие.
    """
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    path = os.path.join(event_folder(event_id) or "", "preview.mp4")
    if not os.path.isfile(path):
        return jsonify({"ошибка": "лёгкой копии нет"}), 404
    return media_file(path, mimetype="video/mp4")


@app.get("/api/events/<path:event_id>/media/clip")
def clip(event_id):
    """Оригинал как есть с камеры. Тяжёлый и не всегда играется в браузере —
    на странице показываем не его, а preview."""
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    path = os.path.join(event_folder(event_id) or "", "clip.mp4")
    if not os.path.isfile(path):
        return jsonify({"ошибка": "видео нет"}), 404
    return media_file(path, mimetype="video/mp4")


@app.get("/api/events/<path:event_id>/frames")
def frames(event_id):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    folder = os.path.join(event_folder(event_id) or "", "frames")
    if not os.path.isdir(folder):
        return jsonify([])
    return jsonify([f"/api/events/{event_id}/frames/{name}"
                    for name in sorted(os.listdir(folder)) if name.endswith(".jpg")])


@app.get("/api/events/<path:event_id>/frames/<name>")
def one_frame(event_id, name):
    if current_user() is None:
        return jsonify({"ошибка": "нужен вход"}), 401
    folder = os.path.abspath(os.path.join(event_folder(event_id) or "", "frames"))
    path = os.path.abspath(os.path.join(folder, name))
    if not path.startswith(folder + os.sep) or not os.path.isfile(path):
        return jsonify({"ошибка": "нет такого кадра"}), 404
    return media_file(path)


# ─────────────────────────── статистика и выгрузка ───────────────────────────

@app.get("/api/stats")
def stats():
    data = db.stats()
    marked = data["разметка"]
    good, bad = marked.get("подтверждено", 0), marked.get("исправлено", 0)
    data["точность модели"] = {
        "подтверждено": good, "исправлено": bad,
        "доля верных": round(good / (good + bad), 3) if good + bad else None}
    data["по камерам"] = {r["camera"]: r["n"] for r in rows(
        "SELECT camera, COUNT(*) AS n FROM events GROUP BY camera ORDER BY n DESC")}
    data["по времени суток"] = {r["day_part"]: r["n"] for r in rows(
        "SELECT day_part, COUNT(*) AS n FROM events GROUP BY day_part ORDER BY n DESC")}
    return jsonify(data)


@app.get("/api/last_seen")
def last_seen():
    """Где деда видели в последний раз. Отдельно от журнала: журнал отвечает
    «что было», эта точка — «где он может быть прямо сейчас»."""
    # Где человек находится — такая же личная вещь, как кадр с него.
    # Гостю не отдаём ничего, кроме признака, что данные есть у владельца.
    if not current_user():
        return jsonify({"known": False, "guest": True,
                        "text": "Доступно после входа"})

    known = db.last_seen()
    if not known:
        return jsonify({"known": False,
                        "text": "система его ещё ни разу не видела"})

    gap = time.time() - (known["seen_ts"] or 0)
    minutes = int(gap // 60)
    where = known["zone_name"] or "вне зон"
    answer = {
        "known": True,
        "seenAt": iso(known["seen_at"]),
        "agoMinutes": minutes,
        "where": where,
        "camera": known["camera"],
        "route": known["zone_route"],
        "posture": known["posture"],
        "sleeping": bool(known["sleeping"]) if known["sleeping"] is not None else None,
        "toilet": known["toilet"],
        "level": DANGER_TO_LEVEL.get(known["danger"] or "", "ok"),
        "eventId": known["event_id"],
        "analysisId": known["analysis_id"],
        "text": f"{where}, {known['posture'] or 'поза неизвестна'} — "
                f"{minutes} мин назад",
    }
    if known["event_id"]:
        answer["media"] = {"poster": f"/api/events/{known['event_id']}/media/poster"}
    return jsonify(answer)


@app.get("/api/stats/daily")
def stats_daily():
    return jsonify([dict(r) for r in rows(
        "SELECT substr(started_at,1,10) AS day, COUNT(*) AS n "
        "FROM dataset GROUP BY day ORDER BY day DESC LIMIT 60")])


@app.get("/api/dataset")
def dataset():
    """Выгрузка для дообучения. Берём только то, где эталон известен:
    подтверждённое и исправленное с правками. «Ложно» без правки — не берём:
    известно, что модель ошиблась, но не известно, как правильно."""
    items = rows("SELECT * FROM dataset WHERE deleted_at IS NULL AND ("
                 "verdict = 'correct' OR (verdict = 'incorrect' AND has_corrections = 1))"
                 " ORDER BY analysis_id")
    lines = []
    for row in items:
        folder = row["frames_dir"]
        frames_list = (sorted(os.path.join(folder, n) for n in os.listdir(folder))
                       if folder and os.path.isdir(folder) else [])
        lines.append(json.dumps({
            "analysis_id": row["analysis_id"], "frames": frames_list,
            "camera": row["camera"], "started_at": row["started_at"],
            "day_part": row["day_part"], "zone_route": row["zone_route"],
            "posture": row["posture"], "on_what": row["on_what"],
            "action": row["action"], "objects": row["objects"],
            "subject_found": row["subject_found"], "label_status": row["label_status"],
            # ответы отдельных шагов: по ним видно, какой вопрос был задан
            "moving": column(row, "moving"), "gait": column(row, "gait"),
            "gait_risk": column(row, "gait_risk"),
            "sleeping": column(row, "sleeping"), "eating": column(row, "eating"),
            "drinking": column(row, "drinking"), "meds": column(row, "meds"),
            "toilet": column(row, "toilet"), "fallen": column(row, "fallen"),
            "steps": json.loads(column(row, "steps") or "[]"),
        }, ensure_ascii=False))
    return Response("\n".join(lines), mimetype="application/x-ndjson")


# Демо-журнал (/demo, /demo/api/...): своя база и свои ролики, см. demo_api.py.
from demo_api import demo  # noqa: E402
app.register_blueprint(demo)


if __name__ == "__main__":
    print(f"[api] Care Journal API на порту {PORT}, база {config.DB_PATH}, "
          f"вход {'по паролю' if OWNER_PASSWORD else 'выключен (все — владельцы)'}",
          flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
