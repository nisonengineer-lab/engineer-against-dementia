#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vlm_video_test.py — проверка разбора ВИДЕО через Qwen3-VL (Ollama).

Ollama сейчас не принимает видеофайл напрямую, поэтому:
  1) нарезаем видео на N равномерных кадров через ffmpeg;
  2) отправляем кадры пачкой как images -> модель читает их как клип (последовательность).

Отдельный файл, чтобы не трогать рабочий vlm_test.py.

Требует установленного ffmpeg (и ffprobe) в системе + pip install requests.

Использование:
  python3 vlm_video_test.py clip.mp4 --url http://OLLAMA_HOST_IP:11434
  python3 vlm_video_test.py clip.mp4 --frames 8 --height 512 --url http://OLLAMA_HOST_IP:11434
  python3 vlm_video_test.py --selftest        # проверка нарезки без сервера
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

import requests

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-vl:8b"
DEFAULT_FRAMES = 6          # минимум по требованию (ниже нельзя)
DEFAULT_HEIGHT = 512        # ради точности (можно снизить --height для скорости)
DEFAULT_CTX = 4096          # окно контекста
DEFAULT_NUM_PREDICT = 1024  # ПОТОЛОК, а не цель: лимит просто не даёт ответу оборваться -> пусто исключено
DEFAULT_PROMPT = (
    "Перед тобой кадры ПОДРЯД из короткого видео, по порядку во времени (от первого к последнему). "
    "Опиши по-русски ОДНОЙ короткой строкой, что сделал человек за клип — как запись в дневнике.\n"
    "ПРАВИЛА ТОЧНОСТИ (важно):\n"
    "- Пиши ТОЛЬКО то, что реально видно на кадрах. НЕ придумывай детали, которых не видно.\n"
    "- Если в последних кадрах человека уже не видно — так и напиши: «встал и вышел из кадра» "
    "или «ушёл из комнаты». НЕ угадывай, КУДА именно он пошёл (к столу, на кухню и т.п.), "
    "если это не очевидно на кадрах.\n"
    "- Лучше сказать общо, но ВЕРНО, чем точно, но ошибиться.\n"
    "- Без рассуждений, одна короткая строка."
)


# ----------------------------------------------------------------------
#                        НАРЕЗКА КАДРОВ (ffmpeg)
# ----------------------------------------------------------------------

def _need(tool: str):
    if shutil.which(tool) is None:
        sys.exit(f"[FAIL] Не найден {tool}. Установи ffmpeg (в него входит и ffprobe).")


def video_duration(path: str) -> float:
    """Длительность видео в секундах через ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(path: str, n: int, height: int) -> list[bytes]:
    """N равномерных кадров из видео -> список JPEG-байтов."""
    _need("ffmpeg")
    _need("ffprobe")
    if not os.path.exists(path):
        sys.exit(f"[FAIL] Нет файла: {path}")

    dur = video_duration(path)
    tmp = tempfile.mkdtemp(prefix="vlm_frames_")
    frames: list[bytes] = []
    try:
        for i in range(n):
            # момент кадра: середины равных интервалов (если длительность известна)
            ts = dur * (i + 0.5) / n if dur > 0 else i
            out = os.path.join(tmp, f"f{i:03d}.jpg")
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
                   "-ss", f"{ts:.3f}", "-i", path,
                   "-frames:v", "1", "-q:v", "3"]
            if height:
                cmd += ["-vf", f"scale=-2:{height}"]
            cmd += ["-y", out]
            subprocess.run(cmd, capture_output=True)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                with open(out, "rb") as f:
                    frames.append(f.read())
        return frames
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
#                            ЗАПРОС В LLM
# ----------------------------------------------------------------------

def build_payload(model: str, prompt: str, frames: list[bytes],
                  num_ctx: int = DEFAULT_CTX, num_predict: int = DEFAULT_NUM_PREDICT) -> dict:
    return {
        "model": model,
        "prompt": prompt,
        "images": [base64.b64encode(f).decode("ascii") for f in frames],
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": num_predict},
        "keep_alive": -1,          # держать модель в памяти -> не перегружать каждый вызов
    }


def analyze(url: str, model: str, prompt: str, video: str, n: int, height: int,
            num_ctx: int, num_predict: int) -> None:
    frames = extract_frames(video, n, height)
    if not frames:
        sys.exit("[FAIL] Не удалось извлечь ни одного кадра из видео.")
    print(f"... извлёк {len(frames)} кадров (h={height}), контекст {num_ctx}, "
          f"лимит ответа {num_predict}, отправляю в {model} на {url}")
    payload = build_payload(model, prompt, frames, num_ctx, num_predict)
    try:
        r = requests.post(f"{url}/api/generate", json=payload, timeout=300)
    except requests.RequestException as e:
        sys.exit(f"[FAIL] Сервер недоступен — вот ЭТО уже сетевая проблема: {e}")

    if r.status_code != 200:
        # сервер ответил, но отклонил запрос — покажем причину из тела ответа
        print(f"[FAIL] Ollama ответил {r.status_code} (сервер ЖИВ, но запрос отклонён).")
        print("Причина от сервера:", (r.text or "").strip()[:800])
        if "context" in (r.text or "").lower():
            print(f"\nПодсказка: {len(frames)} кадров не влезли в контекст. "
                  f"Подними окно:  --ctx {num_ctx * 2}   или уменьши  --frames / --height")
        sys.exit(1)

    data = r.json()

    resp = (data.get("response") or "").strip()
    p_in = data.get("prompt_eval_count")      # сколько токенов занял ввод (промпт + картинки)
    n_out = data.get("eval_count")            # сколько токенов сгенерила модель
    print("\n===== ОТВЕТ МОДЕЛИ (по клипу) =====")
    print(resp or "(пустой ответ)")
    print("===================================")
    if data.get("total_duration"):
        print(f"(время: {data['total_duration'] / 1e9:.1f} c на {len(frames)} кадров)")
    print(f"диагностика: ввод={p_in} токенов из num_ctx={num_ctx}, сгенерено={n_out} токенов, "
          f"done_reason={data.get('done_reason')}")
    if not resp:
        if data.get("done_reason") == "length":
            print("!! Модель ушла в 'thinking' и упёрлась в лимит до ответа (баг qwen3-vl+Ollama).")
            print(f"   Подними потолок:  --num-predict {max(1024, (n_out or 0) * 2)}   (и /no_think уже в промпте)")
        elif isinstance(p_in, int) and p_in >= num_ctx * 0.9:
            print("!! Картинки съели почти весь контекст — подними --ctx.")
        else:
            print("!! Пустой ответ при done_reason=%s — подними --num-predict." % data.get("done_reason"))


# ----------------------------------------------------------------------
#                              SELF-TEST
# ----------------------------------------------------------------------

def selftest() -> None:
    """Без сервера: генерим тестовое видео, режем, проверяем нарезку и сборку запроса."""
    _need("ffmpeg")
    tmp = tempfile.mkdtemp(prefix="vlm_selftest_")
    vid = os.path.join(tmp, "test.mp4")
    try:
        # 3-секундный ролик с движущимся счётчиком кадров (testsrc)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=duration=3:size=640x480:rate=15", "-y", vid],
            capture_output=True, check=True)
        assert os.path.exists(vid) and os.path.getsize(vid) > 0, "видео не создалось"

        dur = video_duration(vid)
        print(f"[OK] тестовое видео создано, длительность ~{dur:.1f} c")
        assert 2.0 < dur < 4.0, dur

        frames = extract_frames(vid, DEFAULT_FRAMES, DEFAULT_HEIGHT)
        print(f"[OK] извлечено кадров: {len(frames)} (просили {DEFAULT_FRAMES})")
        assert len(frames) == DEFAULT_FRAMES, len(frames)
        assert all(f[:2] == b"\xff\xd8" for f in frames), "кадры не JPEG"

        payload = build_payload(DEFAULT_MODEL, DEFAULT_PROMPT, frames)
        json.dumps(payload)   # сериализуется -> уйдёт по сети
        print(f"[OK] запрос собран: картинок в images = {len(payload['images'])}")
        print(">>> SELFTEST ПРОШЁЛ (нарезка и сборка ок; проверь на живом видео с --url)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
#                                MAIN
# ----------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Разбор видео через Qwen3-VL (кадры -> Ollama)")
    ap.add_argument("video", nargs="?", help="путь к видеофайлу")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="сколько кадров нарезать")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="высота кадра (ресайз)")
    ap.add_argument("--ctx", type=int, default=DEFAULT_CTX, help="размер контекста num_ctx")
    ap.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT,
                    dest="num_predict", help="лимит длины ответа (токенов)")
    ap.add_argument("--selftest", action="store_true", help="проверка нарезки без сервера")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if not args.video:
        ap.error("укажи путь к видео, либо --selftest")
    analyze(args.url, args.model, args.prompt, args.video,
            args.frames, args.height, args.ctx, args.num_predict)


if __name__ == "__main__":
    main()