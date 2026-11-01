"""Демо-журнал: настоящий care_api/journal_api.py поверх копии данных.

Код журнала не копируется — берётся тот же модуль, что работает на малине,
поэтому ответы API в демо ровно такие, как на основном сайте. Отличия:

- база открыта ТОЛЬКО НА ЧТЕНИЕ. Обычный Database() при старте создаёт
  схему и дописывает колонки — на копии с малины этого быть не должно;
- пути в базе записаны как на малине (/share/care_monitor/...), здесь они
  переводятся в DEMO_DATA_DIR;
- всё, что пишет (оценка, переоценка, удаление), отвечает 403;
- /api/health не ходит в Frigate и модель: в демо их нет.
"""
import os
import sys
from pathlib import Path

from flask import jsonify, request

import inventory

CARE_API = Path(__file__).resolve().parent.parent / "care_api"
DB_PATH = inventory.DATA_DIR / "monitor.db"

# Окружение journal_api читает при импорте — задаём до него.
os.environ["DB_PATH"] = str(DB_PATH)
os.environ.setdefault("OWNER_LOGIN", os.environ.get("DEMO_OWNER_LOGIN", ""))
os.environ.setdefault("OWNER_PASSWORD", os.environ.get("DEMO_OWNER_PASSWORD", ""))
if os.environ["OWNER_PASSWORD"] and not os.environ.get("SESSION_SECRET"):
    # Без своего секрета подпись сессии известна всем, кто видел код.
    os.environ["SESSION_SECRET"] = os.urandom(32).hex()
sys.path.insert(0, str(CARE_API))

import storage  # noqa: E402  (care_api/storage)

PATH_COLUMNS = ("folder", "clip_path", "frames_dir")


class DemoDatabase(storage.Database):
    def __init__(self, path=None, readonly=True):
        super().__init__(str(DB_PATH), readonly=True)

    def _rows(self, sql, params=()):
        found = super()._rows(sql, params)
        for row in found:
            for name in PATH_COLUMNS:
                if row.get(name):
                    row[name] = str(inventory._local(row[name]))
        return found


storage.Database = DemoDatabase
import journal_api  # noqa: E402

app = journal_api.app

# Что разрешено менять в демо: только вход и выход.
WRITABLE = {"/api/session"}


@app.before_request
def read_only():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path not in WRITABLE:
        return jsonify({"ошибка": "демо: только просмотр"}), 403
    # Выгрузка для дообучения отдаёт пути к файлам на этой машине.
    if request.path == "/api/dataset":
        return jsonify({"ошибка": "в демо выгрузки нет"}), 403
    return None


def demo_health():
    try:
        total = journal_api.rows("SELECT COUNT(*) AS n FROM events")[0]["n"]
        part = {"name": "База", "ok": True, "why": f"событий {total} (демо, копия)"}
    except Exception as error:
        part = {"name": "База", "ok": False, "why": str(error)[:80]}
    return jsonify({"ok": part["ok"], "parts": [part]})


app.view_functions["health"] = demo_health
