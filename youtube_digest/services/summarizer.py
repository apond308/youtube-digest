"""LLM-based transcript summarization via an OpenAI-compatible API."""

import json
import logging
import re
from dataclasses import dataclass

from openai import AuthenticationError as OpenAIAuthError
from openai import BadRequestError, NotFoundError, OpenAI, PermissionDeniedError

from ..config import (
    BACKUP_LLM,
    MAX_TRANSCRIPT_TOKENS,
    PRIMARY_LLM,
    SUMMARY_PROMPT_TEMPLATE,
    LLMProviderConfig,
)

logger = logging.getLogger(__name__)

_MIN_SUMMARY_LENGTH = 200

_ERROR_PATTERNS = [
    re.compile(r"^API Error:", re.IGNORECASE),
    re.compile(r'"type"\s*:\s*"error"'),
    re.compile(r'"error"\s*:\s*\{'),
    re.compile(r"not_found_error|invalid_request_error|authentication_error"),
    re.compile(r"^Error code:\s*\d{3}\b", re.IGNORECASE),
    re.compile(r"\[Request interrupted", re.IGNORECASE),
]


class LLMError(Exception):
    """Raised when the LLM API call fails.

    ``permanent`` marks failures that retrying cannot fix — a bad model slug,
    expired credentials, or a request the provider rejects outright.  Callers
    use it to abort instead of burning attempts on a doomed request.
    """

    def __init__(
        self,
        message: str,
        permanent: bool = False,
        provider: LLMProviderConfig | None = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.provider = provider


class LLMAuthenticationError(LLMError):
    """Raised when the LLM API returns a 401 / auth error."""

    def __init__(self, message: str, provider: LLMProviderConfig) -> None:
        super().__init__(message, permanent=True, provider=provider)


class LLMFallbackError(LLMError):
    """Raised when both the primary and backup providers fail."""

    def __init__(self, primary_error: LLMError, backup_error: LLMError) -> None:
        message = (
            f"Primary model failed: {primary_error}\n\n"
            f"Backup model also failed: {backup_error}"
        )
        super().__init__(
            message,
            permanent=primary_error.permanent and backup_error.permanent,
        )
        self.primary_error = primary_error
        self.backup_error = backup_error


@dataclass(frozen=True)
class SummarizationResult:
    """Generated summary plus details about any provider fallback."""

    content: str
    provider: LLMProviderConfig
    primary_error: LLMError | None = None


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


def _summarize_with_provider(
    transcript: str,
    provider: LLMProviderConfig,
) -> str:
    """Generate a summary with one provider."""
    try:
        client = OpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            default_headers={"User-Agent": "YouTubeDigest/1.0"},
        )
        user_prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)

        logger.debug(
            "API request: provider=%s url=%s model=%s prompt_len=%d",
            provider.name,
            provider.base_url,
            provider.model,
            len(user_prompt),
        )

        create_kwargs: dict = {
            "model": provider.model,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if provider.reasoning_effort:
            create_kwargs["reasoning_effort"] = provider.reasoning_effort

        response = client.chat.completions.create(**create_kwargs)

        if not response.choices:
            raise LLMError("API returned no choices", provider=provider)

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise LLMError("API returned empty content", provider=provider)

        if _looks_like_error(content):
            raise LLMError(
                f"API returned an error disguised as content: {content[:300]}",
                provider=provider,
            )

        return content

    except LLMError:
        raise
    except OpenAIAuthError as e:
        raise LLMAuthenticationError(
            f"{provider.name.title()} model '{provider.model}' authentication failed: {e}",
            provider,
        ) from e
    except (NotFoundError, BadRequestError, PermissionDeniedError) as e:
        message = (
            f"{provider.name.title()} model '{provider.model}' was rejected by "
            f"{provider.base_url}. Provider said: {e}"
        )
        raise LLMError(message, permanent=True, provider=provider) from e
    except Exception as e:
        raise LLMError(
            f"{provider.name.title()} model '{provider.model}' failed: {e}",
            provider=provider,
        ) from e


def summarize_transcript(transcript: str, title: str = "") -> SummarizationResult:
    """Generate a detailed article-style summary of a video transcript.

    Uses the backup provider once when the primary fails. Returns provider
    metadata so the pipeline can send a separate fallback notification.
    """
    estimated_tokens = len(transcript) // 4
    if estimated_tokens > MAX_TRANSCRIPT_TOKENS:
        logger.warning(
            "Transcript very long (%d est. tokens) — truncating", estimated_tokens,
        )
        max_chars = int(MAX_TRANSCRIPT_TOKENS * 4 * 0.8)
        transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

    primary_failure: LLMError | None = None
    try:
        content = _summarize_with_provider(transcript, PRIMARY_LLM)
        return SummarizationResult(content=content, provider=PRIMARY_LLM)
    except LLMError as primary_error:
        logger.error("Primary LLM failed: %s", primary_error)
        primary_failure = primary_error
        if not BACKUP_LLM:
            raise

    backup = BACKUP_LLM
    assert backup is not None
    logger.warning(
        "Falling back to model '%s' at %s",
        backup.model,
        backup.base_url,
    )
    try:
        content = _summarize_with_provider(transcript, backup)
        return SummarizationResult(
            content=content,
            provider=backup,
            primary_error=primary_failure,
        )
    except LLMError as backup_error:
        logger.error("Backup LLM failed: %s", backup_error)
        assert primary_failure is not None
        raise LLMFallbackError(primary_failure, backup_error) from backup_error
