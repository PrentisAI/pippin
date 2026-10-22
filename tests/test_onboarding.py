"""The first minutes: the examples that come with AURORA, the welcome wizard and a person's defaults,
the getting-started checklist, help from the documentation, the demo, aurora quickstart."""

from aurora import examples
from aurora.auth.store import auth_store
from tests.test_accounts import PW, app, team  # noqa: F401
from tests.test_accounts import client as signed_in
from tests.test_api import client, wait_done  # noqa: F401

EX = "ex_citi_bike"


def test_the_example_is_there_and_read_only(client):  # noqa: F811
    projects = {p["campaign_id"]: p for p in client.get("/api/campaigns/").json()}
    assert EX in projects and projects[EX]["example"] and projects[EX]["name"] == "Citi Bike usage patterns"
    camp = client.get(f"/api/campaigns/{EX}").json()
    run = camp["experiments"][0]["run_id"]
    exp = client.get(f"/api/campaigns/{EX}/experiments/{run}").json()
    assert exp["status"] == "completed" and exp["report"] and round(exp["cost_usd"], 2) == 9.38
    # its stream and its files are where the console looks, with this console's paths
    assert any(e["event_type"] == "human_gate_resolved" for e in client.get(f"/api/campaigns/{EX}/experiments/{run}").json()["events"])
    report = client.get(f"/api/campaigns/{EX}/experiments/{run}/report")
    assert report.status_code == 200 and "Citi Bike" in report.text
    # nobody changes it, not even the console's only user
    assert client.patch(f"/api/campaigns/{EX}", json={"name": "mine"}).status_code == 403
    assert client.delete(f"/api/campaigns/{EX}").status_code == 403
    assert client.post(f"/api/campaigns/{EX}/experiments/{run}/rerun").status_code in (403, 404, 405)


def test_hide_duplicate_and_remove_examples(client):  # noqa: F811
    cards = client.get("/api/examples").json()["examples"]
    assert [c["id"] for c in cards] == ["citi-bike", "demo"] and cards[0]["installed"]
    assert "2 h 52 min" in cards[0]["facts"]
    mine = client.post("/api/examples/citi-bike/duplicate").json()
    assert mine["campaign_id"].startswith("p_") and "Citi Bike" in mine["brief"]
    assert client.patch(f"/api/campaigns/{mine['campaign_id']}", json={"name": "my bikes"}).status_code == 200
    client.put("/api/onboarding", json={"examples_hidden": True})
    assert EX not in [p["campaign_id"] for p in client.get("/api/campaigns/").json()]
    client.put("/api/onboarding", json={"examples_hidden": False})
    assert client.delete("/api/examples/citi-bike").status_code == 200
    assert EX not in [p["campaign_id"] for p in client.get("/api/campaigns/").json()]
    assert client.get("/api/examples").json()["examples"][0]["removed"]
    assert client.post("/api/examples/citi-bike/install").json()["campaign_id"] == EX
    assert EX in [p["campaign_id"] for p in client.get("/api/campaigns/").json()]


def test_the_wizard_and_the_defaults_without_accounts(client):  # noqa: F811
    s = client.get("/api/onboarding").json()
    assert s["wizard"] == {"done": False, "step": "welcome"} and s["autonomy"] == "supervised" and s["max_cost_usd"] == 20
    keys = [c["key"] for c in s["checklist"]]
    assert keys[:4] == ["key", "tour", "demo", "run"] and "gpu" in keys and "team" not in keys
    client.put("/api/onboarding", json={"step": "prefs", "field": "ml"})
    client.put("/api/onboarding/defaults", json={"autonomy": "autonomous", "max_cost_usd": 50})
    s = client.put("/api/onboarding", json={"done": True, "tour_done": "run", "tip_seen": "proposal"}).json()
    assert s["wizard"]["done"] and s["field"] == "ml" and s["autonomy"] == "autonomous" and s["max_cost_usd"] == 50
    assert s["tips_seen"] == ["proposal"] and next(c for c in s["checklist"] if c["key"] == "tour")["done"]
    # the console's defaults: what the settings and every new proposal start from
    settings = client.get("/api/settings").json()
    assert settings["autonomy_mode"] == "autonomous" and settings["max_cost_usd"] == 50
    assert client.put("/api/onboarding", json={"field": "astrology"}).status_code == 400


def test_each_new_account_has_its_own_wizard_and_defaults(app, team):  # noqa: F811
    wei, linda = team["wei"], team["linda"]
    wei.put("/api/onboarding", json={"done": True})
    linda.put("/api/onboarding/defaults", json={"autonomy": "autonomous", "max_cost_usd": 5})
    assert not linda.get("/api/onboarding").json()["wizard"]["done"]
    assert linda.get("/api/onboarding").json()["max_cost_usd"] == 5
    assert wei.get("/api/onboarding").json()["max_cost_usd"] == 20          # Linda's choice is hers
    assert "team" in [c["key"] for c in wei.get("/api/onboarding").json()["checklist"]]
    assert "team" not in [c["key"] for c in linda.get("/api/onboarding").json()["checklist"]]
    # the example: everyone sees it, nobody changes it
    from aurora.api.routes.campaigns import store
    from aurora.paths import aurora_home
    examples.install("citi-bike", home=aurora_home(), store=store)
    assert EX in [p["campaign_id"] for p in linda.get("/api/campaigns/").json()]
    assert linda.delete(f"/api/campaigns/{EX}").status_code == 403
    assert linda.delete("/api/examples/citi-bike").status_code == 403


def test_help_from_the_documentation(client):  # noqa: F811
    pages = client.get("/api/help/docs").json()["pages"]
    assert pages[0]["path"] == "index.md" and any(p["path"] == "guide/cluster-jobs.md" for p in pages)
    doc = client.get("/api/help/docs/guide/notifications.md").json()
    assert doc["title"] == "Notifications" and "Quiet hours" in doc["markdown"]
    assert client.get("/api/help/docs/../pyproject.toml").status_code == 404
    assert client.get("/api/help/assets/architecture.png").status_code == 200
    r = client.post("/api/help/ask", json={"question": "how do I answer a decision from my phone?"}).json()
    assert r["sources"][0]["page"] == "Notifications" and r["answer"] == ""      # no key here: the sections


def test_the_demo_goes_into_one_project(client):  # noqa: F811
    first = client.post("/api/demo").json()
    wait_done(client, first["campaign_id"], first["experiment_id"])
    second = client.post("/api/demo").json()
    wait_done(client, second["campaign_id"], second["experiment_id"])
    assert first["campaign_id"] == second["campaign_id"]
    assert next(c for c in client.get("/api/onboarding").json()["checklist"] if c["key"] == "demo")["done"]
    assert auth_store.get_pref("local", "demo_project") == first["campaign_id"]


def test_quickstart_without_a_terminal(monkeypatch, tmp_path, capsys):
    from aurora import quickstart
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(quickstart, "check_machine", lambda assume: True)
    assert quickstart.main(assume=False, serve=False) == 0
    out = capsys.readouterr().out
    assert "[2/4] Model key" in out and "no key" in out and "ideation · screening" in out


def test_a_member_signed_in_has_the_checklist(app, team):  # noqa: F811
    keys = [c["key"] for c in signed_in(app, "sara@lab.example", PW).get("/api/onboarding").json()["checklist"]]
    assert "gpu" not in keys and "team" not in keys and keys[0] == "key"


def test_the_example_does_not_meet_the_run_it_was_made_from(client):  # noqa: F811
    """A console that already has the original run (same run ID) still gets the example, with its own run."""
    from aurora.api.routes.campaigns import _campaigns, store
    from aurora.paths import aurora_home
    original = "20261019-025018_citi-bike-usage-patterns"
    store.save_campaign("p_mine", "my bikes", "", 1.0, 1.0)
    store.save_experiment(original, "p_mine", "my run", "m", status="completed", started_at=1.0)
    store.delete_campaign(EX)
    _campaigns.pop(EX, None)
    assert examples.install("citi-bike", home=aurora_home(), store=store) == EX
    _campaigns.pop(EX, None)
    runs = client.get(f"/api/campaigns/{EX}").json()["experiments"]
    assert [r["run_id"] for r in runs] == ["ex-citi-bike"]
    assert store.get_experiment(original)["campaign_id"] == "p_mine"          # theirs is untouched
