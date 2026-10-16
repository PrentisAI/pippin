"""LLM provider abstraction layer.

Defines the abstract :class:`LLMProvider` interface, data classes for
messages and responses, a :class:`UsageTracker` for cumulative token
accounting, and a hierarchy of LLM-specific exceptions.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar, Type

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base exception for all LLM provider errors."""


class LLMAuthenticationError(LLMError):
    """Raised when API authentication fails (invalid or missing key)."""


class LLMRateLimitError(LLMError):
    """Raised when the provider returns a rate-limit / quota-exceeded error."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    """Raised when an API call times out."""


class LLMResponseParseError(LLMError):
    """Raised when a structured response cannot be parsed into the target schema."""

    def __init__(self, message: str, raw_content: str = ""):
        super().__init__(message)
        self.raw_content = raw_content


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class MessageRole(str, Enum):
    """Role of a message in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    """A single message in a conversation."""

    role: MessageRole
    content: str

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role=MessageRole.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role=MessageRole.USER, content=content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        return cls(role=MessageRole.ASSISTANT, content=content)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts for a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0   # as reported by the provider (OpenRouter), else 0


@dataclass(slots=True)
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model_id: str
    usage: TokenUsage
    finish_reason: str = "stop"
    raw_response: Any = None


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

class UsageTracker:
    """Accumulates token usage per provider and model.

    Thread-safe for typical asyncio use (single event loop).
    """

    def __init__(self) -> None:
        self._usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        )
        self._cost = 0.0

    def record(self, provider: str, model: str, usage: TokenUsage) -> None:
        """Record token usage for a single call."""
        key = f"{provider}/{model}"
        entry = self._usage[key]
        entry["prompt_tokens"] += usage.prompt_tokens
        entry["completion_tokens"] += usage.completion_tokens
        entry["total_tokens"] += usage.total_tokens
        entry["call_count"] += 1
        self._cost += usage.cost_usd

    @property
    def total_tokens(self) -> int:
        """Sum of all tokens across all providers and models."""
        return sum(e["total_tokens"] for e in self._usage.values())

    @property
    def total_cost(self) -> float:
        """Sum of the provider-reported cost (USD) of all calls."""
        return self._cost

    def summary(self) -> dict[str, dict[str, int]]:
        """Return a snapshot of accumulated usage per ``provider/model``."""
        return dict(self._usage)

    def reset(self) -> None:
        """Clear all accumulated usage."""
        self._usage.clear()
        self._cost = 0.0


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Subclasses must implement :meth:`generate`, :meth:`generate_structured`,
    and :meth:`close`.
    """

    provider_name: str  # e.g., "anthropic", "openai", "google", "deepseek"

    def __init__(self, usage_tracker: UsageTracker | None = None) -> None:
        self._usage_tracker = usage_tracker or UsageTracker()

    @property
    def usage_tracker(self) -> UsageTracker:
        return self._usage_tracker

    # -- core interface -------------------------------------------------------

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Generate a completion from *messages*.

        Parameters
        ----------
        messages:
            Conversation history.
        model:
            Override the default model for this call.
        temperature:
            Sampling temperature.
        max_tokens:
            Maximum tokens in the response.
        system_prompt:
            Optional system prompt (handled provider-specifically).
        """
        ...

    @abstractmethod
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
        """Generate a structured response conforming to a Pydantic model.

        Implementations should attempt native JSON-mode or tool-use first,
        falling back to prompt-and-parse with one retry on failure.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release provider resources (HTTP clients, etc.)."""
        ...

    # -- helpers for subclasses -----------------------------------------------

    def _record_usage(self, model: str, usage: TokenUsage) -> None:
        """Record usage through the shared tracker."""
        self._usage_tracker.record(self.provider_name, model, usage)

    @staticmethod
    def _schema_to_json_description(schema: Type[BaseModel]) -> str:
        """Return a human-readable JSON schema string for prompt injection."""
        json_schema = schema.model_json_schema()
        return json.dumps(json_schema, indent=2)

    @staticmethod
    def _parse_structured_response(raw: str, schema: Type[T]) -> T:
        """Attempt to parse *raw* text as JSON conforming to *schema*.

        Handles common issues like markdown code fences around JSON.

        Raises
        ------
        LLMResponseParseError
            If parsing fails.
        """
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        try:
            return schema.model_validate_json(text)
        except Exception as exc:
            # Try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return schema.model_validate_json(text[start : end + 1])
                except Exception:
                    pass
            raise LLMResponseParseError(
                f"Failed to parse response as {schema.__name__}: {exc}",
                raw_content=raw,
            ) from exc

    async def _generate_structured_with_fallback(
        self,
        messages: list[Message],
        schema: Type[T],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system_prompt: str | None = None,
    ) -> T:
        """Fallback structured generation via prompt injection + parsing.

        Subclasses can call this when their native JSON mode is unavailable or
        fails.
        """
        schema_desc = self._schema_to_json_description(schema)
        injection = (
            f"\n\nYou MUST respond with ONLY a valid JSON object conforming "
            f"to this schema (no markdown fences, no extra text):\n"
            f"```json\n{schema_desc}\n```"
        )

        augmented_messages = list(messages)
        if augmented_messages and augmented_messages[-1].role == MessageRole.USER:
            last = augmented_messages[-1]
            augmented_messages[-1] = Message(role=last.role, content=last.content + injection)
        else:
            augmented_messages.append(Message.user(f"Respond with JSON:\n{injection}"))

        response = await self.generate(
            augmented_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

        return self._parse_structured_response(response.content, schema)
