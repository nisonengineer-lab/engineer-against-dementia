import logging
import logging.handlers
import os

import config

FORMAT = "%(asctime)s  %(levelname)-7s %(name)-7s %(message)s"
DATE = "%Y-%m-%d %H:%M:%S"


def setup():
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, DATE))
    root.addHandler(console)

    if config.LOG_FILE:
        os.makedirs(os.path.dirname(os.path.abspath(config.LOG_FILE)), exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=config.LOG_MAX_MB * 1024 * 1024,
            backupCount=config.LOG_BACKUPS,
            encoding="utf-8",
        )
        rotating.setFormatter(logging.Formatter(FORMAT, DATE))
        root.addHandler(rotating)

    logging.getLogger("main").info(
        "логи: уровень %s, файл %s", config.LOG_LEVEL, config.LOG_FILE or "нет")
