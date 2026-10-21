"""The skill library API: list, read, create, edit, revise and delete skills; accept suggestions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.skills.store import ORIGINS, SCOPES, SKILL_SECTIONS, DistilledSkill, SkillStore, skill_template

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
    rule: str                      # the skill's text (Markdown in the standard sections)
    description: str = ""
    source_phase: str = ""
    source_experiment: str = ""
    source_campaign: str = ""
    scope: str = "experiment"
    tags: list[str] = []
    origin: str = "manual"         # human | self | manual
    kind: str = ""                 # procedure | failure | convention | correction
    skill_id: str = ""             # accepting a revision: the skill it revises
    reason: str = ""               # why (kept in the skill's history on a revision)
    suggestion_id: str = ""        # the suggestion accepted (answered once; repeats change nothing)
    projects: list[str] = []       # scope "projects": the projects that may use it


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    rule: str | None = None
    description: str | None = None
    scope: str | None = None       # experiment | campaign | projects | global
    projects: list[str] | None = None
    source_experiment: str | None = None
    source_campaign: str | None = None
    tags: list[str] | None = None
    kind: str | None = None


def _check_scope(scope: str | None, projects: list[str] | None, source_experiment: str = "",
                 source_campaign: str = "") -> None:
    if scope is None:
        return
    if scope not in SCOPES:
        raise HTTPException(400, f"scope must be one of {list(SCOPES)}")
    if scope == "projects" and not projects:
        raise HTTPException(400, "scope 'projects' needs at least one project")
    if scope == "experiment" and not source_experiment:
        raise HTTPException(400, "scope 'experiment' needs the experiment it belongs to")
    if scope == "campaign" and not source_campaign:
        raise HTTPException(400, "scope 'campaign' needs the project it belongs to")


def _gate(scope: str, source_campaign: str, current: DistilledSkill | None = None) -> dict[str, str]:
    """Rules on reaching beyond one project: a confidential project's skills stay in it; with the team
    rule on, a member's skill reaches other projects once an admin approves.  The approval fields."""
    if scope not in ("projects", "global"):
        return {"approval": ""}
    from aurora.auth.guard import current_user
    from aurora.auth.store import auth_store
    if source_campaign and (auth_store.project_access(source_campaign) or {}).get("confidential"):
        raise HTTPException(400, "The project it was learned in is confidential: its skills stay in it")
    if current is not None and current.scope == scope and current.approval in ("", "pending"):
        return {"approval": current.approval, "approved_by": current.approved_by}
    user = current_user()
    if user.is_admin or not auth_store.rules().get("skills_need_approval"):
        return {"approval": "", "approved_by": "" if user.is_local else user.user_id}
    return {"approval": "pending", "approved_by": ""}


class SkillReviseRequest(BaseModel):
    rule: str
    reason: str = ""
    description: str | None = None


@router.get("/pending")
async def pending_skills() -> list[DistilledSkill]:
    """Skills waiting for a team admin before they reach other projects."""
    return get_skill_store().pending()


class Approval(BaseModel):
    approve: bool


@router.post("/{skill_id}/approval")
async def decide_skill(skill_id: str, body: Approval) -> DistilledSkill:
    """Approve a skill for other projects, or keep it where it was learned."""
    from aurora.auth.guard import current_user
    from aurora.auth.store import auth_store
    store = get_skill_store()
    skill = store.get(skill_id)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    if skill.approval != "pending":
        raise HTTPException(409, "This skill is not waiting for approval")
    user = current_user()
    out = store.update(skill_id, approval="" if body.approve else "rejected", approved_by=user.user_id)
    auth_store.log(user.user_id, ("approved" if body.approve else "kept in its project") + f" the skill “{skill.name}”",
                   skill.source_campaign)
    return out  # type: ignore[return-value]


@router.get("/template")
async def skill_template_text() -> dict:
    """The standard sections of a skill, and an empty skill in them."""
    return {"sections": [{"name": h, "what": w} for h, w in SKILL_SECTIONS], "template": skill_template()}


@router.get("/suggestions")
async def suggestion_outcomes(ids: str = "") -> dict[str, dict]:
    """What was done with the given suggestions (comma-separated IDs): added or dismissed."""
    return get_skill_store().outcomes([i for i in ids.split(",") if i][:500])


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(suggestion_id: str) -> dict:
    get_skill_store().record_outcome(suggestion_id, "dismissed")
    return {"state": "dismissed"}


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
    _check_scope(req.scope, req.projects, req.source_experiment, req.source_campaign)
    existing = store.get_by_name(req.name)
    if existing:
        raise HTTPException(409, f"Skill '{req.name}' already exists")
    gate = _gate(req.scope, req.source_campaign)
    skill = DistilledSkill(**gate,
        name=req.name,
        rule=req.rule,
        description=req.description,
        source_phase=req.source_phase,
        source_experiment=req.source_experiment,
        source_campaign=req.source_campaign,
        scope=req.scope,
        projects=req.projects,
        tags=req.tags,
        origin=req.origin if req.origin in ORIGINS else "manual",
        kind=req.kind,
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
    current = store.get(skill_id)
    if current is None:
        raise HTTPException(404, "Skill not found")
    _check_scope(req.scope, fields.get("projects", current.projects),
                 fields.get("source_experiment", current.source_experiment),
                 fields.get("source_campaign", current.source_campaign))
    if req.scope is not None:
        fields.update(_gate(req.scope, fields.get("source_campaign", current.source_campaign), current))
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


@router.post("/{skill_id}/revise")
async def revise_skill(skill_id: str, req: SkillReviseRequest) -> DistilledSkill:
    """A new version of a skill; the current one is kept in its history."""
    skill = get_skill_store().revise(skill_id, req.rule, reason=req.reason, description=req.description)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@router.post("/suggest", status_code=200)
async def accept_suggestion(req: SkillCreateRequest) -> DistilledSkill:
    """Accept a suggested skill: a new one, or a revision of an existing one (by ID or name).

    A suggestion is accepted once: accepting it again returns the skill without a new version."""
    store = get_skill_store()
    if not req.skill_id:            # a revision keeps the skill's scope
        _check_scope(req.scope, req.projects, req.source_experiment, req.source_campaign)
    if req.suggestion_id:
        done = store.outcomes([req.suggestion_id]).get(req.suggestion_id)
        if done and done.get("state") == "added" and (sk := store.get(done.get("skill_id", ""))):
            return sk
    existing = store.get(req.skill_id) if req.skill_id else store.get_by_name(req.name)
    if existing:
        revised = store.revise(existing.skill_id, req.rule, reason=req.reason or "accepted suggestion",
                               description=req.description or None, source_experiment=req.source_experiment)
        if req.scope == "global" and existing.scope != "global":
            revised = store.update(existing.skill_id, scope="global",
                                   **_gate("global", existing.source_campaign, existing))
        result = revised or existing
        _record_added(req.suggestion_id, result)
        return result
    skill = DistilledSkill(
        name=req.name,
        rule=req.rule,
        description=req.description,
        source_phase=req.source_phase,
        source_experiment=req.source_experiment,
        source_campaign=req.source_campaign,
        scope=req.scope,
        projects=req.projects,
        tags=req.tags,
        origin=req.origin if req.origin in ORIGINS else "manual",
        kind=req.kind,
        **_gate(req.scope, req.source_campaign),
    )
    created = store.create(skill)
    _record_added(req.suggestion_id, created)
    return created


def _record_added(suggestion_id: str, skill: DistilledSkill) -> None:
    if suggestion_id:
        get_skill_store().record_outcome(suggestion_id, "added", skill_id=skill.skill_id, name=skill.name,
                                         version=skill.version, scope=skill.scope)
