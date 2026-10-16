"""Distilled skills API — CRUD endpoints for learned rules/patterns."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.skills.store import DistilledSkill, SkillStore

router = APIRouter(prefix="/api/skills", tags=["skills"])

_store: SkillStore | None = None


def get_skill_store() -> SkillStore:
    global _store
    if _store is None:
        from aurora.paths import aurora_home
        _store = SkillStore(aurora_home() / "skills.db")
    return _store


class SkillCreateRequest(BaseModel):
    name: str
    rule: str
    description: str = ""
    source_phase: str = ""
    source_experiment: str = ""
    source_campaign: str = ""
    scope: str = "experiment"
    tags: list[str] = []


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    rule: str | None = None
    description: str | None = None
    scope: str | None = None
    tags: list[str] | None = None


class SkillSuggestion(BaseModel):
    """A skill candidate suggested by auto-distillation."""
    name: str
    rule: str
    description: str = ""
    source_phase: str = ""
    source_experiment: str = ""
    confidence: float = 0.0


@router.get("/")
async def list_skills(
    scope: str | None = None,
    experiment: str | None = None,
    q: str | None = None,
) -> list[DistilledSkill]:
    store = get_skill_store()
    if q:
        return store.search(q)
    return store.list_all(scope=scope, source_experiment=experiment)


@router.post("/", status_code=201)
async def create_skill(req: SkillCreateRequest) -> DistilledSkill:
    store = get_skill_store()
    existing = store.get_by_name(req.name)
    if existing:
        raise HTTPException(409, f"Skill '{req.name}' already exists")
    skill = DistilledSkill(
        name=req.name,
        rule=req.rule,
        description=req.description,
        source_phase=req.source_phase,
        source_experiment=req.source_experiment,
        source_campaign=req.source_campaign,
        scope=req.scope,
        tags=req.tags,
    )
    return store.create(skill)


@router.get("/{skill_id}")
async def get_skill(skill_id: str) -> DistilledSkill:
    store = get_skill_store()
    skill = store.get(skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@router.patch("/{skill_id}")
async def update_skill(skill_id: str, req: SkillUpdateRequest) -> DistilledSkill:
    store = get_skill_store()
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    skill = store.update(skill_id, **fields)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str) -> None:
    store = get_skill_store()
    if not store.delete(skill_id):
        raise HTTPException(404, "Skill not found")


@router.get("/experiment/{experiment_id}")
async def list_skills_for_experiment(
    experiment_id: str, campaign_id: str = ""
) -> list[DistilledSkill]:
    """Return all skills relevant to a specific experiment (own + campaign + global)."""
    store = get_skill_store()
    return store.list_for_experiment(experiment_id, campaign_id=campaign_id)


@router.post("/suggest", status_code=200)
async def accept_suggestion(req: SkillCreateRequest) -> DistilledSkill:
    """Accept an auto-distilled skill suggestion — same as create but with semantic distinction."""
    store = get_skill_store()
    existing = store.get_by_name(req.name)
    if existing:
        return store.update(existing.skill_id, rule=req.rule, description=req.description) or existing
    skill = DistilledSkill(
        name=req.name,
        rule=req.rule,
        description=req.description,
        source_phase=req.source_phase,
        source_experiment=req.source_experiment,
        source_campaign=req.source_campaign,
        scope=req.scope,
        tags=req.tags,
    )
    return store.create(skill)
