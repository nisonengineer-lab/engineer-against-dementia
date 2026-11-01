"""Демо-журнал: /demo/api/... — те же адреса, что у настоящего журнала.

Данные — отдельная база DEMO_DB_PATH (собирает demo/build_demo.py) и копии
роликов в DEMO_DATA_DIR. С живой базой Care Monitor демо не пересекается.

Карточки в базе уже готовые и по-английски, поэтому здесь нет разбора и
пересчёта: только выборка, фильтры и отдача файлов.

Всё открыто без входа, включая кадры и видео: в демо лежат только отобранные
вручную ролики. Всё, что меняет данные (оценка, переоценка, удаление),
отвечает 403.

Страница демо — /demo/ — берётся из папки demo_site рядом с этим файлом.
"""
import json
import os
import sqlite3
import threading
import time

from flask import Blueprint, Response, jsonify, request, send_file, send_from_directory

DEMO_DB_PATH = os.environ.get("DEMO_DB_PATH", "/share/care_demo/demo.db")
DEMO_DATA_DIR = os.environ.get("DEMO_DATA_DIR", "/share/care_demo/events")
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_site")

demo = Blueprint("demo", __name__)

KINDS = [
    {"id": "exit",     "label": "Left the house",     "level": "crit"},
    {"id": "fall",     "label": "Fall",               "level": "crit"},
    {"id": "unsteady", "label": "Unsteady gait",      "level": "warn"},
    {"id": "night",    "label": "Night activity",     "level": "warn"},
    {"id": "long",     "label": "Long inactivity",    "level": "warn"},
    {"id": "toilet",   "label": "Toilet",             "level": "ok"},
    {"id": "sleep",    "label": "Sleep",              "level": "ok"},
    {"id": "meal",     "label": "Eating or drinking", "level": "ok"},
    {"id": "meds",     "label": "Medication",         "level": "ok"},
    {"id": "quiet",    "label": "Quiet",              "level": "ok"},
]
VOCABULARY = {
    "posture": ["walking", "standing", "sitting", "lying", "not visible"],
    "on_what": ["floor", "bed", "sofa", "armchair", "chair", "ground", "toilet",
                "not visible"],
}

_local = threading.local()


def _db():
    """Своё соединение на поток, только чтение. Нет базы — демо выключено."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        if not os.path.isfile(DEMO_DB_PATH):
            return None
        conn = sqlite3.connect(f"file:{os.path.abspath(DEMO_DB_PATH)}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def _rows(sql, params=()):
    conn = _db()
    return conn.execute(sql, params).fetchall() if conn else []


def _no_demo():
    return jsonify({"error": "demo data is not installed"}), 404


def _media(path, mimetype=None):
    """206 для перемотки и кэш на сутки: файлы демо не меняются."""
    answer = send_file(os.path.abspath(path), mimetype=mimetype, conditional=True)
    answer.headers["Cache-Control"] = "public, max-age=86400"
    return answer


def _folder(event_id):
    found = _rows("SELECT folder FROM cards WHERE event_id = ?", (event_id,))
    return os.path.join(DEMO_DATA_DIR, found[0]["folder"]) if found else None


# ─────────────────────────── страница ───────────────────────────

@demo.get("/demo")
def page_redirect():
    return Response(status=301, headers={"Location": "/demo/"})


@demo.get("/demo/")
def page():
    return send_from_directory(SITE_DIR, "index.html")


@demo.get("/demo/<path:name>")
def page_files(name):
    if name.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(SITE_DIR, name)


# ─────────────────────────── служебное ───────────────────────────

@demo.route("/demo/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


@demo.route("/demo/api/<path:_any>", methods=["POST", "PUT", "PATCH", "DELETE"])
def read_only(_any):
    # Вход «удаётся» сразу: в демо смотреть можно всё и без него.
    if _any == "session" and request.method == "POST":
        return jsonify({"authed": True, "name": "demo"})
    if _any == "session" and request.method == "DELETE":
        return jsonify({"authed": False})
    return jsonify({"error": "demo is read-only"}), 403


@demo.get("/demo/api/session")
def session():
    return jsonify({"authed": True, "name": "demo", "guest": False, "demo": True})


@demo.get("/demo/api/health")
def health():
    total = _rows("SELECT COUNT(*) AS n FROM cards")
    ok = bool(total)
    return jsonify({"ok": ok, "parts": [{
        "name": "Demo data", "ok": ok,
        "why": f"{total[0]['n']} events" if ok else "demo.db not found"}]})


@demo.get("/demo/api/ping.png")
def ping_png():
    from journal_api import ping_png as real
    return real()


@demo.get("/demo/api/kinds")
def kinds():
    return jsonify(KINDS)


@demo.get("/demo/api/vocabulary")
def vocabulary():
    return jsonify(VOCABULARY)


# ─────────────────────────── лента ───────────────────────────

@demo.get("/demo/api/events")
def events():
    if _db() is None:
        return _no_demo()
    args = request.args
    if args.get("deleted") == "true":
        return jsonify(items=[], nextCursor=None, total=0)

    where, params = ["1"], []
    if args.get("from"):
        where.append("started_at >= ?")
        params.append(args["from"] + " 00:00:00")
    if args.get("to"):
        where.append("started_at <= ?")
        params.append(args["to"] + " 23:59:59")
    if args.get("camera"):
        where.append("camera = ?")
        params.append(args["camera"])
    levels = [lv for lv in (args.get("level") or "").split(",") if lv]
    if levels:
        where.append("level IN (%s)" % ",".join("?" * len(levels)))
        params += levels
    if args.get("q"):
        where.append("card LIKE ?")
        params.append(f"%{args['q']}%")
    if args.get("cursor"):
        mark = args["cursor"].split("|", 1)
        where.append("(start_ts < ? OR (start_ts = ? AND event_id < ?))")
        params += [float(mark[0]), float(mark[0]), mark[1] if len(mark) > 1 else ""]

    wanted = set(k for k in (args.get("kind") or "").split(",") if k)
    limit = max(1, min(50, int(args.get("limit", 10))))
    found = _rows("SELECT * FROM cards WHERE " + " AND ".join(where) +
                  " ORDER BY start_ts DESC, event_id DESC", params)
    matched = [r for r in found if not wanted or wanted & set(json.loads(r["kinds"]))]
    page = matched[:limit]
    more = len(matched) > limit
    last = page[-1] if page else None
    return jsonify(items=[json.loads(r["card"]) for r in page],
                   nextCursor=f"{last['start_ts']}|{last['event_id']}" if more and last else None,
                   total=len(matched))


@demo.get("/demo/api/events/stream")
def stream():
    """Демо не пополняется: держим соединение и только пингуем."""
    def generate():
        yield "retry: 60000\n\n"
        while True:
            yield ": ping\n\n"
            time.sleep(25)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@demo.get("/demo/api/events/<path:event_id>")
def one_event(event_id):
    found = _rows("SELECT card FROM cards WHERE event_id = ?", (event_id,))
    if not found:
        return jsonify({"error": "no such event"}), 404
    return jsonify(json.loads(found[0]["card"]))


@demo.get("/demo/api/events/<path:event_id>/recheck")
def recheck_state(event_id):
    return jsonify({"state": "none"})


# ─────────────────────────── медиа ───────────────────────────

@demo.get("/demo/api/events/<path:event_id>/media/poster")
def poster(event_id):
    frames = os.path.join(_folder(event_id) or "", "frames")
    if os.path.isdir(frames):
        names = sorted(n for n in os.listdir(frames) if n.endswith(".jpg"))
        if names:
            return _media(os.path.join(frames, names[len(names) // 2]))
    return jsonify({"error": "no frames"}), 404


@demo.get("/demo/api/events/<path:event_id>/media/<kind>")
def video(event_id, kind):
    if kind not in ("preview", "clip"):
        return jsonify({"error": "not found"}), 404
    path = os.path.join(_folder(event_id) or "", f"{kind}.mp4")
    if not os.path.isfile(path):
        return jsonify({"error": "no video"}), 404
    return _media(path, mimetype="video/mp4")


@demo.get("/demo/api/events/<path:event_id>/frames")
def frames(event_id):
    folder = os.path.join(_folder(event_id) or "", "frames")
    if not os.path.isdir(folder):
        return jsonify([])
    return jsonify([f"/demo/api/events/{event_id}/frames/{name}"
                    for name in sorted(os.listdir(folder)) if name.endswith(".jpg")])


@demo.get("/demo/api/events/<path:event_id>/frames/<name>")
def one_frame(event_id, name):
    folder = os.path.abspath(os.path.join(_folder(event_id) or "", "frames"))
    path = os.path.abspath(os.path.join(folder, name))
    if not path.startswith(folder + os.sep) or not os.path.isfile(path):
        return jsonify({"error": "no such frame"}), 404
    return _media(path)


# ─────────────────────────── «где он сейчас» и статистика ───────────────────────────

@demo.get("/demo/api/last_seen")
def last_seen():
    found = _rows("SELECT card FROM cards ORDER BY start_ts DESC LIMIT 1")
    if not found:
        return jsonify({"known": False, "text": "no demo data"})
    card = json.loads(found[0]["card"])
    route = next((e["v"] for e in card["evidence"] if e["k"] == "route"), "")
    where = route.split(" → ")[-1] if route else "outside the zones"
    posture = card["facts"].get("posture") or "posture unknown"
    return jsonify({
        "known": True, "demo": True,
        "seenAt": card["startedAt"], "agoMinutes": None,
        "where": where, "camera": next((e["v"] for e in card["evidence"]
                                        if e["k"] == "camera"), None),
        "route": route, "posture": card["facts"].get("posture"),
        "sleeping": card["facts"].get("sleeping"), "toilet": card["facts"].get("toilet"),
        "level": card["level"], "eventId": card["id"],
        "text": f"{where}, {posture} — last demo event",
        "media": {"poster": card["media"]["poster"]},
    })


@demo.get("/demo/api/stats/daily")
def stats_daily():
    return jsonify([dict(r) for r in _rows(
        "SELECT substr(started_at,1,10) AS day, COUNT(*) AS n "
        "FROM cards GROUP BY day ORDER BY day DESC")])
