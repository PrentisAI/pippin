"""One OpenRouter client for the LLM judges and generators of all experiments.

Every call needs ``OPENROUTER_API_KEY`` in the environment.  Spend is accumulated in a plain dict
(``{"prompt": tokens, "completion": tokens, "cost": usd}``) so callers can enforce budgets.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

BASE_URL = "https://openrouter.ai/api/v1"


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set; it is needed only for steps that call models.")
    return key


def client(**headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key()}", **headers})


def new_spend() -> dict:
    return {"prompt": 0, "completion": 0, "cost": 0.0}


def parse_json_object(text: str) -> dict:
    """The outermost ``{...}`` of a model reply."""
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


async def chat_json(http: httpx.AsyncClient, model: str, prompt: str, spend: dict | None = None, *,
                    temperature: float | None = 0, max_tokens: int | None = None, attempts: int = 4,
                    timeout: float = 600) -> dict:
    """Single-turn chat completion that must return a JSON object.

    Retries on HTTP and parse errors (backoff 5 s, 10 s, ...).  Returns the parsed object, or
    ``{"error": "..."}`` after the last attempt.  ``temperature=None`` uses the provider default.
    """
    body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}, "usage": {"include": True}}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    for attempt in range(attempts):
        try:
            r = await http.post(f"{BASE_URL}/chat/completions", json=body, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            u = j.get("usage") or {}
            if spend is not None:
                spend["prompt"] = spend.get("prompt", 0) + (u.get("prompt_tokens") or 0)
                spend["completion"] = spend.get("completion", 0) + (u.get("completion_tokens") or 0)
                spend["cost"] = spend.get("cost", 0.0) + float(u.get("cost") or 0)
            return parse_json_object(j["choices"][0]["message"].get("content") or "")
        except Exception as exc:  # noqa: BLE001 - any failure is retried, then reported
            if attempt == attempts - 1:
                return {"error": str(exc)}
            await asyncio.sleep(5 * (attempt + 1))
    return {"error": "no attempts"}


async def key_usage(http: httpx.AsyncClient) -> float:
    """Total spend of the API key so far (USD), for budget checks of long runs."""
    r = await http.get(f"{BASE_URL}/key")
    r.raise_for_status()
    return float(r.json()["data"]["usage"])
