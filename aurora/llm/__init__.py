"""Multi-provider LLM abstraction layer for AURORA.

Provides a unified async interface across Anthropic (Claude), OpenAI (GPT),
Google (Gemini), and DeepSeek models.  The :class:`LLMRouter` maps each
:class:`~aurora.config.PipelineStage` to the correct provider and model
so that agents never instantiate providers directly.
"""

from __future__ import annotations

from aurora.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    TokenUsage,
    UsageTracker,
    LLMError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMResponseParseError,
)
from aurora.llm.router import LLMRouter
from aurora.llm.mock import MockProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMRouter",
    "Message",
    "MessageRole",
    "MockProvider",
    "TokenUsage",
    "UsageTracker",
    "LLMError",
    "LLMAuthenticationError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "LLMResponseParseError",
]
