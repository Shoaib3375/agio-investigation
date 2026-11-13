"""OpenAI LLM provider — httpx-based, no SDK dependency.

Uses the OpenAI Chat Completions API with JSON mode (response_format:
json_object) to get structured output. Parses the response into an LLMReport.

Security notes:
  - API key is never logged.
  - The system prompt instructs the model to ignore instructions found
    in the evidence data blocks.
  - Malformed or non-JSON responses raise LLMOutputError, not crash the pipeline.
  - Timeouts and connection errors are caught and return None.
"""

from __future__ import annotations

import json

import httpx
import structlog

from app.intelligence.llm.base import LLMProvider, LLMReport

logger = structlog.get_logger()

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class LLMOutputError(Exception):
    """The LLM returned output that cannot be parsed into LLMReport."""


class OpenAIProvider(LLMProvider):
    """Calls the OpenAI Chat Completions API and parses structured JSON output."""

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 60,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "openai"

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def explain(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2048,
    ) -> LLMReport | None:
        """Call OpenAI and return a parsed LLMReport, or None on any failure."""
        if not self._api_key:
            return None

        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _OPENAI_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            logger.warning("llm.openai.timeout", model=model)
            return None
        except httpx.NetworkError as exc:
            logger.warning("llm.openai.network_error", error=str(exc))
            return None

        if response.status_code != 200:
            logger.error(
                "llm.openai.api_error",
                status=response.status_code,
                # Do NOT log response body — may contain account details
            )
            return None

        try:
            body = response.json()
            raw_content = body["choices"][0]["message"]["content"]
            parsed = json.loads(raw_content)
            return LLMReport.model_validate(parsed)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.error("llm.openai.parse_error", error=str(exc))
            return None
        except Exception as exc:  # pydantic ValidationError etc.
            logger.error("llm.openai.validation_error", error=str(exc))
            return None
