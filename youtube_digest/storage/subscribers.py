"""Subscriber configuration loading from YAML."""

import logging

import yaml

from ..config import CHANNEL_IDS, MAX_VIDEOS_PER_DAY, SUBSCRIBERS_PATH
from ..models import Subscriber

logger = logging.getLogger(__name__)


def load_subscribers() -> list[Subscriber]:
    """Load and validate subscribers from ``subscribers.yaml``."""
    if not SUBSCRIBERS_PATH.exists():
        logger.warning("Subscriber file not found: %s", SUBSCRIBERS_PATH)
        return []

    try:
        with open(SUBSCRIBERS_PATH, "r") as f:
            data = yaml.safe_load(f)

        subscribers: list[Subscriber] = []
        for sub in data.get("subscribers", []):
            invalid = [c for c in sub.get("channels", []) if c not in CHANNEL_IDS]
            if invalid:
                logger.warning(
                    "Subscriber %s has invalid channels: %s", sub["name"], invalid,
                )

            valid_channels = [c for c in sub.get("channels", []) if c in CHANNEL_IDS]
            default_max = MAX_VIDEOS_PER_DAY if MAX_VIDEOS_PER_DAY > 0 else 5

            subscribers.append(Subscriber(
                name=sub["name"],
                email=sub["email"],
                channels=valid_channels,
                max_videos=sub.get("max_videos", default_max),
            ))

        logger.info("Loaded %d subscriber(s)", len(subscribers))
        return subscribers

    except Exception as e:
        logger.error("Error loading subscribers: %s", e)
        return []
