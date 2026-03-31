"""SQLite persistence for tracking sent videos per subscriber."""

import logging
import sqlite3
from datetime import datetime
from typing import Optional

from ..config import DB_PATH
from ..models import SentVideo

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating tables on first use."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def mark_sent(
    video_id: str,
    subscriber_email: str,
    channel_id: str,
    channel_name: str,
    title: str,
    video_url: str,
    published_at: Optional[datetime] = None,
    status: str = "success",
) -> None:
    """Record that a video has been sent (or skipped) for a subscriber."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sent_videos
            (video_id, subscriber_email, channel_id, channel_name, title, video_url, published_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, subscriber_email, channel_id, channel_name, title, video_url,
             published_at, status),
        )
        conn.commit()


def is_sent(video_id: str, subscriber_email: str) -> bool:
    """Check whether a video has already been sent to a specific subscriber."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM sent_videos WHERE video_id = ? AND subscriber_email = ?",
            (video_id, subscriber_email),
        )
        return cursor.fetchone() is not None


def get_sent_video_ids(subscriber_email: str) -> set[str]:
    """Return the set of video IDs already sent to *subscriber_email*."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT video_id FROM sent_videos WHERE subscriber_email = ?",
            (subscriber_email,),
        )
        return {row["video_id"] for row in cursor.fetchall()}


def get_recent_sent(
    subscriber_email: Optional[str] = None,
    limit: int = 10,
) -> list[SentVideo]:
    """Return recently sent videos, optionally filtered by subscriber."""
    with get_connection() as conn:
        if subscriber_email:
            cursor = conn.execute(
                "SELECT * FROM sent_videos WHERE subscriber_email = ? ORDER BY sent_at DESC LIMIT ?",
                (subscriber_email, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM sent_videos ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            )

        return [
            SentVideo(
                video_id=row["video_id"],
                subscriber_email=row["subscriber_email"],
                channel_id=row["channel_id"],
                channel_name=row["channel_name"],
                title=row["title"],
                video_url=row["video_url"],
                published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
                sent_at=datetime.fromisoformat(row["sent_at"]),
                status=row["status"],
            )
            for row in cursor.fetchall()
        ]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sent_videos (
            video_id TEXT NOT NULL,
            subscriber_email TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            published_at TIMESTAMP,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success',
            PRIMARY KEY (video_id, subscriber_email)
        );

        CREATE INDEX IF NOT EXISTS idx_sent_subscriber ON sent_videos(subscriber_email);
        CREATE INDEX IF NOT EXISTS idx_sent_channel ON sent_videos(channel_id);
        CREATE INDEX IF NOT EXISTS idx_sent_at ON sent_videos(sent_at);
    """)
    conn.commit()
