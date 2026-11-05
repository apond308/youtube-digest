"""Main orchestration pipeline for YouTube Digest.

Coordinates the full workflow: fetch feeds -> process videos -> deliver digests.
"""

import logging
import time
from datetime import datetime
from typing import Optional

import requests

from .config import CHANNEL_IDS, GMAIL_ADDRESS
from .models import ProcessedVideo, Subscriber, Video
from .services.feed import get_recent_videos
from .services.transcript import extract_video_id, get_video_info
from .services.summarizer import (
    LLMAuthenticationError,
    LLMError,
    LLMFallbackError,
    summarize_transcript,
)
from .delivery.email import send_digest_email, send_error_notification
from .storage.archive import archive_summary, get_archived_summary
from .storage.database import get_sent_video_ids, mark_sent, try_claim_video
from .storage.subscribers import load_subscribers

logger = logging.getLogger(__name__)

SHORTS_CHECK_TIMEOUT = 5

_video_cache: dict[str, ProcessedVideo] = {}


def main() -> int:
    """Run the full digest pipeline.  Returns 0 on success, 1 on errors."""
    _video_cache.clear()
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

    max_attempts = 3
    processed = None
    for attempt in range(1, max_attempts + 1):
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

        except LLMError as e:
            if isinstance(e, LLMFallbackError):
                _send_primary_llm_failure_notification(
                    owner_email=owner,
                    video=video,
                    primary_error=str(e.primary_error),
                    fallback_model=(
                        e.backup_error.provider.model
                        if e.backup_error.provider
                        else "configured backup"
                    ),
                    backup_succeeded=False,
                )
            send_error_notification(
                error_type="LLM Request Failed",
                details=(
                    f"{e}\n\nRetrying will not help until the configuration is fixed, "
                    f"so the remaining attempts were skipped."
                ),
                owner_email=owner,
                video_title=video.title,
                video_url=video.video_url,
            )
            return False

        if processed.status != "error":
            _notify_primary_llm_failure(processed, owner)

        if processed.status != "error":
            break

        if attempt < max_attempts:
            logger.warning(
                "Attempt %d/%d failed for '%s', retrying in 10s...",
                attempt, max_attempts, video.title,
            )
            time.sleep(10)
            # Clear cache so _fetch_and_summarize retries from scratch
            _video_cache.pop(video.video_id, None)

    if processed.status == "error":
        _notify_primary_llm_failure(processed, owner)
        details = f"Failed to process {video.video_url} after {max_attempts} attempts"
        if processed.error:
            details += f"\n\nLast error: {processed.error}"
        send_error_notification(
            error_type="On-Demand Summarization Failed",
            details=details,
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
    video_id = extract_video_id(video_url)
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

    new_videos = _filter_shorts(new_videos)
    if not new_videos:
        logger.info("All new videos were Shorts — nothing to send")
        return 0, 0

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
            _notify_primary_llm_failure(processed, owner_email)

            if processed.status == "error":
                error_count += 1
                continue

            if processed.status == "no_transcript":
                if not try_claim_video(video.video_id, subscriber.email):
                    logger.info("Skipping duplicate (already claimed): %s", video.video_id)
                    success_count += 1
                    continue
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

        except LLMError as e:
            remaining = len(new_videos) - (success_count + error_count) - 1
            logger.error("LLM request failed permanently — aborting run: %s", e)
            error_count += 1
            if isinstance(e, LLMFallbackError):
                _send_primary_llm_failure_notification(
                    owner_email=owner_email,
                    video=video,
                    primary_error=str(e.primary_error),
                    fallback_model=(
                        e.backup_error.provider.model
                        if e.backup_error.provider
                        else "configured backup"
                    ),
                    backup_succeeded=False,
                )
            send_error_notification(
                error_type="LLM Request Failed",
                details=(
                    f"{e}\n\nThe digest run was aborted; "
                    f"{remaining + 1} video(s) will retry next run."
                ),
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
            error="Could not fetch video info or transcript from YouTube",
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
    try:
        summary_result = summarize_transcript(video_info.transcript, video_info.title)
    except LLMError as e:
        # Permanent failures (bad model slug, expired auth) propagate so callers
        # can abort instead of retrying a request that cannot succeed.
        if e.permanent:
            raise
        logger.error("Summarization failed for '%s': %s", video.title, e)
        return ProcessedVideo(
            video=video,
            video_info=video_info,
            summary=None,
            archive_path=None,
            status="error",
            error=str(e),
            primary_llm_error=(
                str(e.primary_error) if isinstance(e, LLMFallbackError) else None
            ),
        )

    summary = summary_result.content
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
        fallback_model=(
            summary_result.provider.model if summary_result.primary_error else None
        ),
        primary_llm_error=(
            str(summary_result.primary_error) if summary_result.primary_error else None
        ),
    )
    _video_cache[video.video_id] = result
    return result


def _send_primary_llm_failure_notification(
    owner_email: str,
    video: Video,
    primary_error: str,
    fallback_model: str,
    backup_succeeded: bool,
) -> None:
    """Notify the owner separately whenever the primary provider fails."""
    fallback_status = (
        "The backup model succeeded, so summary delivery continued normally."
        if backup_succeeded
        else "The backup model was attempted but also failed."
    )
    send_error_notification(
        error_type="Primary LLM Failed — Backup Activated",
        details=(
            f"Primary provider error:\n{primary_error}\n\n"
            f"Backup model: {fallback_model}\n{fallback_status}"
        ),
        owner_email=owner_email,
        video_title=video.title,
        video_url=video.video_url,
    )


def _notify_primary_llm_failure(
    processed: ProcessedVideo,
    owner_email: str,
) -> None:
    """Send at most one fallback notification for a processed video."""
    if not processed.primary_llm_error or processed.fallback_notification_sent:
        return

    _send_primary_llm_failure_notification(
        owner_email=owner_email,
        video=processed.video,
        primary_error=processed.primary_llm_error,
        fallback_model=processed.fallback_model or "configured backup",
        backup_succeeded=processed.status == "success",
    )
    processed.fallback_notification_sent = True


def _send_to_subscriber(
    processed: ProcessedVideo,
    subscriber: Subscriber,
    owner_email: str,
) -> bool:
    """Send a processed video digest email to a subscriber."""
    video = processed.video

    if not try_claim_video(video.video_id, subscriber.email):
        logger.warning(
            "Duplicate send prevented for '%s' → %s", video.title, subscriber.email
        )
        return True

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


def _is_youtube_short(video_id: str) -> bool:
    """Return True if the video is a YouTube Short (<=60s vertical video)."""
    try:
        resp = requests.head(
            f"https://www.youtube.com/shorts/{video_id}",
            allow_redirects=False,
            timeout=SHORTS_CHECK_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; YouTubeDigest/1.0)"},
        )
        return resp.status_code == 200
    except Exception:
        return False


def _filter_shorts(videos: list[Video]) -> list[Video]:
    """Remove YouTube Shorts, keeping only full-length videos."""
    kept: list[Video] = []
    for video in videos:
        if _is_youtube_short(video.video_id):
            logger.info("Skipping Short: %s (%s)", video.title, video.video_url)
        else:
            kept.append(video)
    return kept


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
