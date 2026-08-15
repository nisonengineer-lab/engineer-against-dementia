#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vlm_test.py — проверка, что Qwen3-VL реально видит и описывает фото через Ollama.
Это первый шаг: до всякой обвязки убеждаемся, что модель работает.

Использование:
  # проверить, что сервер жив и модель установлена:
  python3 vlm_test.py --check

  # описать одно фото:
  python3 vlm_test.py photo.jpg

  # свой промпт / другой хост / другая модель:
  python3 vlm_test.py photo.jpg --prompt "Что человек делает? Ест или нет?" \
                                --url http://OLLAMA_HOST_IP:11434 --model qwen3-vl:8b

  # проверить логику без сервера (сборка запроса, кодирование картинки):
  python3 vlm_test.py --selftest

Зависимости: pip install requests
"""

import argparse
import base64
import json
import sys

import requests

# По умолчанию — локально. Для сетевого доступа поставь http://<IP-бокса>:11434
DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-vl:8b"
DEFAULT_PROMPT = ("Ты смотришь на фото пожилого человека дома. Опиши ПРОСТО и коротко, "
                  "по-русски, что он делает. Если видишь улики (тарелка, еда, сигарета, "
                  "телефон) — упомяни их. Если не уверен — так и скажи.")


def b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def check_server(url: str, model: str) -> bool:
    """Сервер жив? Версия? Модель установлена?"""
    ok = True
    try:
        v = requests.get(f"{url}/api/version", timeout=5).json().get("version", "?")
        print(f"[OK]   Ollama доступен, версия {v}")
        if _ver_tuple(v) < (0, 12, 7):
            print(f"[ВНИМАНИЕ] Нужна Ollama >= 0.12.7 для qwen3-vl (у тебя {v})")
            ok = False
    except requests.RequestException as e:
        print(f"[FAIL] Ollama недоступен на {url}: {e}")
        print("       -> запущен ли сервис? верный ли IP/порт? открыт ли 11434?")
        return False
    try:
        tags = requests.get(f"{url}/api/tags", timeout=5).json().get("models", [])
        names = [m.get("name", "") for m in tags]
        if any(model in n or n.startswith(model.split(":")[0]) for n in names):
            print(f"[OK]   Модель установлена: {model}")
        else:
            print(f"[FAIL] Модель {model} не найдена. Установленные: {names or 'нет'}")
            print(f"       -> ollama pull {model}")
            ok = False
    except requests.RequestException as e:
        print(f"[FAIL] Не смог получить список моделей: {e}")
        ok = False
    return ok


def _ver_tuple(v: str):
    try:
        return tuple(int(x) for x in v.split("-")[0].split(".")[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


def build_payload(model: str, prompt: str, image_b64: str) -> dict:
    return {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": 0.1},
        "keep_alive": "10m",
    }


def describe(url: str, model: str, prompt: str, image_path: str) -> None:
    payload = build_payload(model, prompt, b64_image(image_path))
    print(f"... отправляю {image_path} в {model} на {url}")
    try:
        r = requests.post(f"{url}/api/generate", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"[FAIL] Запрос не прошёл: {e}")
        sys.exit(1)

    print("\n===== ОТВЕТ МОДЕЛИ =====")
    print(data.get("response", "").strip() or "(пустой ответ)")
    print("========================")
    # немного диагностики скорости
    total = data.get("total_duration")
    if total:
        print(f"(время: {total / 1e9:.1f} c)")


def selftest() -> None:
    """Без сервера: проверяем сборку запроса и кодирование картинки."""
    # 1x1 JPEG (минимальный валидный)
    tiny_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb00430008060607060508"
        "0707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720"
        "222c231c1c2837292c30313434341f27393d38323c2e333432ffc00011080001"
        "000103012200021101031101ffc4001f0000010501010101010100000000000000"
        "000102030405060708090a0bffc400b5100002010303020403050504040000017d"
        "01020300041105122131410613516107227114328191a1082342b1c11552d1f024"
        "33627282090a161718191a25262728292a3435363738393a434445464748494a53"
        "5455565758595a636465666768696a737475767778797a838485868788898a9293"
        "9495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9ca"
        "d2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda000c03"
        "01000211031100003f00f7fa28a2803fffd9")
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(tiny_jpeg)
        tmp = f.name
    try:
        b = b64_image(tmp)
        assert len(b) > 0 and isinstance(b, str)
        p = build_payload(DEFAULT_MODEL, DEFAULT_PROMPT, b)
        assert p["images"] and p["model"] == DEFAULT_MODEL
        assert p["stream"] is False
        # сериализуется в JSON (то, что реально уйдёт по сети):
        json.dumps(p)
        print("[OK] картинка кодируется в base64")
        print("[OK] запрос собирается и сериализуется в JSON")
        print(f"[OK] размер base64: {len(b)} символов, картинок в запросе: {len(p['images'])}")
        print(">>> SELFTEST ПРОШЁЛ (логика ок; теперь проверь на живом сервере: --check)")
    finally:
        os.remove(tmp)


def main() -> None:
    ap = argparse.ArgumentParser(description="Проверка Qwen3-VL через Ollama")
    ap.add_argument("image", nargs="?", help="путь к фото")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--check", action="store_true", help="проверить сервер и модель")
    ap.add_argument("--selftest", action="store_true", help="проверка логики без сервера")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if args.check:
        sys.exit(0 if check_server(args.url, args.model) else 1)
    if not args.image:
        ap.error("укажи путь к фото, либо --check / --selftest")
    describe(args.url, args.model, args.prompt, args.image)


if __name__ == "__main__":
    main()