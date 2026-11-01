#!/usr/bin/env python3
"""Подсчёт данных Care Monitor: сколько видео на диске и сколько записей в базе.

Только читает: база открывается в режиме read-only, файлы не трогаются.
Папка с данными задаётся DEMO_DATA_DIR (по умолчанию ../db_backup), чтобы
данные можно было перенести куда угодно.

Запуск отдельно: python demo/inventory.py  -> печатает JSON.
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "DEMO_DATA_DIR", Path(__file__).resolve().parent.parent / "db_backup"))

# Пути в базе записаны так, как они лежат на малине.
SHARE_PREFIX = "/share/care_monitor/"


def _camera_from_folder(name):
    """«00-01-40_ezviz_e6_z7vn5l» -> «ezviz_e6»: время в начале, код в конце."""
    parts = name.split("_")
    return "_".join(parts[1:-1]) or "?"


def _local(path):
    """Путь из базы -> путь в DATA_DIR."""
    if not path:
        return None
    if path.startswith(SHARE_PREFIX):
        return DATA_DIR / path[len(SHARE_PREFIX):]
    return DATA_DIR / path


def scan_disk():
    """Обходит events/<день>/<событие>/ и считает ролики, кадры и объём.

    В папке события лежит основной ролик clip.mp4 и иногда короткий preview.mp4.
    """
    zero = lambda: {"folders": 0, "clips": 0, "previews": 0, "video_bytes": 0,
                    "frames": 0, "bytes": 0}
    days = defaultdict(zero)
    cameras = defaultdict(zero)
    folders = set()
    events_dir = DATA_DIR / "events"
    if not events_dir.is_dir():
        return {"days": {}, "cameras": {}, "folders": folders}

    for day in sorted(os.scandir(events_dir), key=lambda e: e.name):
        if not day.is_dir():
            continue
        for ev in os.scandir(day.path):
            if not ev.is_dir():
                continue
            folders.add(f"{day.name}/{ev.name}")
            for bucket in (days[day.name], cameras[_camera_from_folder(ev.name)]):
                bucket["folders"] += 1
            for root, _, files in os.walk(ev.path):
                for f in files:
                    size = os.stat(os.path.join(root, f)).st_size
                    kind = ("clips" if f == "clip.mp4" else
                            "previews" if f.endswith(".mp4") else
                            "frames" if f.endswith(".jpg") else None)
                    for bucket in (days[day.name], cameras[_camera_from_folder(ev.name)]):
                        bucket["bytes"] += size
                        if kind:
                            bucket[kind] += 1
                        if kind in ("clips", "previews"):
                            bucket["video_bytes"] += size
    return {"days": dict(days), "cameras": dict(cameras), "folders": folders}


def scan_db(disk_folders):
    db_path = DATA_DIR / "monitor.db"
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = lambda sql: con.execute(sql).fetchall()

    tables = {name: q(f'SELECT count(*) FROM "{name}"')[0][0]
              for (name,) in q("SELECT name FROM sqlite_master WHERE type='table' "
                               "AND name NOT LIKE 'sqlite_%' ORDER BY name")}

    days = defaultdict(lambda: {"events": 0, "analysed": 0, "labelled": 0})
    for day, n in q("SELECT substr(started_at,1,10), count(*) FROM events GROUP BY 1"):
        days[day]["events"] = n
    for day, n in q("SELECT substr(e.started_at,1,10), count(DISTINCT e.event_id) "
                    "FROM events e JOIN analyses a USING(event_id) GROUP BY 1"):
        days[day]["analysed"] = n
    for day, n in q("SELECT substr(e.started_at,1,10), count(DISTINCT a.event_id) "
                    "FROM labels l JOIN analyses a ON a.id = l.analysis_id "
                    "JOIN events e USING(event_id) GROUP BY 1"):
        days[day]["labelled"] = n

    cameras = defaultdict(lambda: {"events": 0, "analysed": 0})
    for cam, n in q("SELECT camera, count(*) FROM events GROUP BY 1"):
        cameras[cam]["events"] = n
    for cam, n in q("SELECT e.camera, count(DISTINCT e.event_id) "
                    "FROM events e JOIN analyses a USING(event_id) GROUP BY 1"):
        cameras[cam]["analysed"] = n

    # Сверка базы с диском.
    rows = q("SELECT folder, clip_path, deleted_at FROM events")
    with_folder = with_clip = deleted = 0
    db_folders = set()
    for folder, clip, deleted_at in rows:
        deleted += deleted_at is not None
        local = _local(folder)
        if local is not None:
            rel = local.relative_to(DATA_DIR / "events").as_posix() \
                if local.is_relative_to(DATA_DIR / "events") else None
            if rel:
                db_folders.add(rel)
            if local.is_dir():
                with_folder += 1
        clip_local = _local(clip)
        if clip_local is not None and clip_local.is_file():
            with_clip += 1

    first, last = q("SELECT min(started_at), max(started_at) FROM events")[0]
    analysed = q("SELECT count(DISTINCT event_id) FROM analyses")[0][0]
    con.close()
    return {
        "tables": tables,
        "days": dict(days),
        "cameras": dict(cameras),
        "period": {"first": first, "last": last},
        "check": {
            "rows": len(rows),
            "rows_with_folder": with_folder,
            "rows_with_clip": with_clip,
            "rows_deleted": deleted,
            "rows_without_folder": len(rows) - with_folder,
            "folders_without_row": len(disk_folders - db_folders),
            "analysed": analysed,
        },
    }


def other_files():
    """Всё, что лежит рядом с events: базы, логи, эталонные фото."""
    out = []
    for e in sorted(os.scandir(DATA_DIR), key=lambda e: e.name):
        if e.name == "events":
            continue
        if e.is_file():
            out.append({"name": e.name, "bytes": e.stat().st_size})
        elif e.is_dir():
            size = sum(os.stat(os.path.join(r, f)).st_size
                       for r, _, fs in os.walk(e.path) for f in fs)
            out.append({"name": e.name + "/", "bytes": size})
    return out


EMPTY_DISK = {"folders": 0, "clips": 0, "previews": 0, "video_bytes": 0,
              "frames": 0, "bytes": 0}


def collect():
    t0 = time.time()
    disk = scan_disk()
    db = scan_db(disk["folders"])

    all_days = sorted(set(disk["days"]) | set((db or {}).get("days", {})))
    days = [{"day": d,
             **disk["days"].get(d, EMPTY_DISK),
             **(db or {}).get("days", {}).get(d, {"events": 0, "analysed": 0,
                                                  "labelled": 0})}
            for d in all_days]

    all_cams = sorted(set(disk["cameras"]) | set((db or {}).get("cameras", {})))
    cameras = [{"camera": c,
                **disk["cameras"].get(c, EMPTY_DISK),
                **(db or {}).get("cameras", {}).get(c, {"events": 0, "analysed": 0})}
               for c in all_cams]

    files = other_files()
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_seconds": round(time.time() - t0, 1),
        "data_dir": str(DATA_DIR),
        "totals": {
            "clips": sum(d["clips"] for d in days),
            "previews": sum(d["previews"] for d in days),
            "video_bytes": sum(d["video_bytes"] for d in days),
            "folders": sum(d["folders"] for d in days),
            "frames": sum(d["frames"] for d in days),
            "events_bytes": sum(d["bytes"] for d in days),
            "other_bytes": sum(f["bytes"] for f in files),
        },
        "days": days,
        "cameras": cameras,
        "db": None if db is None else {k: db[k] for k in ("tables", "period", "check")},
        "files": files,
    }


if __name__ == "__main__":
    json.dump(collect(), sys.stdout, ensure_ascii=False, indent=1)
