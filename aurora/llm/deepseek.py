"""DeepSeek LLM provider (OpenAI-compatible API)."""

from __future__ import annotations

import os
from typing import TypeVar

from pydantic import BaseModel

from aurora.llm.base import UsageTracker
from aurora.llm.openai_provider import OpenAIProvider

T = TypeVar("T", bound=BaseModel)

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAIProvider):
    """LLM provider for DeepSeek models.

    DeepSeek exposes an OpenAI-compatible API, so this subclass simply
    overrides the defaults for base URL, API key env-var, and model name.

    Parameters
    ----------
    api_key:
        DeepSeek API key.  Falls back to ``DEEPSEEK_API_KEY`` env var.
    default_model:
        Model identifier used when no explicit model is passed.
    base_url:
        API endpoint.  Defaults to ``https://api.deepseek.com``.
    max_retries:
        Maximum retries on transient errors.
    usage_tracker:
        Shared usage tracker instance.
    """

    provider_name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "deepseek-chat",
        base_url: str | None = None,
        max_retries: int = 2,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        super().__init__(
            api_key=resolved_key,
            default_model=default_model,
            base_url=base_url or _DEEPSEEK_BASE_URL,
            max_retries=max_retries,
            usage_tracker=usage_tracker,
        )
