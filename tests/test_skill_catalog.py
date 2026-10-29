"""The skill catalog: skills others have shared, listed with their source and licence, added by choice as whole
folders (pinned to a commit, licence beside them), written where the agents find them, and removed again."""

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.skills import catalog
from aurora.skills.materializer import SkillMaterializer, description_of
from aurora.skills.store import SkillStore

SRC = catalog.SOURCES["k-dense"]
SKILL_MD = """---
name: statistical-power
description: >-
  Calculates sample sizes and statistical power
  for study planning.
license: MIT
---

# Statistical power
"""


def github(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith(f"https://api.github.com/repos/{SRC.repo}/git/trees/{SRC.commit}"):
        return httpx.Response(200, json={"tree": [
            {"path": "skills/statistical-power/SKILL.md", "type": "blob", "size": 200},
            {"path": "skills/statistical-power/scripts/power.py", "type": "blob", "size": 30},
            {"path": "skills/statistical-power", "type": "tree"},
            {"path": "skills/statistical-powerful/SKILL.md", "type": "blob", "size": 10},   # not this one
            {"path": "LICENSE.md", "type": "blob", "size": 20}]})
    raw = f"https://raw.githubusercontent.com/{SRC.repo}/{SRC.commit}/"
    files = {"skills/statistical-power/SKILL.md": SKILL_MD, "skills/statistical-power/scripts/power.py": "print(1)\n",
             "LICENSE.md": "MIT License\n"}
    if url.startswith(raw) and url[len(raw):] in files:
        return httpx.Response(200, text=files[url[len(raw):]])
    return httpx.Response(404)


@pytest.fixture()
def fake_github(monkeypatch):
    monkeypatch.setattr(catalog, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(github)))


def test_every_entry_names_a_source_and_category():
    cats = {c for c, _ in catalog.CATEGORIES}
    names = [e.name for e in catalog.ENTRIES]
    assert len(names) == len(set(names))
    for e in catalog.ENTRIES:
        assert e.source in catalog.SOURCES and e.category in cats and e.summary and e.path.endswith(e.name)
    assert any(e.recommended for e in catalog.ENTRIES)


async def test_added_as_a_whole_folder_with_its_licence(tmp_path, fake_github):
    store = SkillStore(tmp_path / "s.db")
    sk = await catalog.install("statistical-power", store)
    assert sk.origin == "catalog" and sk.scope == "global" and sk.source["license"] == "MIT"
    assert sk.source["commit"] == SRC.commit and sk.rule.startswith("---\nname: statistical-power")
    bundle = catalog.bundle_of(store.get(sk.skill_id))
    assert sorted(p.name for p in bundle.iterdir()) == ["LICENSE", "SKILL.md", "SOURCE.md", "scripts"]
    assert SRC.repo in (bundle / "SOURCE.md").read_text()
    # adding it again changes nothing; the listing knows it is in the library
    assert (await catalog.install("statistical-power", store)).skill_id == sk.skill_id
    entry = next(e for e in catalog.listing(store)["entries"] if e["name"] == "statistical-power")
    assert entry["skill_id"] == sk.skill_id and entry["license"] == "MIT" and SRC.commit in entry["url"]
    # the agents get the whole folder; its folded description is read whole
    ws = tmp_path / "ws"
    m = SkillMaterializer(store, ws)
    m.materialize_for_experiment("e1")
    there = ws / ".claude" / "skills" / "statistical-power"
    assert (there / "scripts" / "power.py").is_file() and (there / "LICENSE").is_file()
    assert description_of((there / "SKILL.md").read_text()) == "Calculates sample sizes and statistical power for study planning."
    m.cleanup()
    assert not there.exists()


async def test_a_name_already_in_the_library_is_not_overwritten(tmp_path, fake_github):
    from aurora.skills.store import DistilledSkill
    store = SkillStore(tmp_path / "s.db")
    store.create(DistilledSkill(name="statistical-power", rule="mine", scope="global"))
    with pytest.raises(catalog.CatalogError, match="already has a skill"):
        await catalog.install("statistical-power", store)
    with pytest.raises(catalog.CatalogError, match="No skill"):
        await catalog.install("not-in-the-catalog", store)


def test_the_api_adds_and_removes_and_keeps_it_unedited(tmp_path, monkeypatch, fake_github):
    from aurora.api.app import create_app
    from aurora.api.routes import skills as skills_routes
    monkeypatch.setattr(skills_routes, "_store", SkillStore(tmp_path / "api.db"))
    with TestClient(create_app()) as client:
        cat = client.get("/api/skills/catalog").json()
        assert {c["id"] for c in cat["categories"]} >= {"thinking", "stats"} and cat["sources"][0]["license"]
        sk = client.post("/api/skills/catalog/statistical-power", json={}).json()
        assert sk["origin"] == "catalog"
        assert client.post(f"/api/skills/{sk['skill_id']}/revise", json={"rule": "changed"}).status_code == 400
        assert client.post("/api/skills/catalog/statistical-power", json={"scope": "campaign"}).status_code == 400
        bundle = catalog.bundle_of(skills_routes._store.get(sk["skill_id"]))
        assert client.delete(f"/api/skills/{sk['skill_id']}").status_code == 204
        assert not bundle.exists()
        assert not any(e["skill_id"] for e in client.get("/api/skills/catalog").json()["entries"])
