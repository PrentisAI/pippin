"""Anthropic Claude LLM provider."""

from __future__ import annotations

import json
import logging
import os
from typing import Type, TypeVar

from pydantic import BaseModel

from aurora.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMResponseParseError,
    LLMTimeoutError,
    LLMError,
    Message,
    MessageRole,
    TokenUsage,
    UsageTracker,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ClaudeProvider(LLMProvider):
    """LLM provider for Anthropic's Claude models.

    Parameters
    ----------
    api_key:
        Anthropic API key.  Falls back to ``ANTHROPIC_API_KEY`` env var.
    default_model:
        Model identifier used when no explicit model is passed.
    max_retries:
        Maximum retries on transient errors.
    usage_tracker:
        Shared usage tracker instance.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-opus-4-8",
        max_retries: int = 2,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(usage_tracker=usage_tracker)
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._default_model = default_model
        self._max_retries = max_retries
        self._client: object | None = None  # lazy init

    def _get_client(self):  # -> anthropic.AsyncAnthropic
        """Lazily initialise the async Anthropic client."""
        if self._client is None:
            if not self._api_key:
                raise LLMAuthenticationError(
                    "Anthropic API key not set. Provide api_key or set ANTHROPIC_API_KEY."
                )
            try:
                import anthropic
            except ImportError as exc:
                raise LLMError(
                    "The 'anthropic' package is required for ClaudeProvider. "
                    "Install it with: pip install anthropic"
                ) from exc
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=self._max_retries,
            )
        return self._client

    # -- Message conversion ---------------------------------------------------

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
        """Convert Message list to Anthropic API format.

        System messages are excluded here — they go in the ``system``
        parameter of the API call.
        """
        return [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role != MessageRole.SYSTEM
        ]

    @staticmethod
    def _extract_system_prompt(messages: list[Message], explicit: str | None) -> str | None:
        """Return the system prompt, preferring the explicit one."""
        if explicit:
            return explicit
        for m in messages:
            if m.role == MessageRole.SYSTEM:
                return m.content
        return None

    # -- Core interface -------------------------------------------------------

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        model = model or self._default_model
        system = self._extract_system_prompt(messages, system_prompt)
        api_messages = self._to_anthropic_messages(messages)

        # current SDKs (anthropic>=1.x) no longer accept `temperature`; it is kept in the
        # signature for interface compatibility with the other providers
        kwargs: dict = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            self._handle_api_error(exc)

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        self._record_usage(model, usage)

        return LLMResponse(
            content=content,
            model_id=response.model,
            usage=usage,
            finish_reason=response.stop_reason or "stop",
            raw_response=response,
        )

    async def generate_structured(
        self,
        messages: list[Message],
        schema: Type[T],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> T:
        """Generate structured output using prompt injection + parsing.

        Attempts a direct JSON-mode call first; falls back to prompt-based
        structured generation with one retry.
        """
        model = model or self._default_model

        # Attempt 1: prompt injection asking for JSON
        try:
            return await self._generate_structured_with_fallback(
                messages,
                schema,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
        except LLMResponseParseError:
            logger.warning(
                "Structured generation parse failed for %s on attempt 1, retrying",
                schema.__name__,
            )

        # Attempt 2: even more explicit prompt
        schema_desc = self._schema_to_json_description(schema)
        retry_messages = list(messages) + [
            Message.user(
                f"Your previous response was not valid JSON. "
                f"Please respond with ONLY a raw JSON object (no markdown, no explanation) "
                f"matching this exact schema:\n{schema_desc}"
            ),
        ]
        try:
            response = await self.generate(
                retry_messages,
                model=model,
                temperature=0.1,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            return self._parse_structured_response(response.content, schema)
        except LLMResponseParseError:
            raise
        except Exception as exc:
            raise LLMResponseParseError(
                f"Structured generation failed after retry: {exc}",
                raw_content="",
            ) from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # -- Error handling -------------------------------------------------------

    @staticmethod
    def _handle_api_error(exc: Exception) -> None:
        """Translate provider-specific exceptions to AURORA exceptions."""
        exc_name = type(exc).__name__
        msg = str(exc)

        if "AuthenticationError" in exc_name or "permission" in msg.lower():
            raise LLMAuthenticationError(f"Anthropic authentication failed: {msg}") from exc
        if "RateLimitError" in exc_name or "rate_limit" in msg.lower():
            raise LLMRateLimitError(f"Anthropic rate limit: {msg}") from exc
        if "Timeout" in exc_name or "timeout" in msg.lower():
            raise LLMTimeoutError(f"Anthropic API timeout: {msg}") from exc
        raise LLMError(f"Anthropic API error: {msg}") from exc
