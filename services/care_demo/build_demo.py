#!/usr/bin/env python3
"""Собирает демо-журнал: отдельная база demo.db + копии роликов в demo/data.

Источник — копия данных с малины (db_backup). Отбор событий и перевод лежат
рядом в demo_picks.json: для каждого события id и готовые английские строки.

Карточка события считается ТЕМ ЖЕ кодом, что и в настоящем журнале
(journal_api.event_dict) — виды, уровень, факты, цепочка. Потом все русские
строки заменяются по словарю, а ссылки на медиа переводятся на /demo/api.
В базе карточка лежит готовым JSON: демо статично, пересчитывать нечего.

Запуск: .venv/bin/python demo/build_demo.py
Результат: demo/data/demo.db и demo/data/events/<день>/<событие>/
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"
PICKS = json.load(open(HERE / "demo_picks.json", encoding="utf-8"))

sys.path.insert(0, str(HERE))
import journal  # noqa: E402  (копия с малины, только чтение, пути переведены)

ja = journal.journal_api
EN = PICKS["strings"]
MOVED = re.compile(r"^шёл ([\d.]+) с, стоял ([\d.]+) с$")
CYRILLIC = re.compile("[а-яё]", re.I)


def english(value):
    if isinstance(value, dict):
        return {english(k): english(v) for k, v in value.items()}
    if isinstance(value, list):
        return [english(v) for v in value]
    if isinstance(value, str) and CYRILLIC.search(value):
        moved = MOVED.match(value)
        if moved:
            return f"walked {moved[1]} s, stood {moved[2]} s"
        if value not in EN:
            raise SystemExit(f"нет перевода: {value!r}")
        return EN[value]
    return value


def make_preview(clip, preview):
    """Лёгкая копия H.264 — оригинал с камеры бывает HEVC, браузер его не играет."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-an",
                    "-vf", "scale=640:-2", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "28", "-movflags", "+faststart", str(preview)], check=True)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "events").mkdir(parents=True)
    db = sqlite3.connect(OUT / "demo.db")
    db.executescript("""
        CREATE TABLE cards (
            event_id   TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,      -- как в основной базе, локальное время
            start_ts   REAL NOT NULL,      -- для сортировки и курсора ленты
            camera     TEXT,
            kinds      TEXT NOT NULL,      -- JSON-список видов
            level      TEXT NOT NULL,
            folder     TEXT NOT NULL,      -- относительно DEMO_DATA_DIR
            card       TEXT NOT NULL       -- готовая карточка API, по-английски
        );
    """)

    for pick in PICKS["events"]:
        event_id = pick["id"]
        row = ja.rows(ja.FULL_ROW + " WHERE d.event_id = ? ORDER BY d.analysis_id DESC LIMIT 1",
                      (event_id,))[0]
        card = ja.event_dict(row, True)
        card.pop("recheck", None)

        src = Path(row["folder"])
        rel = f"{src.parent.name}/{src.name}"
        dst = OUT / "events" / rel
        dst.mkdir(parents=True)
        shutil.copy2(src / "clip.mp4", dst / "clip.mp4")
        shutil.copytree(src / "frames", dst / "frames")
        if (src / "preview.mp4").is_file():
            shutil.copy2(src / "preview.mp4", dst / "preview.mp4")
        else:
            make_preview(src / "clip.mp4", dst / "preview.mp4")

        card = english(card)
        card["person"] = {"id": "p1", "name": "Grandpa"}
        base = f"/demo/api/events/{event_id}/media"
        card["media"].update(poster=f"{base}/poster", clip=f"{base}/clip",
                             preview=f"{base}/preview")
        card["recheck"] = None
        db.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?)",
                   (event_id, row["started_at"], row["start_time"], row["camera"],
                    json.dumps(card["kinds"]), card["level"], rel,
                    json.dumps(card, ensure_ascii=False)))
        print(f"{pick['why']:<10} {row['started_at']}  {rel}")

    db.commit()
    db.close()
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"готово: {len(PICKS['events'])} событий, {size / 1e6:.0f} МБ в {OUT}")


if __name__ == "__main__":
    main()
