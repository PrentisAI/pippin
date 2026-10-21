"""Read-only report links, confidential projects, skill approval and the team's provider rules."""

import time

from aurora.auth.providers import model_problem, provider_of
from aurora.auth.store import auth_store
from tests.test_accounts import app, client, team, uid  # noqa: F401


def _report(cid, text="# Findings\n\nWeekend demand rises. ![fig](figures/a.png)\n"):
    """A finished run with a report and a figure."""
    from aurora.api.routes.campaigns import ExperimentRunState, _ensure_campaign
    camp = _ensure_campaign(cid)
    run = ExperimentRunState("r1", cid, "weekend demand", "m", "supervised")
    run.started_at = time.time()
    d = run.campaign_dir / "phases" / "writing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "draft_1.md").write_text(text)
    (d / "figures").mkdir(exist_ok=True)
    (d / "figures" / "a.png").write_bytes(b"\x89PNG fake")
    (run.campaign_dir / "secret.txt").write_text("not shared")
    camp.experiments["r1"] = run
    return run


def test_a_read_only_link_to_the_report(app, team):  # noqa: F811
    linda, sara = team["linda"], team["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "Citi Bike usage patterns"}).json()["campaign_id"]
    _report(cid)
    assert linda.get(f"/api/campaigns/{cid}/link").json() == {"link": None, "allowed": True, "reason": "", "has_report": True}
    assert linda.post(f"/api/campaigns/{cid}/link", json={"days": 12}).status_code == 400
    view = linda.post(f"/api/campaigns/{cid}/link", json={"days": 7}).json()
    path = view["link"]["path"]
    assert view["link"]["days"] == 7
    assert linda.post(f"/api/campaigns/{cid}/link", json={"days": 30}).json()["link"]["path"] == path   # same link, longer
    anyone = client(app)
    page = anyone.get(path)
    assert page.status_code == 200 and "Weekend demand rises" in page.text and "script-src" not in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    token = path.rsplit("/", 1)[-1]
    assert f'src="/r/{token}/files/r1/phases/writing/figures/a.png"' in page.text
    assert anyone.get(f"/r/{token}/files/r1/phases/writing/figures/a.png").status_code == 200
    assert anyone.get(f"/r/{token}/files/r1/secret.txt").status_code == 404
    for sneaky in ("phases/%2e%2e/secret.txt", "%2e%2e/%2e%2e/etc/passwd"):
        r = anyone.get(f"/r/{token}/files/r1/{sneaky}")
        assert "not shared" not in r.text and "root:" not in r.text
    # sara is not in the project: she cannot see or make its link
    assert sara.post(f"/api/campaigns/{cid}/link", json={"days": 7}).status_code == 404
    # confidential: the link stops working and none can be made
    linda.put(f"/api/campaigns/{cid}/access", json={"confidential": True})
    assert anyone.get(path).status_code == 404
    v = linda.get(f"/api/campaigns/{cid}/link").json()
    assert v["link"] is None and not v["allowed"] and "confidential" in v["reason"]
    assert linda.post(f"/api/campaigns/{cid}/link", json={"days": 7}).status_code == 403
    linda.put(f"/api/campaigns/{cid}/access", json={"confidential": False})
    path = linda.post(f"/api/campaigns/{cid}/link", json={"days": 7}).json()["link"]["path"]
    linda.delete(f"/api/campaigns/{cid}/link")
    assert anyone.get(path).status_code == 404
    # the team's rule
    team["wei"].put("/api/team/rules", json={"external_links": False})
    assert linda.post(f"/api/campaigns/{cid}/link", json={"days": 7}).status_code == 403
    # an expired link
    team["wei"].put("/api/team/rules", json={"external_links": True})
    path = linda.post(f"/api/campaigns/{cid}/link", json={"days": 7}).json()["link"]["path"]
    auth_store._x("UPDATE report_links SET expires_at=?", (time.time() - 1,))
    assert anyone.get(path).status_code == 410


def test_skills_reach_the_team_after_approval(app, team):  # noqa: F811
    linda, wei = team["linda"], team["wei"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    other = wei.post("/api/campaigns/quick", json={"prompt": "q"}).json()["campaign_id"]
    rule = "## Purpose\n\nx"
    sk = linda.post("/api/skills/", json={"name": "clean-gbfs", "rule": rule, "scope": "global", "source_campaign": cid}).json()
    assert sk["approval"] == "pending"
    from aurora.api.routes.skills import get_skill_store
    store = get_skill_store()
    assert [s.name for s in store.list_for_experiment("e1", cid)] == ["clean-gbfs"]        # where it was learned
    assert store.list_for_experiment("e2", other) == []                                     # not elsewhere yet
    assert [s["name"] for s in wei.get("/api/skills/pending").json()] == ["clean-gbfs"]
    assert linda.post(f"/api/skills/{sk['skill_id']}/approval", json={"approve": True}).status_code == 403
    assert wei.post(f"/api/skills/{sk['skill_id']}/approval", json={"approve": True}).json()["approval"] == ""
    assert [s.name for s in store.list_for_experiment("e2", other)] == ["clean-gbfs"]
    # an admin's own skill needs nobody's approval
    assert wei.post("/api/skills/", json={"name": "w", "rule": rule, "scope": "global"}).json()["approval"] == ""
    # a confidential project's skills stay in it
    linda.put(f"/api/campaigns/{cid}/access", json={"confidential": True})
    r = linda.post("/api/skills/", json={"name": "c", "rule": rule, "scope": "global", "source_campaign": cid})
    assert r.status_code == 400 and "confidential" in r.json()["detail"]
    assert linda.post("/api/skills/", json={"name": "c", "rule": rule, "scope": "campaign", "source_campaign": cid}).status_code == 201
    # without the rule, a member's skill goes straight to the team
    wei.put("/api/team/rules", json={"skills_need_approval": False})
    assert linda.post("/api/skills/", json={"name": "d", "rule": rule, "scope": "global"}).json()["approval"] == ""


def test_providers_for_confidential_projects(app, team):  # noqa: F811
    linda = team["linda"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    assert provider_of("anthropic/claude-haiku-5.5") == "anthropic" and provider_of("gpt-5") == "openai"
    assert provider_of("mock-agent") == ""
    assert model_problem("google/gemini-3.1-pro-preview", cid) == ""
    linda.put(f"/api/campaigns/{cid}/access", json={"confidential": True})
    assert "keep no data" in model_problem("google/gemini-3.1-pro-preview", cid)
    assert model_problem("anthropic/claude-sonnet-5.5", cid) == ""
    team["wei"].put("/api/team/rules", json={"allowed_providers": ["openai"]})
    assert "allow models from OpenAI only" in model_problem("anthropic/claude-sonnet-5.5", "")
    from aurora.api.routes.campaigns import _ensure_campaign
    from aurora.auth.providers import config_models
    from aurora.config import AuroraConfig
    cfg = AuroraConfig.model_validate({"objective": "x", "skip_phases": ["ideation"]})
    assert "moonshotai/kimi-k3" not in config_models(cfg)
    assert _ensure_campaign(cid)


def test_members_see_other_projects_exist_when_the_team_says_so(app, team):  # noqa: F811
    linda, sara, wei = team["linda"], team["sara"], team["wei"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "Grid batteries"}).json()["campaign_id"]
    secret = linda.post("/api/campaigns/quick", json={"prompt": "Secret"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{secret}/access", json={"confidential": True})
    assert sara.get("/api/campaigns/others").json() == []
    wei.put("/api/team/rules", json={"members_see_all_projects": True})
    others = sara.get("/api/campaigns/others").json()
    assert [o["campaign_id"] for o in others] == [cid] and others[0]["owner"] == "Linda Fei"
    assert sara.get(f"/api/campaigns/{cid}").status_code == 404           # existence only
