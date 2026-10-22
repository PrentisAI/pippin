"""Citations as the console shows them, screening's closest prior work, and seed papers."""

import asyncio

from fastapi.testclient import TestClient

from aurora.literature import indexes as indexes_mod
from aurora.literature.check import parse_ref_tag
from aurora.literature.library import library
from aurora.literature.service import ProjectLiterature
from aurora.models.roundtable import IdeaScore
from tests.test_literature import FakeIndexes


def _client(monkeypatch):
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    from aurora.api.app import create_app
    return TestClient(create_app())


def test_what_the_citations_in_a_text_point_to(monkeypatch):
    c = _client(monkeypatch)
    cid = c.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    lit = ProjectLiterature(cid)
    text = ("Steps matter [Behrens12]. A meta-analysis says so "
            "[ref: The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts; Smith; 2024]. And [Wang25].")
    asyncio.run(lit.search("methanol synthesis", by="Seat A"))
    cites = asyncio.run(lit.check_text(text, by="Seat A", place="Ideation"))
    assert cites.verified == ["Behrens12"] and cites.unknown_keys == ["Wang25"] and len(cites.flagged) == 1
    body = "The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts; Smith; 2024"
    r = c.post(f"/api/campaigns/{cid}/literature/resolve", json={"keys": ["Behrens12", "Wang25"], "refs": [body]}).json()
    b = r["keys"]["Behrens12"]
    assert b["status"] == "verified" and b["year"] == 2012 and "Ideation" in b["used_in"] and b["in_project"]
    assert r["keys"]["Wang25"]["status"] == "unknown"
    smith = r["refs"][body]
    assert smith["status"] == "not_found" and smith["ref_id"] and smith["closest"]["status"] == "verified"
    # "Use the real one instead"
    c.post(f"/api/campaigns/{cid}/literature/refs/{smith['ref_id']}", json={"action": "replace"})
    smith = c.post(f"/api/campaigns/{cid}/literature/resolve", json={"refs": [body]}).json()["refs"][body]
    assert smith["resolution"] == "replaced" and smith["replaced_with"]["key"] == smith["closest"]["key"]
    assert parse_ref_tag(body).title.startswith("The active site")


def test_screening_names_its_closest_prior_work(monkeypatch, tmp_path):
    from aurora.config import AuroraConfig
    from aurora.core.engine import ResearchEngine
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    eng = ResearchEngine(AuroraConfig(objective="x", campaign_id="c", workspace={"root": str(tmp_path)},
                                      sandbox={"docker": False}))
    eng._setup()
    asyncio.run(eng.literature.search("methanol synthesis", by="Screening"))
    score = IdeaScore(novelty=6.5, closest=[{"key": "Kuld16", "differs": "Per station, not per city."},
                                            {"key": "Made99", "differs": "an invented paper"}])
    closest = eng._closest_work(score)
    assert [c["key"] for c in closest] == ["Kuld16"] and closest[0]["differs"] == "Per station, not per city."
    assert closest[0]["year"] == 2016 and closest[0]["mine"] is False
    assert any(u["place"] == "Screening" for u in library.uses(eng.literature.campaign_id))


def test_a_run_can_start_from_seed_papers(monkeypatch):
    c = _client(monkeypatch)
    cid = c.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    asyncio.run(ProjectLiterature(cid).search("methanol", by="Me"))
    from aurora.api.routes.campaigns import _seed_block, _seed_keys
    assert _seed_keys(cid, ["[Behrens12]", "Nope11", "Behrens12"]) == ["Behrens12"]
    block = _seed_block(cid, ["Behrens12"])
    assert "## Seed papers" in block and "[Behrens12]" in block
    assert any(u["place"] == "seed" for u in library.uses(cid))
    assert library.has_item(cid, library.paper("Behrens12")["paper_id"])
