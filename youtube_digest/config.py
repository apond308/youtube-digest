"""Configuration settings for YouTube Digest.

All settings are loaded from environment variables (via .env) or have sensible defaults.
Channel configuration is loaded from channels.yaml (see channels.example.yaml).
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = DATA_DIR / "state.db"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
SUBSCRIBERS_PATH = PROJECT_ROOT / "subscribers.yaml"
CHANNELS_PATH = PROJECT_ROOT / "channels.yaml"
CHANNELS_EXAMPLE_PATH = PROJECT_ROOT / "channels.example.yaml"
MODEL_CONFIG_PATH = PROJECT_ROOT / "model.yaml"

DATA_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# LLM API (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMProviderConfig:
    """Connection settings for one OpenAI-compatible LLM provider."""

    name: str
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str | None = None


def _load_model_config() -> tuple[LLMProviderConfig, LLMProviderConfig | None]:
    """Load provider definitions from YAML and credentials from the environment."""
    cfg: dict = {}
    if MODEL_CONFIG_PATH.exists():
        try:
            with open(MODEL_CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Error loading model.yaml: %s", e)

    primary_cfg = cfg.get("primary") or {}
    backup_cfg = cfg.get("backup") or {}

    primary = LLMProviderConfig(
        name="primary",
        api_key=os.getenv("PRIMARY_LLM_API_KEY", ""),
        base_url=primary_cfg.get("base_url", ""),
        model=primary_cfg.get("model", ""),
        reasoning_effort=primary_cfg.get("reasoning_effort") or None,
    )

    backup_model = backup_cfg.get("model", "")
    backup_url = backup_cfg.get("base_url", "")
    backup_key = os.getenv("BACKUP_LLM_API_KEY", "")
    backup = None
    if backup_model and backup_url and backup_key:
        backup = LLMProviderConfig(
            name="backup",
            api_key=backup_key,
            base_url=backup_url,
            model=backup_model,
            reasoning_effort=backup_cfg.get("reasoning_effort") or None,
        )
    elif backup_model or backup_url or backup_key:
        logger.error(
            "Backup LLM is partially configured; model, base URL, and API key are all required"
        )

    return primary, backup


PRIMARY_LLM, BACKUP_LLM = _load_model_config()
MAX_TRANSCRIPT_TOKENS = 300_000

# ---------------------------------------------------------------------------
# Email (Gmail SMTP)
# ---------------------------------------------------------------------------
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# ---------------------------------------------------------------------------
# Processing limits
# ---------------------------------------------------------------------------
MAX_VIDEOS_PER_DAY = int(os.getenv("MAX_VIDEOS_PER_DAY", "0"))

# ---------------------------------------------------------------------------
# Channel registry  (loaded from channels.yaml)
# ---------------------------------------------------------------------------


def _load_channels() -> dict[str, str]:
    """Load channel ID -> display name mapping from channels.yaml.

    Falls back to channels.example.yaml if channels.yaml doesn't exist.
    """
    path = CHANNELS_PATH
    if not path.exists():
        path = CHANNELS_EXAMPLE_PATH
        if path.exists():
            logger.warning(
                "channels.yaml not found — using channels.example.yaml. "
                "Run ./install.sh or copy channels.example.yaml to channels.yaml "
                "and add your own channels."
            )
        else:
            logger.error(
                "No channel configuration found. "
                "Create channels.yaml (see channels.example.yaml)."
            )
            return {}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        entries = data.get("channels", [])
        channels: dict[str, str] = {}
        for entry in entries:
            cid = entry.get("id", "").strip()
            name = entry.get("name", "").strip()
            if cid and name:
                channels[cid] = name
            else:
                logger.warning("Skipping malformed channel entry: %s", entry)
        if not channels:
            logger.warning("No valid channels found in %s", path)
        return channels
    except Exception as e:
        logger.error("Error loading channels from %s: %s", path, e)
        return {}


CHANNELS: dict[str, str] = _load_channels()
CHANNEL_IDS: dict[str, str] = {name: cid for cid, name in CHANNELS.items()}

RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# ---------------------------------------------------------------------------
# Summarization prompt
# ---------------------------------------------------------------------------
SUMMARY_PROMPT_TEMPLATE = """You are writing a digest entry for a reader who subscribes to this channel but won't watch the video. They should come away feeling they watched it.

Video: "{title}" — {channel} (published {published})
Use the spelling of names, products and terms from the title where the auto-captions disagree; otherwise correct obvious caption errors from context.

**Required format — follow this exactly:**

## Summary
Open directly with the substance, never with "This video…" or "The video explores…". Follow the video's own order, in ### sections named for their content (not "Part 1").
- Interviews/podcasts: name the speaker when attributing claims.
- Keep concrete detail: numbers, names, dates, quotes worth keeping, steps in a process.
- Attribute opinions and predictions to the speaker ("Paine argues…"); only state as fact what the video presents as fact.
- Use bullets for lists, steps and facts; prose for narrative and reasoning.
- Skip sponsor reads, channel plugs and intro/outro filler.
- Length scales with content: roughly 1 word of summary per 8-10 words of transcript, minimum 250, maximum 3000.

## Takeaways
3-6 bullets: what to remember or do differently, plus anything the video left unresolved or that deserves a skeptical eye.

If the transcript is incomplete or cuts off mid-sentence, summarize everything available and add a one-line note at the end. NEVER ask for the rest of the transcript.

---

**TRANSCRIPT:**

{transcript}"""
