"""The first minutes and the help that stays: the welcome wizard's state and a person's defaults, the
getting-started checklist, the examples that come with Pippin, the guided tour, first-time tips, and
the documentation in the console.

Everything is per person (``user_prefs``): every new account goes through the wizard once.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aurora import examples, help, home
from aurora.auth.guard import accounts_on, current_user
from aurora.auth.store import auth_store

router = APIRouter(prefix="/api", tags=["onboarding"])

FIELDS = ("materials", "ml", "bio", "data", "other")
TOUR_CHAPTERS = ("run", "app", "start")


def _pref(key: str, default: Any = None) -> Any:
    return auth_store.get_pref(current_user().user_id, key, default)


def _set(key: str, value: Any) -> None:
    auth_store.set_pref(current_user().user_id, key, value)


def _my_runs(user_id: str) -> list[dict[str, Any]]:
    """The real (not scripted, not example) runs a person started."""
    from aurora.api.routes.campaigns import store
    out = []
    for row in store.list_campaigns():
        if examples.is_example(row["campaign_id"]):
            continue
        for e in store.list_experiments(row["campaign_id"]):
            try:
                cfg = json.loads(e.get("config_json") or "{}")
            except ValueError:
                continue
            if (cfg.get("started_by") or "local") == user_id and not (cfg.get("request") or {}).get("mock_mode"):
                out.append(e)
    return out


def checklist() -> list[dict[str, Any]]:
    """What a person has tried of Pippin, each with where to do it.  Done-ness comes from what they did."""
    from aurora.api.routes.campaigns import live_notifications
    from aurora.auth.secrets import key_for
    me = current_user()
    facts = auth_store.usage_facts(me.user_id)
    tours = set(_pref("onboarding_tours", []) or [])
    try:
        notify = any(c.enabled for c in live_notifications(None if me.is_local else me.user_id).channels)
    except Exception:
        notify = False
    items = [
        ("key", "Connect a model key", "One OpenRouter key for every model", bool(key_for(me)[0]), "key"),
        ("tour", "Walk through the example", "Six minutes over a finished run", "run" in tours, "tour:run"),
        ("demo", "Watch a run happen", "A scripted study, in seconds, no cost", bool(_pref("onboarding_demo")), "demo"),
        ("run", "Start your own research", "From a template or your own question", bool(_my_runs(me.user_id)), "start"),
        ("decide", "Answer a decision", "Approve, give an instruction, or stop", bool(_pref("onboarding_decided")), "doc:concepts/human-in-the-loop.md"),
        ("trace", "Trace a number in a report", "Click a result to see the script behind it", bool(_pref("onboarding_traced")), "tour:run"),
        ("brainstorm", "Brainstorm an idea", "With a Skeptic, Methods and a Librarian", facts["brainstormed"], "brainstorm"),
        ("papers", "Bring in your papers", "Search the literature, or connect Zotero", facts["papers"] or facts["zotero"], "library"),
        ("notify", "Get decisions on your phone", "WeCom, Feishu, Slack, WeChat or a webhook", notify, "settings:notifications"),
        ("around", "Find your way around", "Library, search, analytics, skills, settings", "app" in tours, "tour:app"),
    ]
    if me.is_local or me.is_admin:
        from aurora.api.routes.campaigns import base_config
        compute = (base_config().get("compute") or {})
        gpu = bool((compute.get("slurm") or {}).get("enabled") or (compute.get("ssh") or {}).get("enabled"))
        items.append(("gpu", "Connect GPUs (optional)", "A SLURM cluster or a GPU machine over SSH", gpu, "doc:guide/cluster-jobs.md"))
    if accounts_on() and me.is_admin:
        items.append(("team", "Invite your colleagues", "Each signs in and uses their own key",
                      len(auth_store.list_users()) > 1, "team"))
    return [{"key": k, "label": label, "hint": hint, "done": done, "action": action}
            for k, label, hint, done, action in items]


def _defaults() -> dict[str, Any]:
    from aurora.api.routes.campaigns import base_config, default_max_cost
    cfg = base_config()
    return {"autonomy": (cfg.get("human") or {}).get("autonomy", "supervised"), "max_cost_usd": default_max_cost()}


@router.get("/onboarding")
async def get_onboarding() -> dict[str, Any]:
    me = current_user()
    return {
        "wizard": {"done": bool(_pref("onboarding_done")), "step": _pref("onboarding_step", "welcome")},
        "field": _pref("onboarding_field", ""),
        **_defaults(),
        "tours": _pref("onboarding_tours", []) or [],
        "tips_seen": _pref("onboarding_tips", []) or [],
        "examples_hidden": bool(_pref("examples_hidden")),
        "accounts": accounts_on(), "admin": me.is_local or me.is_admin, "name": me.name,
        "checklist": checklist(),        # same thread as every other use of the database connection
    }


class OnboardingUpdate(BaseModel):
    step: Literal["welcome", "connect", "prefs", "start"] | None = None
    done: bool | None = None
    field: str | None = None
    tour_done: Literal["run", "app", "start"] | None = None
    tip_seen: str | None = None
    tips_reset: bool | None = None
    traced: bool | None = None
    examples_hidden: bool | None = None


@router.put("/onboarding")
async def update_onboarding(body: OnboardingUpdate) -> dict[str, Any]:
    if body.step is not None:
        _set("onboarding_step", body.step)
    if body.done is not None:
        _set("onboarding_done", body.done)
        if not body.done:
            _set("onboarding_step", "welcome")
    if body.field is not None:
        if body.field and body.field not in FIELDS:
            raise HTTPException(400, f"field must be one of {', '.join(FIELDS)}")
        _set("onboarding_field", body.field)
    if body.tour_done is not None:
        tours = set(_pref("onboarding_tours", []) or [])
        _set("onboarding_tours", sorted(tours | {body.tour_done}))
    if body.tip_seen:
        tips = set(_pref("onboarding_tips", []) or [])
        _set("onboarding_tips", sorted(tips | {body.tip_seen[:60]}))
    if body.tips_reset:
        _set("onboarding_tips", [])
    if body.traced:
        _set("onboarding_traced", True)
    if body.examples_hidden is not None:
        _set("examples_hidden", body.examples_hidden)
    return await get_onboarding()


class DefaultsBody(BaseModel):
    autonomy: Literal["supervised", "autonomous"]
    max_cost_usd: float | None = None


@router.put("/onboarding/defaults")
async def set_defaults(body: DefaultsBody) -> dict[str, Any]:
    """How hands-on new runs are and their budget: the console's defaults without accounts, else the
    person's own (over the console's)."""
    if body.max_cost_usd is not None and body.max_cost_usd <= 0:
        raise HTTPException(400, "The budget must be above 0, or none")
    me = current_user()
    if me.is_local:
        from aurora.api.console_settings import load_overlay, save_overlay, with_changes
        save_overlay(with_changes(load_overlay(), {"human.autonomy": body.autonomy, "budget.max_cost_usd": body.max_cost_usd}))
    else:
        mine = dict(_pref("defaults", {}) or {})
        mine["human"] = {**(mine.get("human") or {}), "autonomy": body.autonomy}
        mine["budget"] = {**(mine.get("budget") or {}), "max_cost_usd": body.max_cost_usd}
        _set("defaults", mine)
    return await get_onboarding()


# ------------------------------------------------------------------------------------------ examples

@router.get("/examples")
async def list_examples() -> dict[str, Any]:
    from aurora.api.routes.campaigns import store
    from aurora.paths import aurora_home
    removed = set(examples.removed_ids(aurora_home()))
    cards = []
    for c in examples.catalogue():
        installed = c["kind"] == "scripted" or bool(store.get_campaign(c.get("campaign_id", "")))
        cards.append({**c, "installed": installed, "removed": c["id"] in removed})
    return {"examples": cards, "hidden": bool(_pref("examples_hidden"))}


@router.post("/examples/{example_id}/duplicate")
async def duplicate_example(example_id: str) -> dict[str, Any]:
    """A project of one's own with the example's question, ready to run (nothing runs yet)."""
    from aurora.api.routes.campaigns import create_quick_project, store
    card = next((c for c in examples.catalogue() if c["id"] == example_id and c["kind"] != "scripted"), None)
    if card is None:
        raise HTTPException(404, "No such example")
    row = store.get_campaign(card["campaign_id"]) or {}
    objective = row.get("objective") or ""
    project = await create_quick_project(f"{card['title']} (my copy)", objective)
    return {**project, "brief": objective}


@router.post("/examples/{example_id}/install")
async def install_example(example_id: str) -> dict[str, Any]:
    """Put an example (back) into this console (admins)."""
    from aurora.api.routes.campaigns import _campaigns, store
    from aurora.paths import aurora_home
    examples.set_removed(aurora_home(), example_id, False)
    cid = await asyncio.to_thread(examples.install, example_id, home=aurora_home(), store=store)
    if cid is None:
        raise HTTPException(404, "No such example")
    _campaigns.pop(cid, None)
    return {"campaign_id": cid}


@router.delete("/examples/{example_id}")
async def remove_example(example_id: str) -> dict[str, Any]:
    """Take an example out of this console for everyone (admins); it stays out after updates."""
    from aurora.api.routes.campaigns import _campaigns, _projects_root, store
    from aurora.paths import aurora_home
    cid = examples.project_id(example_id)
    examples.set_removed(aurora_home(), example_id, True)
    _campaigns.pop(cid, None)
    store.delete_campaign(cid)
    store.delete_events(cid)
    shutil.rmtree(_projects_root() / cid, ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------------------------------------ help

@router.get("/help/docs")
async def help_pages() -> dict[str, Any]:
    return {"pages": help.pages()}


@router.get("/help/docs/{path:path}")
async def help_page(path: str) -> dict[str, Any]:
    doc = help.page(path)
    if doc is None:
        raise HTTPException(404, "No such page")
    return doc


@router.get("/help/assets/{name}")
async def help_asset(name: str) -> FileResponse:
    f = help.asset(name)
    if f is None:
        raise HTTPException(404, "No such file")
    return FileResponse(f)


class AskBody(BaseModel):
    question: str


class HomeBody(BaseModel):
    text: str
    history: list[dict[str, Any]] = []


@router.post("/home/reply")
async def home_reply(body: HomeBody) -> dict[str, Any]:
    """What the person means by what they typed on the start page, and a sentence back
    (``{"intent": None}`` without a key or when the model cannot be asked)."""
    from aurora.api.routes.campaigns import NO_KEY, base_config
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Say something")
    cfg = base_config()
    key = cfg.get("api_key") or ""
    if not key or key == NO_KEY:
        import os
        key = os.environ.get("OPENROUTER_API_KEY", "") if current_user().is_local else ""
    if not key:
        return {"intent": None, "reply": ""}
    model = (cfg.get("models") or {}).get("frontdesk") or "anthropic/claude-haiku-5.5"
    r = await home.reply(text, body.history, api_key=key, base_url=cfg.get("api_base_url") or "https://openrouter.ai/api/v1",
                         model=model)
    return r or {"intent": None, "reply": ""}


@router.post("/help/ask")
async def help_ask(body: AskBody) -> dict[str, Any]:
    """An answer to "how do I ..." from the documentation."""
    from aurora.api.routes.campaigns import NO_KEY, base_config
    question = body.question.strip()[:500]
    if not question:
        raise HTTPException(400, "Ask a question")
    cfg = base_config()
    key = cfg.get("api_key") or ""
    if not key or key == NO_KEY:
        import os
        key = os.environ.get("OPENROUTER_API_KEY", "") if current_user().is_local else ""
    model = (cfg.get("models") or {}).get("frontdesk") or "anthropic/claude-haiku-5.5"
    return await help.answer(question, api_key=key, base_url=cfg.get("api_base_url") or "https://openrouter.ai/api/v1",
                             model=model)
