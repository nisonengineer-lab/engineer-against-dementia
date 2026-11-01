#!/usr/bin/env python3
"""Демо-сервер: мониторинг данных и демо-журнал.

  /             — сколько видео и записей (мониторинг)
  /journal/     — журнал, та же страница, что на основном сайте
  /api/...      — API журнала (care_api/journal_api.py) поверх копии данных

Только читает данные из DEMO_DATA_DIR. Страница журнала берётся из
репозитория сайта (DEMO_SITE_DIR) как есть. Слушает 127.0.0.1 — наружу его
выпускает туннель Cloudflare, напрямую из сети он не виден.

Запуск: .venv/bin/python demo/server.py  ->  http://127.0.0.1:8093
"""
import os
import re
import threading
import time
from pathlib import Path

from flask import Response, abort, jsonify, request, send_from_directory

import inventory
from journal import app

HERE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("DEMO_SITE_DIR", "../site"))
HOST = os.environ.get("DEMO_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEMO_PORT", 8093))
# Обход диска дешёвый, но не на каждый запрос: держим результат минуту.
CACHE_SECONDS = int(os.environ.get("DEMO_CACHE_SECONDS", 60))

_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _inventory(refresh=False):
    with _lock:
        if refresh or _cache["data"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
            _cache["data"] = inventory.collect()
            _cache["at"] = time.time()
        return _cache["data"]


@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/api/inventory")
def api_inventory():
    data = dict(_inventory(refresh=request.args.get("refresh") == "1"))
    # Путь на этой машине наружу не отдаём.
    data.pop("data_dir", None)
    return jsonify(data)


# ─── страница журнала с сайта ───

@app.get("/journal")
def journal_redirect():
    return app.redirect("/journal/")


@app.get("/journal/")
def journal_page():
    html = (SITE_DIR / "journal" / "index.html").read_text(encoding="utf-8")
    # На сайте страница ходит в боевой API. Здесь — в свой, на том же адресе.
    html = re.sub(r'(<meta name="api-base" content=")[^"]*(")', r"\1\2", html)
    return Response(html, mimetype="text/html")


@app.get("/<any(css, js, images, i18n):part>/<path:name>")
def site_static(part, name):
    return send_from_directory(SITE_DIR / part, name)


@app.get("/robots.txt")
def robots():
    # Страница журнала проверяет по нему, жив ли сам сайт.
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.get("/favicon.ico")
def favicon():
    abort(404)


if __name__ == "__main__":
    print(f"Данные: {inventory.DATA_DIR}")
    print(f"Сайт:   {SITE_DIR}")
    print(f"Мониторинг: http://{HOST}:{PORT}/   Журнал: http://{HOST}:{PORT}/journal/")
    app.run(host=HOST, port=PORT, threaded=True)
