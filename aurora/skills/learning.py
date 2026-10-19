"""Learning skills: from the agents' own runs (self-distillation) and from the researcher's corrections.

Both produce :class:`SkillProposal` objects, in the structured form of the skill library
(:data:`~aurora.skills.store.SKILL_SECTIONS`): a new skill, or a revision of one that exists.
The engine offers them to the researcher (or, with ``skills.auto_accept``, adds them itself).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aurora.skills.store import KINDS, SKILL_SECTIONS, DistilledSkill

logger = logging.getLogger(__name__)


class SkillProposal(BaseModel):
    action: str = Field(description="'new' for a new skill, 'revise' to replace the text of an existing one")
    name: str = Field(description="kebab-case name; for 'revise', the existing skill's name")
    description: str = Field(description="One line: what the skill is for and when to use it")
    kind: str = Field(description="procedure | failure | convention | correction")
    text: str = Field(description="The whole skill in Markdown, with a '## <section>' heading for each section")
    reason: str = Field(description="What in the material shows this is worth keeping (quote or cite it)")


class SkillProposals(BaseModel):
    skills: list[SkillProposal] = Field(description="0 to 3 proposals; empty if nothing is worth keeping")


_SECTIONS = "\n".join(f"- {h}: {what}" for h, what in SKILL_SECTIONS)

_FORMAT = f"""A skill is written in these sections, each under a '## <name>' heading:
{_SECTIONS}
Write only what the material supports; a section may say "none known yet". Be concrete: commands,
settings, thresholds, file names. A skill must be useful beyond this one task, for other
campaigns that meet the same situation."""

DISTILL_PROMPT = f"""You maintain a research team's skill library. You read what an agent just did and
keep what future campaigns should not have to learn again:
- a procedure that worked, with the settings that mattered and how it was validated (kind: procedure);
- a failure that was diagnosed, with how it showed, its cause and the fix (kind: failure).
If an existing skill covers the same ground, revise it (action 'revise', same name, the whole new
text) rather than adding a near-duplicate, and keep what in it is still right. Propose nothing for
routine work, for facts about this task's data only, or for what any competent agent does anyway.

{_FORMAT}"""

FEEDBACK_PROMPT = f"""You maintain a research team's skill library. The researcher just told a research
agent something. Decide whether it is a reusable correction or convention that agents should follow
in future campaigns too (a default setting, a validity rule, a resource or safety rule, a method
constraint), or only a decision about this task (a choice of data, scope or direction).
Only in the first case propose a skill (kind: correction or convention): the researcher's rule,
stated generally, with the situation it applies to and how to check it is followed. If an existing
skill covers it, revise that skill instead. Keep the researcher's intent; do not add rules of your own.

{_FORMAT}"""


def transcript_digest(path: str | Path, *, job: str | None = None, max_chars: int = 40000) -> str:
    """A compact account of an agent session from its JSONL transcript: what it said and did,
    its commands and their results (errors in full), within *max_chars* (start and end kept)."""
    lines: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if job and e.get("job") not in ("", None, job):
                    continue
                lines.append(_entry_line(e))
    except OSError:
        return ""
    lines = [x for x in lines if x]
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    head, tail = text[: max_chars // 5], text[-(max_chars - max_chars // 5):]
    return f"{head}\n[... {len(text) - max_chars} characters left out ...]\n{tail}"


def _entry_line(e: dict[str, Any]) -> str:
    kind = e.get("event_type") or e.get("phase") or ""
    d = e.get("data") or {}
    if kind in ("thinking", "text"):
        t = str(d.get("text") or e.get("text") or "").strip()
        return f"[{kind}] {t[:600]}" if t else ""
    if kind == "tool_call":
        inp = d.get("input") or d.get("tool_input") or {}
        what = inp.get("command") or inp.get("file_path") or json.dumps(inp)[:300] if isinstance(inp, dict) else inp
        return f"[{d.get('tool_name') or d.get('name') or 'tool'}] {str(what)[:400]}"
    if kind == "tool_result":
        out = str(d.get("output") or d.get("content") or d.get("result") or "")
        failed = d.get("is_error") or d.get("success") is False or re.search(r"Traceback|Error|error:|Killed", out)
        return f"[result{' FAILED' if failed else ''}] {out[:1500] if failed else out[:300]}"
    if kind in ("error", "permission_denied"):
        return f"[{kind}] {json.dumps(d)[:800]}"
    return ""


def library_summary(skills: list[DistilledSkill], *, max_chars: int = 12000) -> str:
    """The existing skills, for the model to revise rather than duplicate."""
    if not skills:
        return "(the library is empty)"
    parts = [f"### {s.name} (v{s.version}, {s.kind or 'rule'}, from {s.origin})\n{s.description}\n"
             f"{s.rule[:700]}" for s in skills]
    text = "\n\n".join(parts)
    return text[:max_chars]


def clean(proposals: list[SkillProposal], existing: dict[str, DistilledSkill]) -> list[SkillProposal]:
    """Normalise names and kinds; a 'new' skill named like an existing one revises it."""
    out = []
    for p in proposals:
        name = re.sub(r"[^a-z0-9]+", "-", p.name.lower()).strip("-")[:60]
        if not name or not p.text.strip():
            continue
        action = "revise" if name in existing else "new"
        kind = p.kind if p.kind in KINDS else "procedure"
        out.append(p.model_copy(update={"name": name, "action": action, "kind": kind}))
    return out


async def distill(router: Any, stage: Any, *, digest: str, what: str, library: str) -> list[SkillProposal]:
    """Self-distillation: skills worth keeping from one piece of work (a phase or a research job)."""
    from aurora.llm.base import Message
    if len(digest) < 200:
        return []
    result = await router.generate_structured(
        stage, [Message.system(DISTILL_PROMPT),
                Message.user(f"## The work\n{what}\n\n## Existing skills\n{library}\n\n"
                             f"## What the agent did (transcript digest)\n{digest}")],
        SkillProposals, temperature=0.2, max_tokens=8000)
    return SkillProposals.model_validate(dict(result)).skills[:3]


async def from_feedback(router: Any, stage: Any, *, message: str, context: str, library: str) -> list[SkillProposal]:
    """A skill from the researcher's correction, if it is a reusable one."""
    from aurora.llm.base import Message
    result = await router.generate_structured(
        stage, [Message.system(FEEDBACK_PROMPT),
                Message.user(f"## What the researcher said\n{message}\n\n## Context\n{context}\n\n"
                             f"## Existing skills\n{library}")],
        SkillProposals, temperature=0.2, max_tokens=6000)
    return SkillProposals.model_validate(dict(result)).skills[:1]
