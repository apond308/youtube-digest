"""Main orchestration pipeline for YouTube Digest.

Coordinates the full workflow: fetch feeds -> process videos -> deliver digests.
"""

import logging
import re
from datetime import datetime
from typing import Optional

import requests

from .config import CHANNEL_IDS, GMAIL_ADDRESS
from .models import ProcessedVideo, Subscriber, Video
from .services.feed import get_recent_videos
from .services.transcript import get_video_info
from .services.summarizer import LLMAuthenticationError, summarize_transcript
from .delivery.email import send_digest_email, send_error_notification
from .delivery.archive import archive_summary, get_archived_summary
from .storage.database import get_sent_video_ids, mark_sent
from .storage.subscribers import load_subscribers

logger = logging.getLogger(__name__)

_video_cache: dict[str, ProcessedVideo] = {}


def main() -> int:
    """Run the full digest pipeline.  Returns 0 on success, 1 on errors."""
    logger.info(
        "YouTube Digest started at %s",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    subscribers = load_subscribers()
    if not subscribers:
        logger.warning("No subscribers configured")
        return 1

    owner_email = subscribers[0].email or GMAIL_ADDRESS
    feed_videos = _fetch_all_feeds(subscribers)

    total_success = 0
    total_errors = 0

    for subscriber in subscribers:
        success, errors = _process_subscriber(subscriber, feed_videos, owner_email)
        total_success += success
        total_errors += errors

    if total_success == 0 and total_errors == 0:
        logger.info("No new videos found for any subscriber")

    logger.info("Complete: %d success, %d errors", total_success, total_errors)
    return 0 if total_errors == 0 else 1


def summarize_single_video(
    video_url: str,
    subscriber: Subscriber,
    owner_email: Optional[str] = None,
) -> bool:
    """Process and send one on-demand YouTube video to a subscriber."""
    owner = owner_email or subscriber.email or GMAIL_ADDRESS

    video = _build_video_from_url(video_url)
    if not video:
        logger.error("Invalid YouTube URL for on-demand summary: %s", video_url)
        return False

    sent_ids = get_sent_video_ids(subscriber.email)
    if video.video_id in sent_ids:
        logger.info(
            "On-demand video already sent to %s: %s",
            subscriber.email,
            video.video_id,
        )
        return True

    try:
        processed = _fetch_and_summarize(video)
    except LLMAuthenticationError:
        send_error_notification(
            error_type="LLM Authentication Failed",
            details="OAuth token has expired. On-demand summary could not be processed.",
            owner_email=owner,
            video_title=video.title,
            video_url=video.video_url,
        )
        return False

    if processed.status == "error":
        send_error_notification(
            error_type="On-Demand Summarization Failed",
            details=f"Failed to process {video.video_url}",
            owner_email=owner,
            video_title=video.title,
            video_url=video.video_url,
        )
        return False

    if processed.status == "no_transcript":
        mark_sent(
            video_id=video.video_id,
            subscriber_email=subscriber.email,
            channel_id=video.channel_id,
            channel_name=video.channel_name,
            title=video.title,
            video_url=video.video_url,
            published_at=video.published_at,
            status="skipped",
        )
        logger.info("On-demand video has no usable transcript: %s", video.video_url)
        return False

    return _send_to_subscriber(processed, subscriber, owner)


def _fetch_all_feeds(subscribers: list[Subscriber]) -> list[Video]:
    """Fetch RSS feeds for all unique channels across subscribers (once)."""
    all_channels: dict[str, str] = {}
    for subscriber in subscribers:
        all_channels.update(subscriber.get_channel_ids(CHANNEL_IDS))

    if not all_channels:
        return []

    logger.info("Fetching feeds for %d unique channel(s)", len(all_channels))
    return get_recent_videos(all_channels)


def _build_video_from_url(video_url: str) -> Optional[Video]:
    """Build a minimal Video object for an on-demand request."""
    video_id = _extract_video_id(video_url)
    if not video_id:
        return None

    normalized_url = f"https://www.youtube.com/watch?v={video_id}"
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    title = f"YouTube Video ({video_id})"
    channel_name = "YouTube"

    # Use oEmbed metadata for a better title/channel when available.
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": normalized_url, "format": "json"},
            timeout=10,
        )
        response.raise_for_status()
        metadata = response.json()
        title = metadata.get("title", title)
        channel_name = metadata.get("author_name", channel_name)
    except Exception:
        logger.debug("Unable to fetch oEmbed metadata for %s", normalized_url)

    return Video(
        video_id=video_id,
        title=title,
        video_url=normalized_url,
        channel_id=channel_name,
        channel_name=channel_name,
        published_at=None,
        thumbnail_url=thumbnail_url,
    )


def _extract_video_id(url: str) -> Optional[str]:
    """Extract an 11-character YouTube video ID from URL variants."""
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)

    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)

    match = re.search(r"embed/([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)

    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url

    return None


def _process_subscriber(
    subscriber: Subscriber,
    feed_videos: list[Video],
    owner_email: str,
) -> tuple[int, int]:
    """Process and deliver videos for one subscriber.

    Returns (success_count, error_count).
    """
    logger.info(
        "Processing subscriber: %s (%s) channels=[%s]",
        subscriber.name,
        subscriber.email,
        ", ".join(subscriber.channels),
    )

    subscriber_channels = subscriber.get_channel_ids(CHANNEL_IDS)
    if not subscriber_channels:
        logger.warning("No valid channels for subscriber %s", subscriber.name)
        return 0, 0

    relevant = [v for v in feed_videos if v.channel_id in subscriber_channels]
    if not relevant:
        logger.info("No recent videos from subscribed channels")
        return 0, 0

    sent_ids = get_sent_video_ids(subscriber.email)
    new_videos = [v for v in relevant if v.video_id not in sent_ids]
    if not new_videos:
        logger.info("No new videos to send")
        return 0, 0

    logger.info("Found %d new video(s)", len(new_videos))

    if subscriber.max_videos > 0 and len(new_videos) > subscriber.max_videos:
        logger.info(
            "Limiting to %d video(s) with channel diversity",
            subscriber.max_videos,
        )
        new_videos = _select_with_diversity(new_videos, subscriber.max_videos)

    success_count = 0
    error_count = 0

    for video in new_videos:
        try:
            processed = _fetch_and_summarize(video)

            if processed.status == "error":
                error_count += 1
                continue

            if processed.status == "no_transcript":
                mark_sent(
                    video_id=video.video_id,
                    subscriber_email=subscriber.email,
                    channel_id=video.channel_id,
                    channel_name=video.channel_name,
                    title=video.title,
                    video_url=video.video_url,
                    published_at=video.published_at,
                    status="skipped",
                )
                success_count += 1
                continue

            if _send_to_subscriber(processed, subscriber, owner_email):
                success_count += 1
            else:
                error_count += 1

        except LLMAuthenticationError:
            remaining = len(new_videos) - (success_count + error_count) - 1
            logger.error(
                "LLM auth failed — aborting run. %d video(s) will retry next run.",
                remaining + 1,
            )
            error_count += 1
            send_error_notification(
                error_type="LLM Authentication Failed",
                details="OAuth token has expired. The digest run was aborted and all remaining videos will retry next run.",
                owner_email=owner_email,
                video_title=video.title,
                video_url=video.video_url,
            )
            break

        except Exception as e:
            logger.error("Unexpected error processing '%s': %s", video.title, e)
            error_count += 1
            send_error_notification(
                error_type="Unexpected Error",
                details=str(e),
                owner_email=owner_email,
                video_title=video.title,
                video_url=video.video_url,
            )

    return success_count, error_count


def _fetch_and_summarize(video: Video) -> ProcessedVideo:
    """Fetch transcript and generate summary.  Results are cached per-run."""
    if video.video_id in _video_cache:
        return _video_cache[video.video_id]

    logger.info("Processing: %s (%s)", video.title, video.video_url)

    existing = get_archived_summary(video.channel_name, video.video_id)
    if existing:
        logger.info("Using cached summary from archive")
        result = ProcessedVideo(
            video=video,
            video_info=None,
            summary=existing["summary"],
            archive_path=existing["path"],
            status="success",
        )
        _video_cache[video.video_id] = result
        return result

    logger.info("Fetching transcript...")
    video_info = get_video_info(video.video_url, title=video.title)

    if not video_info:
        logger.warning("Failed to fetch video info (temporary), will retry next run")
        return ProcessedVideo(
            video=video,
            video_info=None,
            summary=None,
            archive_path=None,
            status="error",
        )

    if not video_info.transcript:
        logger.info("Skipping: no English transcript available")
        result = ProcessedVideo(
            video=video,
            video_info=video_info,
            summary=None,
            archive_path=None,
            status="no_transcript",
        )
        _video_cache[video.video_id] = result
        return result

    logger.info(
        "Transcript fetched (%d chars), generating summary...",
        len(video_info.transcript),
    )
    summary = summarize_transcript(video_info.transcript, video_info.title)

    if not summary:
        logger.error("Summarization failed for '%s'", video.title)
        return ProcessedVideo(
            video=video,
            video_info=video_info,
            summary=None,
            archive_path=None,
            status="error",
        )

    logger.info("Summary generated (%d chars), archiving...", len(summary))
    archive_path = archive_summary(
        channel_name=video.channel_name,
        video_title=video.title,
        video_url=video.video_url,
        summary=summary,
        published_at=video.published_at,
        video_id=video.video_id,
    )

    result = ProcessedVideo(
        video=video,
        video_info=video_info,
        summary=summary,
        archive_path=archive_path,
        status="success",
    )
    _video_cache[video.video_id] = result
    return result


def _send_to_subscriber(
    processed: ProcessedVideo,
    subscriber: Subscriber,
    owner_email: str,
) -> bool:
    """Send a processed video digest email to a subscriber."""
    video = processed.video
    logger.info("Sending '%s' to %s...", video.title, subscriber.name)

    thumbnail_url = ""
    if processed.video_info:
        thumbnail_url = processed.video_info.thumbnail_url
    elif video.thumbnail_url:
        thumbnail_url = video.thumbnail_url

    email_sent = send_digest_email(
        recipient_email=subscriber.email,
        channel_name=video.channel_name,
        video_title=video.title,
        video_url=video.video_url,
        thumbnail_url=thumbnail_url,
        summary=processed.summary,
        published_at=video.published_at,
    )

    mark_sent(
        video_id=video.video_id,
        subscriber_email=subscriber.email,
        channel_id=video.channel_id,
        channel_name=video.channel_name,
        title=video.title,
        video_url=video.video_url,
        published_at=video.published_at,
        status="success" if email_sent else "email_failed",
    )

    if email_sent:
        logger.info("Email sent to %s", subscriber.email)
    else:
        logger.warning("Email failed for %s", subscriber.email)
        send_error_notification(
            error_type="Email Delivery Failed",
            details=f"Failed to send digest to {subscriber.email}",
            owner_email=owner_email,
            video_title=video.title,
            video_url=video.video_url,
        )

    return email_sent


def _select_with_diversity(
    videos: list[Video],
    max_count: int,
) -> list[Video]:
    """Select up to max_count videos with round-robin channel diversity.

    Prioritises oldest videos first so they don't expire from the RSS feed.
    """
    if len(videos) <= max_count:
        return videos

    def sort_key(v: Video) -> datetime:
        return v.published_at if v.published_at is not None else datetime.max

    by_channel: dict[str, list[Video]] = {}
    for v in videos:
        by_channel.setdefault(v.channel_name, []).append(v)

    for channel in by_channel:
        by_channel[channel].sort(key=sort_key)

    selected: list[Video] = []
    channels = list(by_channel.keys())
    idx = 0

    while len(selected) < max_count:
        channel = channels[idx % len(channels)]
        if by_channel[channel]:
            selected.append(by_channel[channel].pop(0))
        idx += 1
        if all(len(v) == 0 for v in by_channel.values()):
            break

    return selected
