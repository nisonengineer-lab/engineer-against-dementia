#!/usr/bin/env python3
"""Точка входа аддона Home Assistant.

Читает настройки аддона из /data/options.json, раскладывает их в переменные
окружения (их подхватывает config.py) и запускает main.py.

Всё, что должно пережить обновление аддона и быть доступно по Samba, лежит
в /share/care_monitor:
    monitor.db      база
    last_seen.json  где деда видели в последний раз — читается снаружи
    events/         видео и кадры по датам
    reference/      эталонные фото наблюдаемого — положи их сюда
    prompts/        по файлу на каждый вопрос к модели, правь без пересборки
    monitor.log     журнал
"""
import json
import os
import shutil
import time
import urllib.request

VERSION = "2.7.0"
SHARE = "/share/care_monitor"
OPTIONS = "/data/options.json"


def system_timezone():
    """Таймзону спрашиваем у Supervisor, иначе всё время будет в UTC."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        request = urllib.request.Request(
            "http://supervisor/info", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return (json.load(response).get("data") or {}).get("timezone")
    except Exception as error:
        print(f"[run] таймзону у Supervisor взять не вышло: {error}", flush=True)
        return None


if not os.environ.get("TZ"):
    zone = system_timezone()
    if zone:
        os.environ["TZ"] = zone
        print(f"[run] таймзона из Home Assistant: {zone}", flush=True)
try:
    time.tzset()
except AttributeError:
    pass

options = {}
if os.path.exists(OPTIONS):
    with open(OPTIONS, encoding="utf-8") as f:
        options = json.load(f)

os.makedirs(os.path.join(SHARE, "events"), exist_ok=True)
os.makedirs(os.path.join(SHARE, "reference"), exist_ok=True)

# Промпты кладём в /share по одному файлу на шаг цепочки, дальше их правит
# пользователь. Новые файлы (появившиеся с обновлением аддона) доносим, уже
# лежащие не трогаем — там могут быть правки владельца.
prompts = os.path.join(SHARE, "prompts")
os.makedirs(prompts, exist_ok=True)
brought = []
for name in sorted(os.listdir("/app/prompts")):
    target = os.path.join(prompts, name)
    if not os.path.exists(target):
        shutil.copy(os.path.join("/app/prompts", name), target)
        brought.append(name)
if brought:
    print(f"[run] промпты скопированы в {prompts}: {', '.join(brought)} — "
          f"правь их там", flush=True)

env = os.environ.copy()
env.update({
    "TARGET_IDENTITY":      str(options.get("target_identity", "Grandfather")),
    "TARGET_DESCRIPTION":   str(options.get("target_description", "")),
    "CAMERAS":              str(options.get("cameras", "")),
    "FRIGATE_URL":          str(options.get("frigate_url", "")),
    "ATTRIBUTE_FIELD":      str(options.get("attribute_field", "person_by_appearance")),

    "MQTT_HOST":            str(options.get("mqtt_host", "core-mosquitto")),
    "MQTT_PORT":            str(options.get("mqtt_port", 1883)),
    "MQTT_USER":            str(options.get("mqtt_user", "")),
    "MQTT_PASSWORD":        str(options.get("mqtt_password", "")),

    "VLM_URL":              str(options.get("vlm_url", "")),
    "VLM_MODEL":            str(options.get("vlm_model", "qwen3-vl:4b-instruct")),
    # Переключатель модели: qwen — кадры по одному, llama — склеенная сетка.
    "VLM_BACKEND":          str(options.get("vlm_backend", "qwen")),
    "VLM_LLAMA_MODEL":      str(options.get("vlm_llama_model", "llama3.2-vision:11b")),
    "GRID_WIDTH":           str(options.get("grid_width", 1120)),
    "VLM_VALIDATE_MODEL":   str(options.get("vlm_validate_model", "")),
    "VLM_CONTEXT":          str(options.get("vlm_context", 16384)),
    "VLM_TIMEOUT":          str(options.get("vlm_timeout", 300)),

    "FRAMES_PER_EVENT":     str(options.get("frames_per_event", 12)),
    "CLIP_TRIES":           str(options.get("clip_tries", 3)),
    "CLIP_RETRY_WAIT":      str(options.get("clip_retry_wait", 15)),
    "FRAME_WIDTH":          str(options.get("frame_width", 1280)),

    # Переключатель: резать кадр по наблюдаемому или ужимать целиком.
    "FRAME_MODE":           str(options.get("frame_mode", "crop")),
    "CROP_PAD":             str(options.get("crop_pad", 30)),
    "CROP_PAD_MIN":         str(options.get("crop_pad_min", 0.03)),
    "CROP_MIN":             str(options.get("crop_min", 0.22)),

    # Кадры целиком для вопросов про дверь: край выреза — не край комнаты,
    # и по обрезанному кадру «вошёл в дверь» от «ушёл за край» не отличить.
    # 0 — не делать, все шаги смотрят обрезанные кадры.
    "WIDE_FRAMES":          str(options.get("wide_frames", 8)),

    # Лёгкая копия записи для сайта: H.264, узкая, без звука. Оригинал с
    # камеры в браузере не работает — с уличной это HEVC, которого
    # десктопный Chrome не умеет, и до ста мегабайт на событие.
    "PREVIEW":              "true" if options.get("preview", True) else "false",
    "PREVIEW_WIDTH":        str(options.get("preview_width", 854)),
    "PREVIEW_CRF":          str(options.get("preview_crf", 30)),
    "PREVIEW_PRESET":       str(options.get("preview_preset", "veryfast")),

    "STEP_FRAMES":          str(options.get("step_frames", 6)),
    "VALIDATE_SUBJECT":     "true" if options.get("validate_subject", True) else "false",
    "VALIDATE_FRAMES":      str(options.get("validate_frames", 5)),
    "STEPS_OFF":            str(options.get("steps_off", "")),
    "MOVING_MIN_PATH":      str(options.get("moving_min_path", 0.06)),
    "QUEUE_SIZE":           str(options.get("queue_size", 50)),
    "LOOKBACK":             "true" if options.get("lookback", True) else "false",
    "LOOKBACK_HOURS":       str(options.get("lookback_hours", 24)),

    "ZONE_NAMES":           str(options.get("zone_names", "{}")),
    "CAMERA_ZONES":         str(options.get("camera_zones", "{}")),
    "EXIT_ZONES":           str(options.get("exit_zones", "[]")),
    "BED_ZONES":            str(options.get("bed_zones", "[]")),
    "SOFA_ZONES":           str(options.get("sofa_zones", "[]")),
    "SLEEP_ROOM_ZONES":     str(options.get("sleep_room_zones", "[]")),
    "TOILET_ZONES":         str(options.get("toilet_zones", "[]")),
    "DANGER_STILL_MINUTES": str(options.get("danger_still_minutes", 30)),

    # Сколько суток хранить события в базе и видео на диске; 0 — вечно.
    "RETENTION_DAYS":       str(options.get("retention_days", 3)),

    "LOG_LEVEL":            str(options.get("log_level", "INFO")),
    "LOG_HEARTBEAT_MIN":    str(options.get("log_heartbeat_min", 10)),

    # пути — всё в /share, чтобы пережило обновление аддона
    "DB_PATH":       os.path.join(SHARE, "monitor.db"),
    "LAST_SEEN_FILE": os.path.join(SHARE, "last_seen.json"),
    "DATA_DIR":      os.path.join(SHARE, "events"),
    "REFERENCE_DIR": os.path.join(SHARE, "reference"),
    "PROMPTS_DIR":   prompts,
    "LOG_FILE":      os.path.join(SHARE, "monitor.log"),
})

# Пустая настройка в интерфейсе аддона = «оставить значение из config.py».
# Иначе пустая строка приехала бы в config.py как пустой список зон, и шаги
# про сон, туалет и уход из дома молча перестали бы задаваться.
for key in ("ZONE_NAMES", "CAMERA_ZONES", "EXIT_ZONES", "BED_ZONES", "SOFA_ZONES",
            "SLEEP_ROOM_ZONES", "TOILET_ZONES", "STEPS_OFF"):
    if env.get(key, "").strip() in ("", "[]", "{}", "null"):
        env.pop(key, None)

references = len([n for n in os.listdir(os.path.join(SHARE, "reference"))
                  if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))])
if references == 0:
    print(f"[run] ВНИМАНИЕ: в {SHARE}/reference нет ни одного фото. "
          f"Модель будет узнавать человека только по описанию", flush=True)

print(f"[run] Care Monitor {VERSION}: цель={env['TARGET_IDENTITY']} "
      f"модель={env['VLM_LLAMA_MODEL'] + ' (llama)' if env['VLM_BACKEND'] == 'llama' else env['VLM_MODEL']} эталонов={references} база={env['DB_PATH']}", flush=True)

os.execvpe("python3", ["python3", "/app/main.py"], env)
