"""Email delivery via Gmail SMTP."""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import markdown
from jinja2 import Environment, FileSystemLoader

from ..config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, TEMPLATES_DIR

logger = logging.getLogger(__name__)


def send_digest_email(
    recipient_email: str,
    channel_name: str,
    video_title: str,
    video_url: str,
    thumbnail_url: str,
    summary: str,
    published_at: Optional[datetime] = None,
) -> bool:
    """Send a video digest email to a subscriber.

    Returns *True* on success, *False* on failure.
    """
    if not all([GMAIL_ADDRESS, GMAIL_APP_PASSWORD, recipient_email]):
        logger.error("Email credentials not configured")
        return False

    try:
        summary_html = markdown.markdown(summary, extensions=["extra"])

        env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
        template = env.get_template("email.html")

        html_content = template.render(
            channel_name=channel_name,
            video_title=video_title,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            summary=summary_html,
            published_at=published_at.strftime("%B %d, %Y") if published_at else "Unknown",
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[YouTube Digest] {channel_name}: {video_title}"
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = recipient_email

        plain_text = (
            f"{channel_name}: {video_title}\n"
            f"{video_url}\n\n"
            f"{summary}"
        )

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        return True

    except Exception as e:
        logger.error("Failed to send email to %s: %s", recipient_email, e)
        return False


def send_error_notification(
    error_type: str,
    details: str,
    owner_email: str,
    video_title: Optional[str] = None,
    video_url: Optional[str] = None,
) -> bool:
    """Send an error notification email to the system owner."""
    if not all([GMAIL_ADDRESS, GMAIL_APP_PASSWORD, owner_email]):
        logger.error("Email credentials not configured")
        return False

    try:
        subject = f"[YouTube Digest] Error: {error_type}"

        body = f"YouTube Digest encountered an error:\n\nError Type: {error_type}\n"
        if video_title:
            body += f"Video: {video_title}\n"
        if video_url:
            body += f"URL: {video_url}\n"
        body += f"\nDetails:\n{details}\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = owner_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        logger.info("Error notification sent: %s", error_type)
        return True

    except Exception as e:
        logger.error("Failed to send error notification: %s", e)
        return False
