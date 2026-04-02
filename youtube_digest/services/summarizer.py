"""LLM-based transcript summarization via an OpenAI-compatible API."""

import json
import logging
import re
from typing import Optional

from openai import AuthenticationError as OpenAIAuthError
from openai import OpenAI

from ..config import (
    MAX_TRANSCRIPT_TOKENS,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    SUMMARY_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

_MIN_SUMMARY_LENGTH = 200

_ERROR_PATTERNS = [
    re.compile(r"^API Error:", re.IGNORECASE),
    re.compile(r'"type"\s*:\s*"error"'),
    re.compile(r'"error"\s*:\s*\{'),
    re.compile(r"not_found_error|invalid_request_error|authentication_error"),
    re.compile(r"^Error code:\s*\d{3}\b", re.IGNORECASE),
]


class LLMAuthenticationError(Exception):
    """Raised when the LLM API returns a 401 / auth error."""


def _looks_like_error(content: str) -> bool:
    """Return True if content appears to be an API error rather than a real summary."""
    stripped = content.strip()
    if any(p.search(stripped) for p in _ERROR_PATTERNS):
        return True
    if len(stripped) < _MIN_SUMMARY_LENGTH:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and ("error" in parsed or "type" in parsed):
                return True
        except (json.JSONDecodeError, ValueError):
            pass
    return False


def summarize_transcript(transcript: str, title: str = "") -> Optional[str]:
    """Generate a detailed article-style summary of a video transcript.

    Returns the markdown-formatted summary, or *None* on non-auth errors.
    Raises :class:`LLMAuthenticationError` on 401 so callers can fail fast.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

        estimated_tokens = len(transcript) // 4
        if estimated_tokens > MAX_TRANSCRIPT_TOKENS:
            logger.warning(
                "Transcript very long (%d est. tokens) — truncating", estimated_tokens,
            )
            max_chars = int(MAX_TRANSCRIPT_TOKENS * 4 * 0.8)
            transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

        user_prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)

        logger.debug("API request: url=%s model=%s prompt_len=%d",
                      OPENAI_BASE_URL, MODEL_NAME, len(user_prompt))

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": user_prompt}],
        )

        if not response.choices or len(response.choices) == 0:
            logger.error("Empty response from API")
            return None

        content = response.choices[0].message.content
        if not content or not content.strip():
            logger.error("API returned empty content")
            return None

        if _looks_like_error(content):
            logger.error(
                "API returned an error disguised as content: %s",
                content[:300],
            )
            return None

        return content

    except OpenAIAuthError as e:
        logger.error("LLM authentication failed (token expired?): %s", e)
        raise LLMAuthenticationError(str(e)) from e

    except Exception as e:
        logger.error("Summarization error: %s", e)
        return None
