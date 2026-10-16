"""LLM router — maps pipeline stages to providers and models.

The :class:`LLMRouter` is the **single point** where model assignment is
resolved.  Agents call ``router.generate(stage, ...)`` and never instantiate
providers directly.
"""

from __future__ import annotations

import logging
import os
from typing import Type, TypeVar

from pydantic import BaseModel

from aurora.config import LLMProviderConfig, AuroraConfig, PipelineStage
from aurora.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    TokenUsage,
    UsageTracker,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Provider prefix → provider_name mapping (used when auto-detecting)
_MODEL_PREFIX_TO_PROVIDER: dict[str, str] = {
    "claude-": "anthropic",
    "gpt-": "openai",
    "o1-": "openai",
    "o3-": "openai",
    "gemini-": "google",
    "deepseek-": "deepseek",
    "mock-": "mock",
}

# OpenRouter-style prefixes (provider/model format)
_OPENROUTER_PREFIX_TO_PROVIDER: dict[str, str] = {
    "anthropic/": "openrouter",
    "openai/": "openrouter",
    "google/": "openrouter",
    "deepseek/": "openrouter",
    "meta-llama/": "openrouter",
    "mistralai/": "openrouter",
    "qwen/": "openrouter",
    "moonshotai/": "openrouter",
    "x-ai/": "openrouter",
    "z-ai/": "openrouter",
    "minimax/": "openrouter",
}


class LLMRouter:
    """Routes pipeline stages to the correct LLM provider and model.

    Parameters
    ----------
    config:
        Full campaign configuration — used for model assignments and
        provider credentials.
    providers:
        Pre-built provider instances, keyed by provider name (e.g.
        ``"anthropic"``).  If ``None``, providers are created lazily
        from the configuration.
    usage_tracker:
        Shared token-usage tracker.  Created if not supplied.
    """

    def __init__(
        self,
        config: AuroraConfig,
        providers: dict[str, LLMProvider] | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._config = config
        self._usage_tracker = usage_tracker or UsageTracker()
        self._providers: dict[str, LLMProvider] = providers or {}
        self._provider_configs: dict[str, LLMProviderConfig] = {
            pc.provider: pc for pc in config.llm_providers
        }

    # -- provider resolution --------------------------------------------------

    def _provider_name_for_model(self, model_id: str) -> str:
        """Infer the provider name from a model identifier prefix."""
        # Check OpenRouter-style prefixes first (provider/model)
        for prefix, provider in _OPENROUTER_PREFIX_TO_PROVIDER.items():
            if model_id.startswith(prefix):
                return provider
        for prefix, provider in _MODEL_PREFIX_TO_PROVIDER.items():
            if model_id.startswith(prefix):
                return provider
        raise LLMError(
            f"Cannot determine provider for model '{model_id}'. "
            f"Known prefixes: {list(_MODEL_PREFIX_TO_PROVIDER.keys())}"
        )

    def _get_or_create_provider(self, provider_name: str) -> LLMProvider:
        """Return a cached provider or create one from config."""
        if provider_name in self._providers:
            return self._providers[provider_name]

        pc = self._provider_configs.get(provider_name)

        provider: LLMProvider
        if provider_name == "anthropic":
            from aurora.llm.claude import ClaudeProvider

            provider = ClaudeProvider(
                api_key=pc.api_key if pc else None,
                usage_tracker=self._usage_tracker,
            )
        elif provider_name == "openai":
            from aurora.llm.openai_provider import OpenAIProvider

            provider = OpenAIProvider(
                api_key=pc.api_key if pc else None,
                base_url=pc.base_url if pc else None,
                usage_tracker=self._usage_tracker,
            )
        elif provider_name == "google":
            from aurora.llm.gemini import GeminiProvider

            provider = GeminiProvider(
                api_key=pc.api_key if pc else None,
                usage_tracker=self._usage_tracker,
            )
        elif provider_name == "deepseek":
            from aurora.llm.deepseek import DeepSeekProvider

            provider = DeepSeekProvider(
                api_key=pc.api_key if pc else None,
                base_url=pc.base_url if pc else None,
                usage_tracker=self._usage_tracker,
            )
        elif provider_name == "openrouter":
            from aurora.llm.openai_provider import OpenAIProvider

            provider = OpenAIProvider(
                api_key=(pc.api_key if pc else None) or os.environ.get("OPENROUTER_API_KEY", ""),
                base_url=(pc.base_url if pc else None) or "https://openrouter.ai/api/v1",
                usage_tracker=self._usage_tracker,
            )
        elif provider_name == "mock":
            from aurora.llm.mock import MockProvider

            provider = MockProvider(usage_tracker=self._usage_tracker)
        else:
            raise LLMError(f"Unknown LLM provider: {provider_name}")

        self._providers[provider_name] = provider
        return provider

    # -- stage → (provider, model) resolution ---------------------------------

    def get_provider_and_model(
        self, stage: PipelineStage, *, seat_index: int = 0
    ) -> tuple[LLMProvider, str]:
        """Return ``(provider, model_id)`` for a given pipeline *stage*.

        Parameters
        ----------
        stage:
            The pipeline stage to look up.
        seat_index:
            For ``ROUNDTABLE_SEAT``, selects among the configured seat
            models (wraps around if out of range).
        """
        model_id = self._config.llm_assignment.get_model(stage, seat_index)
        provider_name = self._provider_name_for_model(model_id)
        provider = self._get_or_create_provider(provider_name)
        return provider, model_id

    # -- convenience wrappers -------------------------------------------------

    async def generate(
        self,
        stage: PipelineStage,
        messages: list[Message],
        *,
        seat_index: int = 0,
        **kwargs,
    ) -> LLMResponse:
        """Route and generate in one call."""
        provider, model = self.get_provider_and_model(stage, seat_index=seat_index)
        return await provider.generate(messages, model=model, **kwargs)

    async def generate_structured(
        self,
        stage: PipelineStage,
        messages: list[Message],
        schema: Type[T],
        *,
        seat_index: int = 0,
        **kwargs,
    ) -> T:
        """Route and generate structured output in one call."""
        provider, model = self.get_provider_and_model(stage, seat_index=seat_index)
        return await provider.generate_structured(messages, schema, model=model, **kwargs)

    # -- lifecycle & reporting ------------------------------------------------

    @property
    def usage_tracker(self) -> UsageTracker:
        return self._usage_tracker

    def get_usage_summary(self) -> dict[str, dict[str, int]]:
        """Return accumulated token usage per ``provider/model``."""
        return self._usage_tracker.summary()

    async def close_all(self) -> None:
        """Close all provider clients."""
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception:
                logger.warning("Error closing provider %s", provider.provider_name, exc_info=True)
        self._providers.clear()


def create_router(config: AuroraConfig) -> LLMRouter:
    """Factory: create an :class:`LLMRouter` from a campaign config.

    Providers are created lazily on first use — this function just wires up
    the configuration and shared usage tracker.
    """
    tracker = UsageTracker()
    return LLMRouter(config=config, usage_tracker=tracker)
