"""YouTube channel RSS feed fetching with retry logic."""

import logging
import random
import time
from typing import Optional

import feedparser
import requests

from ..config import CHANNELS, RSS_URL_TEMPLATE
from ..models import Video

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
RETRY_BASE_DELAY = 3  # seconds — doubles each attempt (3, 6, 12, 24)
RETRY_JITTER = 2  # random jitter up to this many seconds
INTER_FEED_DELAY = 1.0  # delay between channel fetches

REQUEST_TIMEOUT = 15
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; YouTubeDigest/1.0)",
    "Accept": "application/xml, text/xml, */*",
}


def get_recent_videos(channels: dict[str, str] | None = None) -> list[Video]:
    """Fetch recent videos from RSS feeds for the given channels.

    Args:
        channels: ``{channel_id: channel_name}`` mapping.  Defaults to all
                  registered channels if *None*.

    Returns:
        Combined list of :class:`Video` objects across all channels.
    """
    if channels is None:
        channels = CHANNELS

    videos: list[Video] = []
    failed_channels: list[str] = []
    channel_list = list(channels.items())

    for i, (channel_id, channel_name) in enumerate(channel_list):
        channel_videos = _fetch_channel_feed(channel_id, channel_name)
        if channel_videos is not None:
            videos.extend(channel_videos)
        else:
            failed_channels.append(channel_name)

        if i < len(channel_list) - 1:
            time.sleep(INTER_FEED_DELAY)

    if failed_channels and len(failed_channels) == len(channels):
        logger.error(
            "All %d channel feeds failed — YouTube may be having issues",
            len(channels),
        )
    elif failed_channels:
        logger.warning(
            "%d channel feed(s) failed: %s",
            len(failed_channels),
            ", ".join(failed_channels),
        )

    return videos


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_feed_xml(rss_url: str) -> str:
    """Fetch raw RSS XML using *requests* with proper headers.

    feedparser's built-in HTTP client sends minimal headers which can cause
    YouTube to return error pages during high-traffic hours.
    """
    resp = requests.get(rss_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _fetch_channel_feed(channel_id: str, channel_name: str) -> Optional[list[Video]]:
    """Fetch videos from a single channel's RSS feed (with retries)."""
    from datetime import datetime

    rss_url = RSS_URL_TEMPLATE.format(channel_id=channel_id)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            xml_content = _fetch_feed_xml(rss_url)
            feed = feedparser.parse(xml_content)

            if feed.bozo and len(feed.entries) == 0:
                if attempt < MAX_RETRIES:
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "Feed error for %s (attempt %d/%d): %s — retrying in %.1fs",
                        channel_name, attempt, MAX_RETRIES, feed.bozo_exception, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    "Feed error for %s (all %d attempts failed): %s",
                    channel_name, MAX_RETRIES, feed.bozo_exception,
                )
                return None

            if feed.bozo and len(feed.entries) > 0:
                logger.warning(
                    "Feed parse warning for %s (got %d entries): %s",
                    channel_name, len(feed.entries), feed.bozo_exception,
                )

            videos: list[Video] = []
            for entry in feed.entries:
                video_id = entry.get("yt_videoid", "")
                if not video_id:
                    link = entry.get("link", "")
                    if "v=" in link:
                        video_id = link.split("v=")[-1].split("&")[0]
                if not video_id:
                    continue

                published_at = None
                if "published_parsed" in entry and entry.published_parsed:
                    try:
                        published_at = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass

                thumbnail_url = ""
                if "media_thumbnail" in entry and entry.media_thumbnail:
                    thumbnail_url = entry.media_thumbnail[0].get("url", "")

                videos.append(Video(
                    video_id=video_id,
                    title=entry.get("title", "Untitled"),
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=channel_id,
                    channel_name=channel_name,
                    published_at=published_at,
                    thumbnail_url=thumbnail_url,
                ))

            return videos

        except Exception as e:
            if attempt < MAX_RETRIES:
                delay = _retry_delay(attempt)
                logger.warning(
                    "Error checking %s (attempt %d/%d): %s — retrying in %.1fs",
                    channel_name, attempt, MAX_RETRIES, e, delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Error checking %s (all %d attempts failed): %s",
                    channel_name, MAX_RETRIES, e,
                )
                return None

    return None


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter."""
    backoff = RETRY_BASE_DELAY * (2 ** (attempt - 1))
    jitter = random.uniform(0, RETRY_JITTER)
    return backoff + jitter
