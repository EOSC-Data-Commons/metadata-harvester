import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
from harvester.settings import current_settings

def setup_logging() -> None:
    settings = current_settings()
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "harvester.log"

    log_level = settings.LOG_LEVEL

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1024 * 1024 * 50,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    logging.basicConfig(
        level=log_level,
        handlers=[console_handler, file_handler],
        force=True
    )
