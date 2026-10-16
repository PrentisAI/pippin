"""OpenAI GPT LLM provider."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from aurora.llm.base import (
    LLMAuthenticationError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseParseError,
    LLMTimeoutError,
    Message,
    MessageRole,
    TokenUsage,
    UsageTracker,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OpenAIProvider(LLMProvider):
    """LLM provider for OpenAI GPT models (and compatible APIs).

    Parameters
    ----------
    api_key:
        OpenAI API key.  Falls back to ``OPENAI_API_KEY`` env var.
    default_model:
        Model identifier used when no explicit model is passed.
    base_url:
        Optional base URL override for OpenAI-compatible APIs.
    max_retries:
        Maximum retries on transient errors.
    usage_tracker:
        Shared usage tracker instance.
    """

    provider_name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o-mini",
        base_url: str | None = None,
        max_retries: int = 2,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(usage_tracker=usage_tracker)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._default_model = default_model
        self._base_url = base_url
        self._max_retries = max_retries
        self._client: object | None = None  # lazy init
        # Optional observer called with a record of every chat-completion call (request, response,
        # usage, cost, reasoning, citations). Used for experiment logging; never alters behaviour.
        self.call_hook: Callable[[dict], None] | None = None

    def _log_call(self, kind: str, model: str, api_messages: list[dict], response, extra: dict | None) -> None:
        if self.call_hook is None:
            return
        try:
            msg = response.choices[0].message
            mextra = getattr(msg, "model_extra", None) or {}
            usage = response.usage
            uextra = (getattr(usage, "model_extra", None) or {}) if usage else {}
            anns = getattr(msg, "annotations", None) or mextra.get("annotations") or []
            urls = []
            for a in anns:
                a = a if isinstance(a, dict) else a.model_dump()
                u = (a.get("url_citation") or {}).get("url")
                if u:
                    urls.append(u)
            self.call_hook({
                "time": time.time(), "kind": kind, "model": model, "response_model": response.model,
                "messages": api_messages, "extra": extra or {}, "content": msg.content or "",
                "reasoning": mextra.get("reasoning") or getattr(msg, "reasoning", None) or "",
                "citations": urls, "finish_reason": response.choices[0].finish_reason,
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "cost": float(uextra.get("cost") or 0),
            })
        except Exception as exc:  # logging must never break a call
            logger.debug("call_hook failed: %s", exc)

    def _get_client(self):  # -> openai.AsyncOpenAI
        """Lazily initialise the async OpenAI client."""
        if self._client is None:
            if not self._api_key:
                raise LLMAuthenticationError(
                    "OpenAI API key not set. Provide api_key or set OPENAI_API_KEY."
                )
            try:
                import openai
            except ImportError as exc:
                raise LLMError(
                    "The 'openai' package is required for OpenAIProvider. "
                    "Install it with: pip install openai"
                ) from exc
            kwargs: dict = {
                "api_key": self._api_key,
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    # -- Message conversion ---------------------------------------------------

    @staticmethod
    def _to_openai_messages(
        messages: list[Message],
        system_prompt: str | None = None,
    ) -> list[dict]:
        """Convert Message list to OpenAI API format.

        If an explicit *system_prompt* is given it is prepended.  System
        messages in the list are preserved as-is.
        """
        result: list[dict] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for m in messages:
            # Skip system messages if we already injected an explicit one
            if m.role == MessageRole.SYSTEM and system_prompt:
                continue
            result.append({"role": m.role.value, "content": m.content})
        return result

    # -- Core interface -------------------------------------------------------

    async def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        model = model or self._default_model
        api_messages = self._to_openai_messages(messages, system_prompt)

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **({"extra_body": extra_body} if extra_body else {}),
            )
        except Exception as exc:
            self._handle_api_error(exc)
        self._log_call("generate", model, api_messages, response, extra_body)

        choice = response.choices[0]
        content = choice.message.content or ""

        raw_usage = response.usage
        usage = TokenUsage(
            prompt_tokens=raw_usage.prompt_tokens if raw_usage else 0,
            completion_tokens=raw_usage.completion_tokens if raw_usage else 0,
            total_tokens=raw_usage.total_tokens if raw_usage else 0,
            cost_usd=_reported_cost(raw_usage),
        )
        self._record_usage(model, usage)

        return LLMResponse(
            content=content,
            model_id=response.model or model,
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
            raw_response=response,
        )

    async def generate_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> T:
        """Generate structured output.

        Attempt 1: use OpenAI ``response_format`` with JSON schema.
        Attempt 2: fall back to prompt-based structured generation.
        """
        model = model or self._default_model
        client = self._get_client()

        # Attempt 1: native JSON schema mode
        try:
            json_schema = schema.model_json_schema()
            # Clean up the schema for OpenAI (remove unsupported keys)
            _clean_schema_for_openai(json_schema)

            api_messages = self._to_openai_messages(messages, system_prompt)
            response = await client.chat.completions.create(
                model=model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": json_schema,
                        "strict": False,
                    },
                },
            )
            self._log_call(f"structured:{schema.__name__}", model, api_messages, response, None)
            content = response.choices[0].message.content or ""
            raw_usage = response.usage
            usage = TokenUsage(
                prompt_tokens=raw_usage.prompt_tokens if raw_usage else 0,
                completion_tokens=raw_usage.completion_tokens if raw_usage else 0,
                total_tokens=raw_usage.total_tokens if raw_usage else 0,
                cost_usd=_reported_cost(raw_usage),
            )
            self._record_usage(model, usage)
            return self._parse_structured_response(content, schema)
        except LLMResponseParseError:
            raise
        except Exception as exc:
            logger.debug(
                "OpenAI native JSON schema mode failed for %s: %s, falling back",
                schema.__name__,
                exc,
            )

        # Attempt 2: prompt-based fallback
        return await self._generate_structured_with_fallback(
            messages,
            schema,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # -- Error handling -------------------------------------------------------

    @staticmethod
    def _handle_api_error(exc: Exception) -> None:
        exc_name = type(exc).__name__
        msg = str(exc)

        if "AuthenticationError" in exc_name:
            raise LLMAuthenticationError(f"OpenAI authentication failed: {msg}") from exc
        if "RateLimitError" in exc_name:
            raise LLMRateLimitError(f"OpenAI rate limit: {msg}") from exc
        if "Timeout" in exc_name or "timeout" in msg.lower():
            raise LLMTimeoutError(f"OpenAI API timeout: {msg}") from exc
        raise LLMError(f"OpenAI API error: {msg}") from exc


def _clean_schema_for_openai(schema: dict) -> None:
    """Remove or transform keys that OpenAI's JSON schema mode doesn't support."""
    schema.pop("title", None)
    schema.pop("description", None)
    schema.pop("examples", None)
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _clean_schema_for_openai(prop)
    if "$defs" in schema:
        for defn in schema["$defs"].values():
            if isinstance(defn, dict):
                _clean_schema_for_openai(defn)
    if "items" in schema and isinstance(schema["items"], dict):
        _clean_schema_for_openai(schema["items"])


def _reported_cost(usage: Any) -> float:
    """Cost of a call as reported by OpenRouter in ``usage.cost``; 0 when absent."""
    try:
        extra = getattr(usage, "model_extra", None) or {}
        return float(extra.get("cost") or getattr(usage, "cost", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
