"""Local markdown archiving of video summaries."""

import logging
import re
from datetime import datetime
from typing import Optional

from ..config import ARCHIVE_DIR

logger = logging.getLogger(__name__)


def archive_summary(
    channel_name: str,
    video_title: str,
    video_url: str,
    summary: str,
    published_at: Optional[datetime] = None,
    video_id: Optional[str] = None,
) -> str:
    """Save a summary to the local archive.

    Returns the path to the archived file.
    """
    now = datetime.now()
    month_dir = ARCHIVE_DIR / now.strftime("%Y-%m")
    month_dir.mkdir(exist_ok=True)

    safe_channel = _slugify(channel_name)
    safe_title = _slugify(video_title)[:50]

    if video_id:
        filename = f"{safe_channel}_{video_id}_{safe_title}.md"
    else:
        filename = f"{safe_channel}_{safe_title}.md"

    filepath = month_dir / filename

    content = (
        f"# {video_title}\n\n"
        f"**Channel:** {channel_name}\n"
        f"**URL:** {video_url}\n"
        f"**Video ID:** {video_id or 'Unknown'}\n"
        f"**Published:** {published_at.strftime('%Y-%m-%d') if published_at else 'Unknown'}\n"
        f"**Archived:** {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"---\n\n"
        f"{summary}\n"
    )

    filepath.write_text(content, encoding="utf-8")
    logger.debug("Archived to %s", filepath)
    return str(filepath)


def get_archived_summary(channel_name: str, video_id: str) -> Optional[dict]:
    """Look up an existing archived summary by *video_id*.

    Returns ``{"summary": ..., "path": ...}`` if found, otherwise *None*.
    """
    if not video_id:
        return None

    safe_channel = _slugify(channel_name)

    for month_dir in ARCHIVE_DIR.glob("*"):
        if not month_dir.is_dir():
            continue
        for filepath in month_dir.glob(f"{safe_channel}_{video_id}_*.md"):
            try:
                content = filepath.read_text(encoding="utf-8")
                parts = content.split("\n---\n", 1)
                if len(parts) == 2:
                    return {"summary": parts[1].strip(), "path": str(filepath)}
            except Exception:
                continue

    return None


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")
