"""YouTube Digest - Automated video summarization and email delivery."""

import logging
from logging.handlers import RotatingFileHandler

from .config import DATA_DIR

LOG_FILE = DATA_DIR / "youtube_digest.log"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to both stderr and a rotating log file."""
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
