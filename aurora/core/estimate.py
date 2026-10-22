"""What an experiment will likely cost and how long it will take, before it starts.

Two sources, the better one first:

* **earlier runs** with the same phases and the same research model (in the projects the person
  can see): the middle half of what they cost and took;
* otherwise **typical phases**: what each phase costs with a Claude Sonnet-class model, times the
  price of the models chosen (OpenRouter's prices, or a rough class by name when they cannot be
  fetched), and how long each phase usually takes.  The number of research jobs is not known before
  planning, so the range assumes two to five.

The run is then watched: it says so (and notifies) when it costs or takes more than the upper end.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

PHASES = ("ideation", "screening", "planning", "preflight", "research", "analysis", "writing")
# per phase with a Sonnet-class model: (cost low, cost high in US$), (minutes low, high)
TYPICAL: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "ideation": ((0.6, 2.0), (8, 20)),          # the round table; independent ideation costs about a third
    "screening": ((0.1, 0.4), (2, 6)),
    "planning": ((0.2, 0.8), (3, 10)),
    "preflight": ((0.3, 1.5), (5, 20)),
    "research_job": ((0.8, 4.0), (15, 90)),     # one research job
    "analysis": ((0.2, 0.8), (3, 10)),
    "writing": ((0.3, 1.2), (5, 15)),
}
JOBS = (2, 5)
OVERSIGHT = 0.2                                  # auditors and the live auditor, on top of the research cost
SONNET = "anthropic/claude-sonnet-5.5"
# rough price relative to Sonnet when OpenRouter's prices are not available
_CLASSES = [("opus", 5.0), ("sonnet", 1.0), ("haiku", 0.3), ("gpt-5.6", 1.0), ("gpt-5-mini", 0.15), ("gpt-5", 0.8),
            ("gemini-3.1-pro", 0.8), ("gemini", 0.15), ("deepseek", 0.15), ("kimi", 0.4), ("glm", 0.3),
            ("qwen", 0.3), ("minimax", 0.2), ("grok", 0.8), ("mock", 0.0)]

_price_cache: tuple[float, dict[str, float]] | None = None


def model_prices(base_url: str = "https://openrouter.ai/api/v1") -> dict[str, float] | None:
    """US$ per million tokens, a 3:1 mix of input and output, by model (cached for an hour)."""
    global _price_cache
    if _price_cache and time.time() - _price_cache[0] < 3600:
        return _price_cache[1]
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=10) as resp:
            data = json.load(resp).get("data", [])
    except Exception as exc:
        logger.info("Could not fetch model prices (%s); estimating by model class", exc)
        return None
    prices = {}
    for m in data:
        p = m.get("pricing") or {}
        try:
            prices[m["id"]] = (3 * float(p.get("prompt", 0)) + float(p.get("completion", 0))) / 4 * 1e6
        except (TypeError, ValueError):
            continue
    _price_cache = (time.time(), prices)
    return prices


def price_factor(model: str, prices: dict[str, float] | None) -> float:
    """A model's price relative to Claude Sonnet."""
    if prices and model in prices and prices.get(SONNET):
        return max(prices[model] / prices[SONNET], 0.01)
    m = (model or "").lower()
    for key, factor in _CLASSES:
        if key in m:
            return factor
    return 1.0


def _active(skip: list[str]) -> list[str]:
    return [p for p in PHASES if p not in skip]


def typical(models: dict[str, Any], skip: list[str], ideation_mode: str, parallel: int,
            prices: dict[str, float] | None) -> dict[str, Any]:
    """The estimate from typical phase costs and durations."""
    agent = models.get("agent") or SONNET
    per_phase = []
    for phase in _active(skip):
        if phase == "ideation":
            seats = models.get("ideation_seats") or [SONNET] * 3
            f = (sum(price_factor(s, prices) for s in seats) / len(seats) * 0.75
                 + price_factor(models.get("ideation_chair") or SONNET, prices) * 0.25)
            (c0, c1), (t0, t1) = TYPICAL["ideation"]
            if ideation_mode == "independent":
                c0, c1, t0, t1 = c0 / 3, c1 / 3, t0 / 2, t1 / 2
            per_phase.append({"phase": phase, "cost": (c0 * f, c1 * f), "minutes": (t0, t1)})
        elif phase == "research":
            f = price_factor(models.get("research") or agent, prices)
            (c0, c1), (t0, t1) = TYPICAL["research_job"]
            rounds = (max(1, -(-JOBS[0] // parallel)), max(1, -(-JOBS[1] // parallel)))
            per_phase.append({"phase": phase, "cost": (c0 * JOBS[0] * f, c1 * JOBS[1] * f),
                              "minutes": (t0 * rounds[0], t1 * rounds[1]), "jobs": JOBS})
        else:
            f = price_factor(models.get(phase) or agent, prices)
            (c0, c1), (t0, t1) = TYPICAL[phase]
            per_phase.append({"phase": phase, "cost": (c0 * f, c1 * f), "minutes": (t0, t1)})
    research = next((p["cost"] for p in per_phase if p["phase"] == "research"), (0.0, 0.0))
    of = (price_factor(models.get("auditor") or SONNET, prices) + price_factor(models.get("supervisor") or SONNET, prices)) / 2
    oversight = (research[0] * OVERSIGHT * of, research[1] * OVERSIGHT * of)
    cost = (sum(p["cost"][0] for p in per_phase) + oversight[0], sum(p["cost"][1] for p in per_phase) + oversight[1])
    minutes = (sum(p["minutes"][0] for p in per_phase), sum(p["minutes"][1] for p in per_phase))
    return {"cost": cost, "hours": (minutes[0] / 60, minutes[1] / 60),
            "per_phase": [{**p, "cost": [round(p["cost"][0], 2), round(p["cost"][1], 2)],
                           "minutes": [round(p["minutes"][0]), round(p["minutes"][1])]} for p in per_phase],
            "oversight": [round(oversight[0], 2), round(oversight[1], 2)]}


def _quartiles(xs: list[float]) -> tuple[float, float, float]:
    xs = sorted(xs)
    if len(xs) < 4:
        return xs[0], statistics.median(xs), xs[-1]
    q = statistics.quantiles(xs, n=4)
    return q[0], q[1], q[2]


def estimate(models: dict[str, Any], skip: list[str], *, ideation_mode: str = "roundtable", parallel: int = 1,
             history: list[dict[str, Any]] | None = None, prices: dict[str, float] | None = None) -> dict[str, Any]:
    """``history``: earlier runs, ``{cost_usd, hours, skip_phases, research_model, name}``."""
    base = typical(models, skip, ideation_mode, max(1, parallel), prices)
    research_model = models.get("research") or models.get("agent") or SONNET
    same = [h for h in history or [] if sorted(h.get("skip_phases") or []) == sorted(skip)
            and h.get("research_model") == research_model and h.get("cost_usd", 0) > 0]
    out: dict[str, Any] = {"per_phase": base["per_phase"], "oversight": base["oversight"], "similar": len(same)}
    if len(same) >= 3:
        c = _quartiles([h["cost_usd"] for h in same])
        t = _quartiles([h["hours"] for h in same if h.get("hours")] or [0.0])
        out.update(cost=[round(c[0], 2), round(c[2], 2)], cost_mid=round(c[1], 2), hours=[round(t[0], 1), round(t[2], 1)],
                   basis=f"from {len(same)} earlier runs with these phases and this research model (the middle half)")
    else:
        earlier = ", ".join(f"${h['cost_usd']:.2f}" for h in same[:3])
        out.update(cost=[round(base["cost"][0], 2), round(base["cost"][1], 2)],
                   cost_mid=round((base["cost"][0] + base["cost"][1]) / 2, 2),
                   hours=[round(base["hours"][0], 1), round(base["hours"][1], 1)],
                   basis="from typical phase costs at these models’ prices, assuming two to five research jobs"
                         + (f"; {len(same)} earlier run{'s' if len(same) > 1 else ''} with these settings cost {earlier}"
                            if same else ""))
    out["note"] = "Excludes the time a run waits for your decisions."
    return out


def history_from_runs(runs: list[Any]) -> list[dict[str, Any]]:
    """Finished runs as the estimator reads them (from their saved configuration)."""
    import yaml
    out = []
    for r in runs:
        if r.status != "completed" or not r.finished_at or not r.started_at:
            continue
        try:
            cfg = yaml.safe_load((r.campaign_dir / "config.yaml").read_text()) or {}
        except Exception:
            continue
        if cfg.get("mock_mode"):
            continue
        m = cfg.get("models") or {}
        out.append({"cost_usd": float(r.cost_usd or 0), "hours": (r.finished_at - r.started_at) / 3600,
                    "skip_phases": cfg.get("skip_phases") or [], "research_model": m.get("research") or m.get("agent") or SONNET,
                    "name": r.task_name})
    return out


def show(est: dict[str, Any]) -> str:
    """'$3–6, 1–3 h' for a notification or a log line."""
    def money(x: float) -> str:
        return f"${x:.0f}" if x >= 10 else f"${x:.1f}" if x >= 1 else f"${x:.2f}"
    h0, h1 = est["hours"]
    return f"{money(est['cost'][0])}–{money(est['cost'][1])}, {h0:g}–{h1:g} h"

