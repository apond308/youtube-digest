"""Data models for YouTube Digest."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Video:
    """A video discovered from an RSS feed."""
    video_id: str
    title: str
    video_url: str
    channel_id: str
    channel_name: str
    published_at: Optional[datetime]
    thumbnail_url: str


@dataclass
class VideoInfo:
    """Video metadata and transcript content."""
    video_id: str
    title: str
    thumbnail_url: str
    video_url: str
    transcript: Optional[str]
    language: str


@dataclass
class ProcessedVideo:
    """A video that has been fully processed (transcript fetched, summary generated)."""
    video: Video
    video_info: Optional[VideoInfo]
    summary: Optional[str]
    archive_path: Optional[str]
    status: str  # "success", "no_transcript", "error"


@dataclass
class SentVideo:
    """A record of a video sent to a subscriber."""
    video_id: str
    subscriber_email: str
    channel_id: str
    channel_name: str
    title: str
    video_url: str
    published_at: Optional[datetime]
    sent_at: datetime
    status: str  # "success", "skipped", "email_failed"


@dataclass
class Subscriber:
    """A newsletter subscriber with channel preferences."""
    name: str
    email: str
    channels: list[str]
    max_videos: int = 5

    def get_channel_ids(self, channel_name_to_id: dict[str, str]) -> dict[str, str]:
        """Get {channel_id: channel_name} for this subscriber's channels.

        Args:
            channel_name_to_id: Mapping of channel display names to YouTube channel IDs.
        """
        return {
            channel_name_to_id[name]: name
            for name in self.channels
            if name in channel_name_to_id
        }
