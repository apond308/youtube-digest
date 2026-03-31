"""LLM-based transcript summarization via an OpenAI-compatible API."""

import logging
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


class LLMAuthenticationError(Exception):
    """Raised when the LLM API returns a 401 / auth error."""


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

        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content

        logger.error("Empty response from API")
        return None

    except OpenAIAuthError as e:
        logger.error("LLM authentication failed (token expired?): %s", e)
        raise LLMAuthenticationError(str(e)) from e

    except Exception as e:
        logger.error("Summarization error: %s", e)
        return None
