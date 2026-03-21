"""로깅 설정 - 파일 자동 순환(rotation) 지원"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime


def setup_logger(level: str = "INFO", log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    if root.handlers:
        root.handlers.clear()

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(fmt)
    root.addHandler(console)

    log_file = os.path.join(log_dir, "trading.log")
    fh = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh.setFormatter(fmt)
    root.addHandler(fh)

    for name in ("urllib3", "requests", "ccxt", "ccxt.base.exchange", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return root
