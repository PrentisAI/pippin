"""Model calls of the field map and the deep read: one task per call, answered as JSON, written to one style.

Every prompt starts with ``TASK: <name>`` (so a run's log, and the tests, can tell the calls apart),
then the shared writing rules, then the task. Answers that are prose come as paragraphs, each with
the kind of claim it makes (see :data:`LABELS`), so the console can show which is which.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

LABELS = ("paper", "lit", "inf", "spec")

STYLE = """\
How to write:
- Plain, clear and deep, in the manner of Andrej Karpathy and Kaiming He: every sentence carries information. \
No filler, no sweeping generalities, no hype.
- Specific, checkable claims: name the paper, the benchmark, the condition, the number when you have it.
- Fluent prose. Avoid dashes as punctuation, scare quotes, and the construction "not X, but Y".
- Keep four kinds of claim apart and label each paragraph with one of them:
  "paper" (the paper itself states or shows it), "lit" (established in other literature; name it),
  "inf" (a reasonable inference from evidence; say from what), "spec" (your own guess, without direct evidence).
  Never present an inference as a fact, or a guess as an inference.
- Cite papers only by the keys given to you, in square brackets, exactly as given. Never invent a paper or a key."""

PARAGRAPHS = '[{"text": "...", "label": "paper|lit|inf|spec"}]'
RETRY_WAIT = 2.0        # seconds before the first retry of a failed call (tests set 0)


@dataclass
class Access:
    api_key: str
    base_url: str
    model: str
    missing: str = ""

    def with_model(self, model: str) -> Access:
        return replace(self, model=model or self.model)


# makes the OpenAI-compatible client (None: the real one); tests put a fake here
client_factory: Any = None


def _client(access: Access) -> Any:
    if client_factory is not None:
        return client_factory(access)
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=access.api_key, base_url=access.base_url)


class ModelError(Exception):
    """The model could not be reached or did not answer usably."""


def parse_json(text: str) -> Any:
    """The JSON in a model's answer (also inside a code fence, or after some words)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON in the answer")


async def ask(access: Access, task: str, body: str, *, temperature: float = 0.3, max_tokens: int = 8000,
              system: str = "", retries: int = 2, web: int = 0, want: str | None = "") -> Any:
    """One call: ``TASK: <task>``, the style, the body; returns the parsed JSON.

    ``web``: let the model search the web first (OpenRouter's web plugin, this many results per search).
    ``want``: the answer is an object; a bare list that came instead is taken as its ``want`` field (or, with
    ``want=""``, its first object). ``None``: the answer as it came."""
    if not access.api_key:
        raise ModelError(f"No API key is set: {access.missing}")
    prompt = f"TASK: {task}\n\n{STYLE}\n\n{body}\n\nAnswer with JSON only."
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    last = ""
    for attempt in range(retries + 1):
        try:
            extra = {"extra_body": {"plugins": [{"id": "web", "max_results": web}]}} if web else {}
            resp = await _client(access).chat.completions.create(model=access.model, messages=messages,
                                                                 temperature=temperature, max_tokens=max_tokens, **extra)
            last = resp.choices[0].message.content or ""
            return shape(parse_json(last), want)
        except ValueError:
            messages = [*messages, {"role": "assistant", "content": last},
                        {"role": "user", "content": "That was not valid JSON. Answer again with the JSON only."}]
        except Exception as exc:
            if attempt == retries:
                raise ModelError(f"{access.model}: {str(exc)[:300]}") from exc
            await asyncio.sleep(RETRY_WAIT * (attempt + 1))
    raise ModelError(f"{access.model} did not answer with JSON: {last[:200]}")


def shape(out: Any, want: str | None) -> Any:
    """An answer in the shape asked for: models sometimes answer an object with the list it holds."""
    if want is None or isinstance(out, dict):
        return out
    if isinstance(out, list):
        if want:
            return {want: out}
        return next((x for x in out if isinstance(x, dict)), {})
    return {}


_LABEL_PREFIX = re.compile(r"^\s*(?:\[\s*)?(?:paper|lit|inf|spec)\s*(?:\]\s*)?[:：|]+\s*|^\s*\[(?:paper|lit|inf|spec)\]\s*", re.I)


def unlabel(text: Any) -> str:
    """A plain string field without the claim label a model sometimes puts in front of it ("spec: ...", "[inf]")
    or the separators it leaves ("||")."""
    t = _LABEL_PREFIX.sub("", str(text or ""))
    return " ".join(t.replace("||", " ").split())


def paragraphs(items: Any) -> list[dict[str, str]]:
    """Paragraphs as the console shows them: text and a known label (an unknown one counts as a guess)."""
    out = []
    for p in items if isinstance(items, list) else [items]:
        if isinstance(p, str):
            p = {"text": p, "label": "inf"}
        if not isinstance(p, dict) or not str(p.get("text", "")).strip():
            continue
        label = str(p.get("label", "")).lower()
        out.append({"text": " ".join(str(p["text"]).split()), "label": label if label in LABELS else "spec"})
    return out


async def gather_limited(coros: list[Any], limit: int = 6) -> list[Any]:
    """Run calls side by side, at most ``limit`` at once; a failed one gives its exception."""
    sem = asyncio.Semaphore(limit)

    async def one(c: Any) -> Any:
        async with sem:
            try:
                return await c
            except Exception as exc:
                logger.info("a review call failed: %s", exc)
                return exc

    return await asyncio.gather(*(one(c) for c in coros))


def settled(results: list[Any], what: str) -> list[Any]:
    """The results that came; if none did, the first failure (a step with nothing to show has failed)."""
    good = [r for r in results if not isinstance(r, BaseException)]
    bad = [r for r in results if isinstance(r, BaseException)]
    if bad and not good:
        raise ModelError(f"Every {what} failed: {bad[0]}")
    return good
