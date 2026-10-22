"""Estimating a run before it starts, and saying so when it goes over."""

import asyncio

from fastapi.testclient import TestClient

from aurora.core import estimate as est


def test_typical_phases_at_the_chosen_models_prices():
    prices = {"anthropic/claude-sonnet-5.5": 6.0, "anthropic/claude-opus-4.8": 30.0, "deepseek/deepseek-v4-pro": 0.9}
    assert est.price_factor("anthropic/claude-opus-4.8", prices) == 5.0
    assert est.price_factor("some/unknown-haiku-x", None) == 0.3 and est.price_factor("mock-agent", None) == 0.0
    sonnet = est.estimate({"agent": "anthropic/claude-sonnet-5.5"}, ["ideation", "screening"], prices=prices)
    opus = est.estimate({"agent": "anthropic/claude-sonnet-5.5", "research": "anthropic/claude-opus-4.8"},
                        ["ideation", "screening"], prices=prices)
    assert sonnet["cost"][0] < sonnet["cost"][1] and opus["cost"][1] > 3 * sonnet["cost"][1] * 0.6
    assert [p["phase"] for p in sonnet["per_phase"]] == ["planning", "preflight", "research", "analysis", "writing"]
    full = est.estimate({}, [], ideation_mode="roundtable", prices=prices)
    cheap = est.estimate({}, [], ideation_mode="independent", prices=prices)
    assert full["per_phase"][0]["phase"] == "ideation" and cheap["per_phase"][0]["cost"][1] < full["per_phase"][0]["cost"][1]
    two = est.estimate({}, ["ideation"], parallel=2, prices=prices)
    one = est.estimate({}, ["ideation"], parallel=1, prices=prices)
    assert two["hours"][1] < one["hours"][1] and "two to five research jobs" in one["basis"]


def test_earlier_runs_with_the_same_setup_decide():
    hist = [{"cost_usd": c, "hours": h, "skip_phases": ["ideation"], "research_model": "anthropic/claude-sonnet-5.5"}
            for c, h in ((2.0, 1.0), (3.0, 1.5), (4.0, 2.0), (5.0, 2.5), (9.0, 6.0))]
    e = est.estimate({"agent": "anthropic/claude-sonnet-5.5"}, ["ideation"], history=hist)
    assert e["similar"] == 5 and "5 earlier runs" in e["basis"] and e["cost"] == [2.5, 7.0]
    other = est.estimate({"agent": "anthropic/claude-sonnet-5.5"}, [], history=hist)
    assert other["similar"] == 0 and "typical" in other["basis"]
    few = est.estimate({"agent": "anthropic/claude-sonnet-5.5"}, ["ideation"], history=hist[:2])
    assert "2 earlier runs with these settings cost $2.00, $3.00" in few["basis"]
    assert est.show({"cost": [2.5, 7.0], "hours": [1.2, 3]}) == "$2.5–$7.0, 1.2–3 h"


def test_the_estimate_endpoint(monkeypatch):
    monkeypatch.setattr(est, "model_prices", lambda *a, **k: None)
    from aurora.api.app import create_app
    c = TestClient(create_app())
    cid = c.post("/api/campaigns/quick", json={"prompt": "x"}).json()["campaign_id"]
    r = c.post(f"/api/campaigns/{cid}/estimate", json={"skip_phases": ["ideation", "screening"],
                                                       "models": {"research": "anthropic/claude-opus-4.8"}}).json()
    assert r["cost"][1] > r["cost"][0] > 0 and r["hours"][1] > 0 and r["per_phase"][2]["phase"] == "research"


async def test_a_run_says_when_it_goes_over(tmp_path):
    from aurora.config import AuroraConfig
    from aurora.core.engine import ResearchEngine
    eng = ResearchEngine(AuroraConfig(objective="x", campaign_id="c", workspace={"root": str(tmp_path)},
                                      mock_mode=True, budget={"estimate_usd": [1.0, 2.0], "estimate_hours": [0.5, 1.0]}))
    eng._setup()
    seen = []

    async def publish(name, **data):
        seen.append((name, data))
    eng._publish = publish
    import time
    eng._started_at = time.time()
    eng.cost_usd = 1.5
    await eng._warn_estimate()
    assert seen == []
    eng.cost_usd = 2.5
    await eng._warn_estimate()
    await eng._warn_estimate()
    assert [(n, d["what"], d["level"]) for n, d in seen] == [("estimate_exceeded", "cost", 1.0)]
    eng.cost_usd = 4.5
    eng._started_at = time.time() - 1.5 * 3600
    await eng._warn_estimate()
    assert {(d["what"], d["level"]) for _, d in seen[1:]} == {("cost", 2.0), ("time", 1.0)}
    assert "twice" in [d for _, d in seen if d["level"] == 2.0][0]["detail"]
    from aurora.core.events import Event
    from aurora.notify import describe
    kind, title, text = describe(Event(name="estimate_exceeded", data=seen[0][1], source="x"), "run")
    assert kind == "estimate_exceeded" and "Over the estimate" in title
    assert asyncio.iscoroutinefunction(eng._warn_estimate)
