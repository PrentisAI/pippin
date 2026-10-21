"""Which model providers a project may use.

The team's rules can limit the providers everyone may use (``allowed_providers``; empty: any).  A
confidential project uses only providers that keep no data (``no_retention_providers``, by default
Anthropic and OpenAI, whose API terms do not retain or train on requests).  A model is named the
OpenRouter way, ``provider/model``.
"""

from __future__ import annotations

from typing import Any

PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "deepseek": "DeepSeek",
    "moonshotai": "Moonshot", "z-ai": "Z.ai", "qwen": "Qwen", "x-ai": "xAI", "meta-llama": "Meta",
    "mistralai": "Mistral", "minimax": "MiniMax",
}
# shown as choices in the team's rules
LISTED = ("anthropic", "openai", "google", "deepseek", "moonshotai", "z-ai", "qwen", "x-ai", "meta-llama", "mistralai")
_BARE = {"claude": "anthropic", "gpt": "openai", "o1": "openai", "o3": "openai", "o4": "openai", "gemini": "google",
         "deepseek": "deepseek", "kimi": "moonshotai", "glm": "z-ai", "qwen": "qwen", "grok": "x-ai"}


def provider_of(model: str) -> str:
    """``anthropic`` from ``anthropic/claude-haiku-5.5``; '' for a model that calls no provider (mock)."""
    m = (model or "").strip().lower()
    if not m or m.startswith("mock"):
        return ""
    if "/" in m:
        return m.split("/", 1)[0].lstrip("~")
    for prefix, provider in _BARE.items():
        if m.startswith(prefix):
            return provider
    return m.split("-", 1)[0]


def _names(providers: list[str]) -> str:
    names = [PROVIDER_NAMES.get(p, p) for p in providers]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def model_problem(model: str, campaign_id: str = "", rules: dict[str, Any] | None = None) -> str:
    """Why *model* may not be used (in *campaign_id*), or ''."""
    from aurora.auth.store import auth_store
    provider = provider_of(model)
    if not provider:
        return ""
    rules = rules if rules is not None else auth_store.rules()
    allowed = [p.lower() for p in rules.get("allowed_providers") or []]
    if allowed and provider not in allowed:
        return (f"The team’s rules allow models from {_names(allowed)} only; {model} is from "
                f"{PROVIDER_NAMES.get(provider, provider)}.")
    if campaign_id and (auth_store.project_access(campaign_id) or {}).get("confidential"):
        safe = [p.lower() for p in rules.get("no_retention_providers") or []]
        if provider not in safe:
            return (f"This project is confidential: it uses only providers that keep no data ({_names(safe)}); "
                    f"{model} is from {PROVIDER_NAMES.get(provider, provider)}.")
    return ""


def models_problem(models: list[str], campaign_id: str = "") -> str:
    """The first problem among several models, or ''."""
    from aurora.auth.store import auth_store
    rules = auth_store.rules()
    for m in dict.fromkeys(x for x in models if x):
        problem = model_problem(m, campaign_id, rules)
        if problem:
            return problem
    return ""


def config_models(cfg: Any) -> list[str]:
    """Every model a run's configuration will call (the round table's only when ideation runs)."""
    ms = cfg.models
    skip = set(getattr(cfg, "skip_phases", []) or [])
    out = [ms.agent, *(getattr(ms, p) for p in ("screening", "planning", "preflight", "research", "analysis", "writing")
                       if p not in skip), ms.auditor, ms.skills, ms.supervisor, ms.frontdesk]
    if "ideation" not in skip:
        out += [*ms.ideation_seats, ms.ideation_chair]
    return [m for m in out if isinstance(m, str) and m]
