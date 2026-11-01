#!/usr/bin/env python3
"""Лёгкая копия записи события — та, которую показывают на странице.

ЗАЧЕМ. Frigate отдаёт клип как есть с камеры: 2880x1620, до ста мегабайт на
трёхминутное событие, а с уличной камеры ещё и HEVC. В браузере это не
работает дважды. HEVC десктопный Chrome и Firefox не декодируют вовсе —
вместо видео чёрный прямоугольник. А сто мегабайт через домашний канал идут
минуты, и человек, который открыл журнал узнать, всё ли в порядке, просто
смотрит на крутилку.

Поэтому рядом с clip.mp4 кладём preview.mp4: H.264 (играет везде), 854
пикселя по ширине, без звука, moov в начале файла — плеер начинает
показывать с первых килобайт, не дожидаясь конца. На боевых записях выходит
в 10-15 раз меньше: 100 МБ -> 12 МБ, 40 МБ -> 2.7 МБ.

Оригинал никуда не девается и остаётся доступен ссылкой: превью — это для
«посмотреть, что было», а не замена записи.

ПОЧЕМУ В ОТДЕЛЬНОМ ПОТОКЕ. Перекодирование стоит десятки секунд процессора.
Разбор события — это тревога, и она не должна ждать удобства: пока мы жмём
видео, в стеке может стоять падение. Поэтому превью делается после того, как
карточка уже в базе, отдельным потоком и по одному за раз — чтобы не
съесть машину, на которой живёт весь дом.
"""
import logging
import os
import queue
import subprocess
import threading

import config

log = logging.getLogger("care.preview")

NAME = "preview.mp4"
_jobs = queue.Queue()
_worker = None
_lock = threading.Lock()


def path_for(folder):
    """Где лежит (или будет лежать) лёгкая копия для этой папки события."""
    return os.path.join(folder, NAME)


def ready(folder):
    """Есть ли готовая копия. Пустой файл — это недоделанная копия, не «есть»."""
    p = path_for(folder)
    try:
        return os.path.getsize(p) > 0
    except OSError:
        return False


def request(folder, short=""):
    """Поставить папку события в очередь. Возврат немедленный."""
    if not config.PREVIEW:
        return
    _start()
    _jobs.put((folder, short))


def make(folder, short="", force=False):
    """Сделать копию прямо здесь и сейчас. Возвращает путь или None.

    Этим же путём идёт добор старых событий: там очередь и поток не нужны,
    скрипт и так работает по одному.
    """
    clip = os.path.join(folder, "clip.mp4")
    out = path_for(folder)
    if not os.path.isfile(clip):
        return None
    if ready(folder) and not force:
        return out

    # Пишем во временный файл рядом и переименовываем в конце. Иначе
    # оборванное перекодирование оставит куцый preview.mp4, и сайт будет
    # честно показывать обрезанное видео как готовое.
    tmp = out + ".part"
    scale = f"scale='min({config.PREVIEW_WIDTH},iw)':-2"
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", clip,
         "-an",                                  # звук не нужен: hasAudio и так false
         "-vf", scale,
         "-c:v", "libx264", "-preset", str(config.PREVIEW_PRESET),
         "-crf", str(config.PREVIEW_CRF),
         "-pix_fmt", "yuv420p",                  # без этого старые плееры не берут
         "-movflags", "+faststart",
         # Временное имя оканчивается на .part, и по расширению ffmpeg
         # формат не угадает — называем его прямо.
         "-f", "mp4", tmp],
        check=False, capture_output=True, text=True,
    )
    if done.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        log.error("%s: превью не сделалось: %s", short or folder,
                  (done.stderr or "").strip()[:200] or f"код {done.returncode}")
        _drop(tmp)
        return None

    os.replace(tmp, out)
    was = os.path.getsize(clip)
    now = os.path.getsize(out)
    log.info("%s: превью готово, %.1f МБ -> %.1f МБ (в %.0f раз меньше)",
             short or folder, was / 1e6, now / 1e6, was / max(now, 1))
    return out


def _drop(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _start():
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="preview", daemon=True)
        _worker.start()


def _loop():
    while True:
        folder, short = _jobs.get()
        try:
            make(folder, short)
        except Exception:
            log.exception("превью сорвалось для %s", folder)
        finally:
            _jobs.task_done()
