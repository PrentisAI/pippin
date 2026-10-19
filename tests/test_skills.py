"""The skill library: structured skills with versions, self-distillation from runs, skills from the
researcher's corrections, and skills reaching the agents."""

import asyncio
import json
import sqlite3

from aurora.config import AuroraConfig
from aurora.core.engine import ResearchEngine
from aurora.core.events import Event
from aurora.skills import learning
from aurora.skills.learning import SkillProposal
from aurora.skills.store import DistilledSkill, SkillStore, missing_sections, skill_template

TEXT = skill_template().replace("<", "").replace(">", "")


def test_revisions_keep_the_history(tmp_path):
    store = SkillStore(tmp_path / "s.db")
    sk = store.create(DistilledSkill(name="chunked-read", rule="v1 text", description="d", origin="self",
                                     kind="procedure", scope="global"))
    store.revise(sk.skill_id, "v2 text", reason="OOM on 80M rows")
    got = store.get(sk.skill_id)
    assert (got.rule, got.version, got.origin) == ("v2 text", 2, "self")
    assert got.history[0]["text"] == "v1 text" and got.history[0]["replaced_because"] == "OOM on 80M rows"
    assert missing_sections(TEXT) == [] and "Failure modes" in missing_sections("## Purpose\nx")


def test_an_older_library_is_upgraded(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE distilled_skills (skill_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        rule TEXT NOT NULL, description TEXT DEFAULT '', source_phase TEXT DEFAULT '',
        source_experiment TEXT DEFAULT '', scope TEXT DEFAULT 'experiment', tags TEXT DEFAULT '[]',
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    conn.execute("INSERT INTO distilled_skills VALUES ('a','old-rule','do X','', '', '', 'global', '[]', 1, 1)")
    conn.commit()
    conn.close()
    [sk] = SkillStore(db).list_all()
    assert (sk.name, sk.version, sk.origin, sk.history) == ("old-rule", 1, "manual", [])


def test_digest_keeps_failures_and_filters_by_job(tmp_path):
    t = tmp_path / "t.jsonl"
    rows = [
        {"event_type": "tool_call", "job": "a", "data": {"tool_name": "Bash", "tool_input": {"command": "python clean.py"}}},
        {"event_type": "tool_result", "job": "a", "data": {"output": "Traceback ... MemoryError" + "x" * 900,
                                                           "is_error": True}},
        {"event_type": "thinking", "job": "b", "data": {"text": "other job"}},
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows))
    digest = learning.transcript_digest(t, job="a")
    assert "python clean.py" in digest and "MemoryError" in digest and "FAILED" in digest
    assert "other job" not in digest


def test_a_new_skill_named_like_an_existing_one_revises_it():
    existing = {"chunked-read": DistilledSkill(name="chunked-read", rule="r")}
    out = learning.clean([SkillProposal(action="new", name="Chunked Read", description="d", kind="weird",
                                        text=TEXT, reason="r")], existing)
    assert out[0].name == "chunked-read" and out[0].action == "revise" and out[0].kind == "procedure"


def make_engine(tmp_path, monkeypatch, **cfg):
    monkeypatch.setenv("AURORA_HOME", str(tmp_path / "home"))
    engine = ResearchEngine(AuroraConfig(objective="Citi Bike usage", mock_mode=True, campaign_id="sk",
                                         workspace={"root": str(tmp_path)}, **cfg))
    engine._setup()
    events = []

    async def record(event):
        events.append((event.name, event.data))

    engine.event_bus.subscribe_all(record)
    return engine, events


PROPOSAL = SkillProposal(action="new", name="chunked-aggregation", kind="failure",
                         description="Aggregate large trip tables in chunks", text=TEXT,
                         reason="the full-table groupby was OOM-killed at 8 GB")


async def test_run_lessons_are_offered_as_suggestions(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"event_type": "text", "data": {"text": "x" * 400}}))

    async def distill(router, stage, *, digest, what, library):
        assert "research job clean" in what
        return [PROPOSAL]

    monkeypatch.setattr(learning, "distill", distill)
    await engine._learn_from_work("research job clean (clean data), which succeeded", str(t), job="clean")
    [(name, data)] = [e for e in events if e[0] == "skill_suggestion"]
    assert data["action"] == "new" and data["origin"] == "self" and data["text"] == TEXT
    assert engine._skill_store.list_all() == []                      # nothing saved without you


async def test_auto_accept_adds_and_revises_and_agents_see_it(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch, skills={"auto_accept": True})
    await engine._offer_skills([PROPOSAL], origin="self", source_phase="research")
    await engine._offer_skills([PROPOSAL.model_copy(update={"text": TEXT + "\nmore"})], origin="self",
                               source_phase="research")
    [sk] = engine._skill_store.list_all()
    assert sk.version == 2 and sk.scope == "global"
    assert [e[1]["action"] for e in events if e[0] == "skill_learned"] == ["new", "revise"]
    skill_md = tmp_path / "sk" / ".claude" / "skills" / "chunked-aggregation" / "SKILL.md"
    assert skill_md.exists() and "## Failure modes" in skill_md.read_text()
    assert "chunked-aggregation: Aggregate large trip tables in chunks" in engine._skills_note()


async def test_a_researcher_correction_becomes_a_suggestion(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch)
    seen = {}

    async def from_feedback(router, stage, *, message, context, library):
        seen["message"] = message
        return [PROPOSAL.model_copy(update={"name": "no-kill-on-shared-gpus", "kind": "correction"})]

    monkeypatch.setattr(learning, "from_feedback", from_feedback)
    await engine._on_feedback_event(Event(name="human_gate_resolved", data={
        "action": "instruct", "message": "never kill processes you did not start", "decided_by": "human",
        "title": "The live auditor escalated"}))
    await engine._on_feedback_event(Event(name="human_gate_resolved", data={     # no words: nothing to learn
        "action": "continue", "message": "", "decided_by": "human"}))
    await engine._finish_skill_learning()
    assert seen["message"] == "never kill processes you did not start"
    [(_, data)] = [e for e in events if e[0] == "skill_suggestion"]
    assert data["origin"] == "human" and data["feedback"] == "never kill processes you did not start"


async def test_learning_can_be_turned_off(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch, skills={"distill": False, "from_feedback": False})
    called = []

    async def fail(*a, **k):
        called.append(1)
        return []

    monkeypatch.setattr(learning, "distill", fail)
    monkeypatch.setattr(learning, "from_feedback", fail)
    await engine._learn_from_work("x", str(tmp_path / "none.jsonl"))
    await engine._on_feedback_event(Event(name="instruction_relayed", data={"message": "use float64"}))
    await asyncio.sleep(0.05)
    assert called == []


def test_api_accepts_a_revision_and_serves_the_template(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aurora.api.app import create_app
    from aurora.api.routes import skills as skills_routes
    monkeypatch.setattr(skills_routes, "_store", SkillStore(tmp_path / "api.db"))
    with TestClient(create_app()) as client:
        assert "## Validation checks" in client.get("/api/skills/template").json()["template"]
        sk = client.post("/api/skills/suggest", json={"name": "chunked", "rule": "v1", "scope": "global",
                                                      "origin": "self", "kind": "failure"}).json()
        rev = client.post("/api/skills/suggest", json={"name": "chunked", "rule": "v2", "scope": "global",
                                                       "skill_id": sk["skill_id"], "reason": "better"}).json()
        assert rev["version"] == 2 and rev["history"][0]["replaced_because"] == "better"
        assert client.delete(f"/api/skills/{sk['skill_id']}").status_code == 204


def test_answered_suggestions_stay_answered(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aurora.api.app import create_app
    from aurora.api.routes import skills as skills_routes
    monkeypatch.setattr(skills_routes, "_store", SkillStore(tmp_path / "api.db"))
    card = {"name": "chunked", "rule": "v1", "scope": "global", "origin": "self", "suggestion_id": "s1"}
    with TestClient(create_app()) as client:
        first = client.post("/api/skills/suggest", json=card).json()
        again = client.post("/api/skills/suggest", json=card).json()           # a second click, or a reload
        assert again["skill_id"] == first["skill_id"] and again["version"] == 1
        client.post("/api/skills/suggestions/s2/dismiss")
        got = client.get("/api/skills/suggestions", params={"ids": "s1,s2,s3"}).json()
        assert got["s1"]["state"] == "added" and got["s1"]["version"] == 1 and got["s1"]["scope"] == "global"
        assert got["s2"]["state"] == "dismissed" and "s3" not in got


async def test_every_suggestion_has_an_id(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch)
    await engine._offer_skills([PROPOSAL, PROPOSAL.model_copy(update={"name": "other"})], origin="self",
                               source_phase="research")
    ids = [d["suggestion_id"] for n, d in events if n == "skill_suggestion"]
    assert len(ids) == 2 and len(set(ids)) == 2 and all(ids)


def test_scopes_decide_who_uses_a_skill(tmp_path):
    store = SkillStore(tmp_path / "s.db")
    for name, scope, extra in (("everywhere", "global", {}),
                               ("run-only", "experiment", {"source_experiment": "run1"}),
                               ("project-only", "campaign", {"source_campaign": "pA"}),
                               ("two-projects", "projects", {"projects": ["pA", "pC"]})):
        store.create(DistilledSkill(name=name, rule="r", scope=scope, **extra))

    def names(run, project):
        return sorted(s.name for s in store.list_for_experiment(run, campaign_id=project))

    assert names("run1", "pA") == ["everywhere", "project-only", "run-only", "two-projects"]
    assert names("run2", "pA") == ["everywhere", "project-only", "two-projects"]
    assert names("run3", "pB") == ["everywhere"]
    assert names("run4", "pC") == ["everywhere", "two-projects"]


def test_api_checks_and_changes_scopes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aurora.api.app import create_app
    from aurora.api.routes import skills as skills_routes
    monkeypatch.setattr(skills_routes, "_store", SkillStore(tmp_path / "api.db"))
    with TestClient(create_app()) as client:
        assert client.post("/api/skills/suggest", json={"name": "a", "rule": "r", "scope": "projects"}).status_code == 400
        assert client.post("/api/skills/suggest", json={"name": "a", "rule": "r", "scope": "weird"}).status_code == 400
        sk = client.post("/api/skills/suggest", json={"name": "a", "rule": "r", "scope": "projects",
                                                      "projects": ["pA", "pB"]}).json()
        assert sk["projects"] == ["pA", "pB"]
        assert client.patch(f"/api/skills/{sk['skill_id']}", json={"scope": "experiment"}).status_code == 400
        moved = client.patch(f"/api/skills/{sk['skill_id']}", json={"scope": "global"}).json()
        assert moved["scope"] == "global"


async def test_instructions_given_before_research_count_too(tmp_path, monkeypatch):
    engine, events = make_engine(tmp_path, monkeypatch)
    seen = []

    async def from_feedback(router, stage, *, message, context, library):
        seen.append(context)
        return []

    monkeypatch.setattr(learning, "from_feedback", from_feedback)
    await engine._on_feedback_event(Event(name="instruction_queued", data={
        "message": "always use PBE+D3 for these slabs", "phase": "planning"}))
    await engine._finish_skill_learning()
    assert seen and "planning phase" in seen[0]
