"""FastAPI server mode for YouTube Digest."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import TEMPLATES_DIR
from .pipeline import main as run_daily_digest
from .pipeline import summarize_single_video
from .storage.subscribers import load_subscribers

logger = logging.getLogger(__name__)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
YOUTUBE_URL_PATTERN = re.compile(
    r"(youtube\.com/watch\?v=[a-zA-Z0-9_-]{11})|(youtu\.be/[a-zA-Z0-9_-]{11})",
    flags=re.IGNORECASE,
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class SummarizeRequest(BaseModel):
    """Payload for on-demand summarization."""

    url: str = Field(min_length=11)
    subscriber_email: str = Field(min_length=3)


def _run_daily_digest_job() -> None:
    """Scheduler callback for the recurring daily digest."""
    logger.info("Starting scheduled daily digest run")
    exit_code = run_daily_digest()
    if exit_code == 0:
        logger.info("Scheduled daily digest completed successfully")
    else:
        logger.error("Scheduled daily digest completed with errors (exit=%d)", exit_code)


def _run_on_demand_job(video_url: str, subscriber_email: str) -> None:
    """Background callback for a single user-submitted YouTube URL."""
    subscribers = load_subscribers()
    subscriber = next((sub for sub in subscribers if sub.email == subscriber_email), None)
    if not subscriber:
        logger.error("Subscriber not found during on-demand run: %s", subscriber_email)
        return

    success = summarize_single_video(video_url=video_url, subscriber=subscriber)
    if success:
        logger.info("On-demand summary sent for %s to %s", video_url, subscriber_email)
    else:
        logger.error("On-demand summary failed for %s to %s", video_url, subscriber_email)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage scheduler lifecycle with app startup/shutdown."""
    scheduler = AsyncIOScheduler(timezone=PACIFIC_TZ)
    scheduler.add_job(
        _run_daily_digest_job,
        trigger=CronTrigger(hour=5, minute=0, timezone=PACIFIC_TZ),
        id="daily-digest",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started: daily digest at 05:00 America/Los_Angeles")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


app = FastAPI(title="YouTube Digest", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the single-page form for on-demand video submission."""
    subscribers = load_subscribers()
    return templates.TemplateResponse(
        "form.html",
        {"request": request, "subscribers": subscribers},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Health endpoint for uptime checks."""
    return {"status": "ok"}


@app.post("/api/summarize")
async def summarize_video(
    payload: SummarizeRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Queue an on-demand summary job and return immediately."""
    if not YOUTUBE_URL_PATTERN.search(payload.url):
        raise HTTPException(status_code=400, detail="Please provide a valid YouTube URL.")

    subscribers = load_subscribers()
    subscriber = next((sub for sub in subscribers if sub.email == payload.subscriber_email), None)
    if not subscriber:
        raise HTTPException(status_code=400, detail="Selected subscriber was not found.")

    background_tasks.add_task(_run_on_demand_job, payload.url, subscriber.email)
    return {
        "status": "accepted",
        "message": "Video queued. You should receive an email shortly.",
    }
