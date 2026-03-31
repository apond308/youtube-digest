"""YouTube transcript fetching via youtube-transcript-api."""

import logging
import re
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    TranscriptsDisabled,
)

from ..models import VideoInfo

logger = logging.getLogger(__name__)

_api = YouTubeTranscriptApi()


def get_video_info(video_url: str, title: str = "") -> Optional[VideoInfo]:
    """Fetch transcript for a YouTube video.

    Tries manual English captions first, then auto-generated, then translated.

    Returns:
        :class:`VideoInfo` with transcript populated on success,
        :class:`VideoInfo` with ``transcript=None`` if no English transcript exists,
        or *None* on temporary/network errors (caller should retry).
    """
    video_id = _extract_video_id(video_url)
    if not video_id:
        logger.error("Could not extract video ID from: %s", video_url)
        return None

    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

    try:
        transcript_data = _fetch_transcript(video_id)

        if transcript_data is None:
            return VideoInfo(
                video_id=video_id,
                title=title,
                thumbnail_url=thumbnail_url,
                video_url=video_url,
                transcript=None,
                language="",
            )

        transcript_text, language = transcript_data
        transcript_text = _clean_transcript(transcript_text)

        logger.debug("Transcript preview: %.500s", transcript_text)

        return VideoInfo(
            video_id=video_id,
            title=title,
            thumbnail_url=thumbnail_url,
            video_url=video_url,
            transcript=transcript_text,
            language=language,
        )

    except Exception as e:
        logger.error("Error fetching transcript: %s", e)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_transcript(video_id: str) -> Optional[tuple[str, str]]:
    """Return ``(transcript_text, language)`` or *None* if unavailable."""
    try:
        transcript_list = _api.list(video_id)

        # Strategy 1: manual English captions
        try:
            transcript = transcript_list.find_manually_created_transcript(
                ["en", "en-US", "en-GB"],
            )
            segments = transcript.fetch()
            return " ".join(seg.text for seg in segments), transcript.language_code
        except NoTranscriptFound:
            pass

        # Strategy 2: auto-generated English
        try:
            transcript = transcript_list.find_generated_transcript(
                ["en", "en-US", "en-GB"],
            )
            segments = transcript.fetch()
            return " ".join(seg.text for seg in segments), transcript.language_code
        except NoTranscriptFound:
            pass

        # Strategy 3: any language translated to English
        try:
            for transcript in transcript_list:
                if transcript.is_translatable:
                    translated = transcript.translate("en")
                    segments = translated.fetch()
                    return (
                        " ".join(seg.text for seg in segments),
                        f"{transcript.language_code}->en",
                    )
        except Exception:
            pass

        logger.info("No English transcript available for %s", video_id)
        return None

    except TranscriptsDisabled:
        logger.info("Transcripts are disabled for %s", video_id)
        return None
    except CouldNotRetrieveTranscript:
        logger.info("Could not retrieve transcript for %s", video_id)
        return None


def _clean_transcript(text: str) -> str:
    """Remove HTML artifacts and annotation noise from transcript text."""
    text = re.sub(r"<[^>]+>", "", text)

    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")

    text = re.sub(
        r"\[(?:Music|Applause|Laughter|Cheering|Silence)\]",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_video_id(url: str) -> Optional[str]:
    """Extract an 11-character video ID from various YouTube URL formats."""
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
