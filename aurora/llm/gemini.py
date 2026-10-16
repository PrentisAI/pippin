"""Google Gemini LLM provider."""

from __future__ import annotations

import logging
import os
from typing import TypeVar

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


class GeminiProvider(LLMProvider):
    """LLM provider for Google Gemini models.

    Parameters
    ----------
    api_key:
        Google AI API key.  Falls back to ``GOOGLE_API_KEY`` env var.
    default_model:
        Model identifier used when no explicit model is passed.
    usage_tracker:
        Shared usage tracker instance.
    """

    provider_name = "google"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gemini-2.0-flash",
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(usage_tracker=usage_tracker)
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._default_model = default_model
        self._configured = False

    def _ensure_configured(self) -> None:
        """Configure the google-generativeai SDK (idempotent)."""
        if self._configured:
            return
        if not self._api_key:
            raise LLMAuthenticationError(
                "Google API key not set. Provide api_key or set GOOGLE_API_KEY."
            )
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise LLMError(
                "The 'google-generativeai' package is required for GeminiProvider. "
                "Install it with: pip install google-generativeai"
            ) from exc
        genai.configure(api_key=self._api_key)
        self._configured = True

    def _get_model(self, model: str | None = None):
        """Return a ``GenerativeModel`` instance for the given model name."""
        self._ensure_configured()
        import google.generativeai as genai

        return genai.GenerativeModel(model or self._default_model)

    # -- Message conversion ---------------------------------------------------

    @staticmethod
    def _to_gemini_history(
        messages: list[Message],
        system_prompt: str | None = None,
    ) -> tuple[str | None, list[dict]]:
        """Convert Message list to Gemini SDK format.

        Returns ``(system_instruction, history_parts)`` where
        ``system_instruction`` is a plain string (or None) and
        ``history_parts`` is a list of ``{"role": ..., "parts": [...]}``
        dicts suitable for ``GenerativeModel.generate_content_async``.
        """
        system = system_prompt
        history: list[dict] = []

        for m in messages:
            if m.role == MessageRole.SYSTEM:
                if not system:
                    system = m.content
                continue
            # Gemini uses "user" and "model" roles
            role = "model" if m.role == MessageRole.ASSISTANT else "user"
            history.append({"role": role, "parts": [m.content]})

        return system, history

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
        import google.generativeai as genai

        model_name = model or self._default_model
        system, history = self._to_gemini_history(messages, system_prompt)

        gen_model = genai.GenerativeModel(
            model_name,
            system_instruction=system,
        )

        try:
            self._ensure_configured()
            generation_config = genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            response = await gen_model.generate_content_async(
                history,
                generation_config=generation_config,
            )
        except Exception as exc:
            self._handle_api_error(exc)

        content = response.text if response.text else ""

        # Token counting — Gemini exposes usage metadata when available
        usage_meta = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        self._record_usage(model_name, usage)

        finish = "stop"
        if response.candidates:
            fr = getattr(response.candidates[0], "finish_reason", None)
            if fr is not None:
                finish = str(fr).lower().replace("finishreason.", "")

        return LLMResponse(
            content=content,
            model_id=model_name,
            usage=usage,
            finish_reason=finish,
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

        Attempt 1: use Gemini ``response_mime_type="application/json"``
        with a ``response_schema``.
        Attempt 2: fall back to prompt-based structured generation.
        """
        import google.generativeai as genai

        model_name = model or self._default_model
        system, history = self._to_gemini_history(messages, system_prompt)

        # Attempt 1: native JSON mode
        try:
            self._ensure_configured()
            gen_model = genai.GenerativeModel(
                model_name,
                system_instruction=system,
            )
            generation_config = genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
            )
            response = await gen_model.generate_content_async(
                history,
                generation_config=generation_config,
            )
            content = response.text if response.text else ""

            usage_meta = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
            completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
            self._record_usage(model_name, usage)

            return self._parse_structured_response(content, schema)
        except LLMResponseParseError:
            raise
        except Exception as exc:
            logger.debug(
                "Gemini native JSON mode failed for %s: %s, falling back",
                schema.__name__,
                exc,
            )

        # Attempt 2: prompt-based fallback
        return await self._generate_structured_with_fallback(
            messages,
            schema,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    async def close(self) -> None:
        # google-generativeai SDK doesn't have an explicit close
        self._configured = False

    # -- Error handling -------------------------------------------------------

    @staticmethod
    def _handle_api_error(exc: Exception) -> None:
        exc_name = type(exc).__name__
        msg = str(exc)

        if "PermissionDenied" in exc_name or "InvalidArgument" in exc_name:
            raise LLMAuthenticationError(f"Gemini auth error: {msg}") from exc
        if "ResourceExhausted" in exc_name or "quota" in msg.lower():
            raise LLMRateLimitError(f"Gemini rate limit: {msg}") from exc
        if "DeadlineExceeded" in exc_name or "timeout" in msg.lower():
            raise LLMTimeoutError(f"Gemini timeout: {msg}") from exc
        raise LLMError(f"Gemini API error: {msg}") from exc
