#!/usr/bin/env python3
"""Точка входа аддона Care Journal API: настройки -> окружение -> journal_api.py."""
import json
import os

options = {}
if os.path.exists("/data/options.json"):
    with open("/data/options.json", encoding="utf-8") as f:
        options = json.load(f)

env = os.environ.copy()
env.update({
    "DB_PATH":        str(options.get("db_path", "/share/care_monitor/monitor.db")),
    "DATA_DIR":       str(options.get("data_dir", "/share/care_monitor/events")),
    "DEMO_DB_PATH":   str(options.get("demo_db_path", "/share/care_demo/demo.db")),
    "DEMO_DATA_DIR":  str(options.get("demo_data_dir", "/share/care_demo/events")),
    "PORT":           str(options.get("port", 8092)),
    "OWNER_LOGIN":    str(options.get("owner_login", "")),
    "OWNER_PASSWORD": str(options.get("owner_password", "")),
    "OWNER_NAME":     str(options.get("owner_name", "owner")),
    "SESSION_SECRET": str(options.get("session_secret", "")),
    "ALLOWED_ORIGIN": str(options.get("allowed_origin", "*")),
    "COOKIE_SECURE":  "1" if options.get("cookie_secure") else "0",
    "FRIGATE_URL":    str(options.get("frigate_url", "")),
    "VLM_URL":        str(options.get("vlm_url", "")),
    "LOG_FILE":       "",
})
guard = "по паролю" if options.get("owner_password") else "ВЫКЛЮЧЕН (все — владельцы)"
print(f"[run] Care Journal API 2.5.0, порт {env['PORT']}, база {env['DB_PATH']}, "
      f"вход {guard}", flush=True)
os.execvpe("python3", ["python3", "/app/journal_api.py"], env)
