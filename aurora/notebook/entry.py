"""A run's page in the notebook, written from the run itself: the idea it took, the hypothesis behind it and what
would refute it, why it was worth trying (the gap, why this idea over the others, the alternatives, screening's
scores), what it expected, and what it found (its claims and recorded results).  What it expected is set against
what it found by a model, once per run and again whenever the findings change."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _list(v: Any) -> list[str]:
    """A field that is a list, or a list written out as text, or one string."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").strip()
    if s.startswith("["):
        try:
            got = ast.literal_eval(s)
            if isinstance(got, list):
                return [str(x).strip() for x in got if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    return [s] if s else []


def _dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    try:
        got = ast.literal_eval(str(v or ""))
        return got if isinstance(got, dict) else {}
    except (ValueError, SyntaxError):
        return {}


def _proposals(run_dir: Path) -> list[dict[str, Any]]:
    """The run's proposals, oldest first."""
    root = run_dir / "phases" / "ideation"
    props = []
    for p in sorted(root.glob("round_*/*.json"), key=lambda p: p.stat().st_mtime) if root.is_dir() else []:
        if p.name.startswith("meeting_state"):
            continue
        try:
            props.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return props


def _chosen(props: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """The idea the run took, and what the researcher said when they chose it ('' if nothing). A direction the
    researcher wrote can be marked selected without an idea of its own ("the latest proposal is fine"): then the idea
    is the proposal it points at, the screened one, else the latest written out in full."""
    def full(p: dict[str, Any]) -> bool:
        return bool(p.get("hypothesis") or p.get("expected_outcomes"))
    selected = [p for p in props if str(p.get("selected")).lower() == "true"]
    said = next((str(p.get("description") or p.get("title") or "") for p in reversed(selected) if not full(p)), "")
    pick = ([p for p in selected if full(p)] or [p for p in props if full(p) and p.get("score") not in (None, "None")]
            or [p for p in props if full(p)] or selected or props or [{}])[-1]
    return pick, said


def _scores(run_dir: Path) -> dict[str, Any]:
    scores = sorted((run_dir / "phases" / "screening").glob("round_*/score.json"), key=lambda p: p.stat().st_mtime)
    if not scores:
        return {}
    try:
        s = json.loads(scores[-1].read_text())
    except (OSError, ValueError):
        return {}
    return {k: s.get(k) for k in ("novelty", "feasibility", "significance", "composite") if s.get(k) is not None}


def _claims(run_dir: Path) -> list[dict[str, Any]]:
    try:
        art = json.loads((run_dir / "artifacts.json").read_text())
    except (OSError, ValueError):
        return []
    return [{"statement": c.get("statement", ""), "status": c.get("status", ""), "confidence": c.get("confidence")}
            for c in art.get("claims") or [] if c.get("statement")][:30]


def entry(run: Any) -> dict[str, Any]:
    """The run's page, as far as it has got."""
    from aurora.results.ledger import ResultsLedger
    d = run.campaign_dir
    p, said = _chosen(_proposals(d))
    decision = _dict(p.get("decision"))
    results = [{k: r.get(k) for k in ("name", "value", "unit", "uncertainty", "description")}
               for r in ResultsLedger(d).all(current=True)][:30]
    report = sorted((d / "phases" / "writing").glob("draft_*.md"))
    return {
        "run_id": run.run_id, "name": run.task_name or run.run_id, "status": run.status, "phase": run.phase,
        "idea": {"title": str(p.get("title") or ""), "description": str(p.get("description") or "")},
        "hypothesis": str(p.get("hypothesis") or ""),
        "refuted_if": str(p.get("falsification_check") or ""),
        "why": {"gap": _list(p.get("research_questions"))[:3], "rationale": str(decision.get("rationale") or ""),
                "alternatives": str(p.get("alternatives_considered") or ""), "scores": _scores(d)},
        "expected": _list(p.get("expected_outcomes"))[:12],
        "claims": _claims(d), "results": results, "report": bool(report),
        "has_idea": bool(p.get("title") or p.get("hypothesis")), "you_said": said[:600],
    }


def match_key(e: dict[str, Any]) -> str:
    """What a comparison is judged from: a new claim or result means judging again."""
    basis = json.dumps([e["hypothesis"], e["expected"], [(c["statement"], c["status"]) for c in e["claims"]],
                        [(r["name"], r["value"]) for r in e["results"]]], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def judgeable(e: dict[str, Any]) -> bool:
    return bool(e["expected"] or e["hypothesis"]) and bool(e["claims"] or e["results"])


async def judge(access: Any, models: dict[str, str], e: dict[str, Any]) -> dict[str, Any]:
    """Each expectation against what the run found, and the hypothesis as a whole: held, partly, not, or not tested."""
    from aurora.review import llm
    exp = "\n".join(f"E{i + 1}. {x}" for i, x in enumerate(e["expected"])) or "(none written)"
    found = "\n".join(f"- [{c['status'] or 'claim'}] {c['statement']}" for c in e["claims"])
    nums = "\n".join(f"- {r['name']} = {r['value']}{(' ' + r['unit']) if r.get('unit') else ''}"
                     + (f" ± {r['uncertainty']}" if r.get("uncertainty") not in (None, "") else "") for r in e["results"])
    res = await llm.ask(access.with_model(models["memory"]), "notebook_expected_vs_actual", f"""A research run set out \
with this hypothesis and these expected outcomes. Set each against what it found.

Hypothesis: {e['hypothesis'] or '(none written)'}
It would be refuted if: {e['refuted_if'] or '(not said)'}

Expected:
{exp}

What it found (claims, with how they stood):
{found or '(none)'}

Recorded results:
{nums or '(none)'}

For each expectation: what was actually found, in one short sentence with the numbers if there are any, and a verdict: \
"held", "partly", "not" (the finding contradicts it), or "untested" (nothing found speaks to it). An expected deliverable \
(a table, a figure) held if it was made. Then the hypothesis as a whole, with one sentence why. Never invent a finding.

JSON: {{"items": [{{"n": 1, "actual": "...", "verdict": "held"}}], "hypothesis": {{"verdict": "partly", "why": "..."}}}}""",
                         want=None, max_tokens=4000)
    res = res if isinstance(res, dict) else {}
    ok = {"held", "partly", "not", "untested"}
    items = []
    for i, x in enumerate(e["expected"]):
        got = next((y for y in res.get("items") or [] if isinstance(y, dict) and int(y.get("n") or 0) == i + 1), {})
        v = str(got.get("verdict") or "untested")
        items.append({"expected": x, "actual": str(got.get("actual") or ""), "verdict": v if v in ok else "untested"})
    h = res.get("hypothesis") if isinstance(res.get("hypothesis"), dict) else {}
    hv = str(h.get("verdict") or "untested")
    return {"items": items, "hypothesis": {"verdict": hv if hv in ok else "untested", "why": str(h.get("why") or "")}}
