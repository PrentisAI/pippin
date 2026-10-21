"""The rest of the brainstorm: discussions on ideas, links, moving, deep search, what's new, the live meeting;
and the literature's PDFs, RIS files, pasted IDs and newer arXiv versions."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm import background as bg
from aurora.brainstorm.live import LiveMeeting
from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from aurora.literature.bibtex import parse_ris
from aurora.literature.library import library
from aurora.literature.records import Paper
from tests.test_brainstorm import FakeModel, _reply, wait
from tests.test_literature import BEHRENS, KULD, VASWANI, FakeIndexes


@pytest.fixture()
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    return TestClient(create_app())


def project(c, prompt="Methanol on Cu"):
    return c.post("/api/campaigns/quick", json={"prompt": prompt}).json()["campaign_id"]


def wait_thread(c, card_id, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/cards/{card_id}/thread").json()
        if not r["pending"] and any(m["agent"] for m in r["messages"]):
            return r
        time.sleep(0.05)
    raise AssertionError("no answer")


def test_an_idea_has_its_own_discussion_and_links(client, model):
    cid = project(client)
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at Cu steps."}).json()["card"]
    other = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Interface sites."}).json()["card"]
    client.post(f"/api/brainstorm/cards/{idea['card_id']}/thread", json={"text": "@Skeptic what is weak here?"})
    th = wait_thread(client, idea["card_id"])
    assert [m["who"] for m in th["messages"]] == ["local", "Skeptic"]
    assert client.get(f"/api/brainstorm/spaces/{cid}").json()["threads"][idea["card_id"]] == 2
    assert client.post(f"/api/brainstorm/cards/{idea['card_id']}/links", json={"to": other["card_id"], "rel": "challenges"}).status_code == 200
    assert {"from_card": idea["card_id"], "to_card": other["card_id"], "rel": "challenges"} in \
        client.get(f"/api/brainstorm/cards/{idea['card_id']}/thread").json()["links"]
    client.delete(f"/api/brainstorm/cards/{idea['card_id']}/links/{other['card_id']}")
    assert client.get(f"/api/brainstorm/cards/{idea['card_id']}/thread").json()["links"] == [
        {"from_card": q["card_id"], "to_card": idea["card_id"], "rel": "asks about"}
        for q in client.get(f"/api/brainstorm/spaces/{cid}").json()["cards"] if q["type"] == "question"]


def test_an_idea_moves_from_your_space_to_a_project(client, model):
    cid = project(client)
    gid = client.post("/api/brainstorm/spaces/global/cards", json={"text": "Battery fade for e-bikes."}).json()["card"]
    bs_store.link(bs_store.add_card("user:local", "local", "question", "Is it the same chemistry?")["card_id"], gid["card_id"], "asks about")
    r = client.post(f"/api/brainstorm/cards/{gid['card_id']}/move", json={"campaign_id": cid}).json()
    assert r == {"campaign_id": cid, "moved": 2}
    assert {c["text"] for c in client.get(f"/api/brainstorm/spaces/{cid}").json()["cards"]} == {
        "Battery fade for e-bikes.", "Is it the same chemistry?"}
    # or a new project started from it
    g2 = client.post("/api/brainstorm/spaces/global/cards", json={"text": "Do e-bikes replace buses?"}).json()["card"]
    new = client.post(f"/api/brainstorm/cards/{g2['card_id']}/move", json={"new_project": "E-bikes and buses"}).json()
    assert client.get(f"/api/campaigns/{new['campaign_id']}").json()["name"] == "E-bikes and buses"


def test_deep_search_and_attachments(client, model):
    cid = project(client)
    chat = client.post(f"/api/brainstorm/spaces/{cid}/chats", json={}).json()
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at Cu steps."}).json()["card"]
    client.post(f"/api/brainstorm/chats/{chat['chat_id']}/deep", json={"focus": idea["card_id"]})
    done = wait(client, chat["chat_id"])
    assert done["messages"][0]["text"].startswith("@Librarian deep search: Zn at Cu steps.")
    system = model.calls[0][0]["content"]
    assert "DEEP SEARCH" in system
    # a paper attached to a message reaches the agent, with its abstract
    pid = library.upsert_paper(Paper.from_dict({**KULD.to_dict(), "abstract": "ZnO promotes Cu fifteen-fold."}))["paper_id"]
    client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages",
                json={"text": "What does this one say?", "attachments": [{"kind": "paper", "paper_id": pid}]})
    time.sleep(0.5)
    wait(client, chat["chat_id"])
    sent = [m for m in model.calls[-1] if str(m.get("content", "")).startswith("What ") and "attached:" in m["content"]]
    assert sent and "ZnO promotes Cu fifteen-fold." in sent[0]["content"]


async def test_whats_new_and_the_agents_own_work(client, model, monkeypatch):
    cid = project(client)
    idea = bs_store.add_card(cid, "local", "idea", "Zn at Cu steps.")
    q = bs_store.add_card(cid, "local", "question", "Steps or interface?")
    bs_store.link(q["card_id"], idea["card_id"], "asks about")
    library.add_item(cid, library.upsert_paper(BEHRENS)["paper_id"], source="zotero")
    # an old unanswered question is brought up
    bs_store._store._x("UPDATE bs_cards SET created_at=? WHERE card_id=?", (time.time() - 3 * 86400, q["card_id"]))
    cards = bs_store.cards(cid)
    assert bg._waiting_questions(cid, "local", cards, bs_store.links({c["card_id"] for c in cards})) == 1
    assert bg._waiting_questions(cid, "local", cards, bs_store.links({c["card_id"] for c in cards})) == 0     # once

    async def suggest_create(**kw):
        return _reply(json.dumps({"suggestions": [
            {"by": "Librarian", "kind": "evidence", "short": "Behrens12 supports the step idea.", "act": "Add it",
             "cards": [idea["card_id"]], "paper_key": "Behrens12", "rel": "supports"},
            {"by": "Methods", "kind": "combine", "short": "made-up", "act": "x", "cards": ["c_nope"]}]}))
    model.chat.completions.create = suggest_create
    from aurora.api.routes import brainstorm as routes
    n = await bg.suggest(cid, "local", cards, [], routes._literature(cid, cid), ag.ModelAccess("k", "u", "m"))
    assert n == 1
    space = client.get(f"/api/brainstorm/spaces/{cid}").json()
    assert {s["kind"] for s in space["suggestions"]} == {"open", "evidence"}
    ev = next(s for s in space["suggestions"] if s["kind"] == "evidence")
    r = client.post(f"/api/brainstorm/suggestions/{ev['sug_id']}/do").json()
    assert r["added"] and bs_store.card(r["added"])["paper_id"] == library.paper("Behrens12")["paper_id"]
    opened = next(s for s in space["suggestions"] if s["kind"] == "open")
    client.post(f"/api/brainstorm/suggestions/{opened['sug_id']}/dismiss")
    assert client.get(f"/api/brainstorm/spaces/{cid}").json()["suggestions"] == []
    # 'Very': the agents work on ideas on their own (in the idea's discussion), within a daily allowance
    model.chat.completions.create = FakeModel().create
    lone = bs_store.add_card(cid, "local", "idea", "Interface sites dominate.")
    worked = await bg.keep_working(cid, "local", lambda th: routes._board(th, cid), ag.ModelAccess("k", "u", "m"), limit=1)
    assert worked == 1 and bs_store.messages(f"card:{lone['card_id']}")[-1]["who"] == "Librarian"
    assert bg._budget("local", "moderate", spend=4) and not bg._budget("local", "moderate", spend=1)


async def test_a_live_round_table_in_the_brainstorm(client, model):
    cid = project(client)
    meeting = LiveMeeting(cid, "run1", "Run 1", "local")
    await meeting.on_event("phase_started", {"phase": "ideation"})
    await meeting.on_event("ideation_stream", {"seat": "p01", "kind": "presentation", "item": "", "round": 0, "text": "Zn at", "model": "m/a"})
    await meeting.on_event("ideation_turn", {"seat": "p01", "kind": "presentation", "item": "", "round": 0, "model": "m/a",
                                             "content": "Zn at steps is the site.", "headline": "Zn at steps is the site"})
    await meeting.on_event("ideation_progress", {"step": "agenda_item", "item": 1, "of": 2, "presenter": "p01"})
    await meeting.on_event("ideation_turn", {"seat": "p02", "kind": "remarks", "item": "p01", "round": 1, "model": "m/b",
                                             "content": "[QUESTION] Is it weather? More.\n\n[SUGGESTION] Split the stations by type. Then.\n\nHAND: no"})
    await meeting.on_event("ideation_progress", {"step": "decided", "chosen": "p01", "headline": "Zn at steps", "rationale": "best"})
    space = client.get(f"/api/brainstorm/spaces/{cid}").json()
    (chat,) = space["chats"]
    assert chat["vis"] == "project" and chat["run_id"] == "run1" and chat["status"] == "decided"
    cards = {c["text"]: c for c in space["cards"]}
    idea = cards["Zn at steps is the site"]
    assert idea["live"] and idea["by"] == "p01" and idea["stand"].startswith("Chosen by the chair")
    assert cards["Is it weather?"]["type"] == "question" and cards["Split the stations by type."]["type"] == "idea"
    msgs = client.get(f"/api/brainstorm/chats/{chat['chat_id']}").json()["messages"]
    seat = next(m for m in msgs if m["who"] == "p01")
    assert seat["text"] == "Zn at steps is the site." and not seat["pending"] and seat["steps"] == ["m/a"]
    # the researcher speaks: the meeting hears it before the next turn (only while it meets)
    assert client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages", json={"text": "Consider weather."}).status_code == 409
    from aurora.agents.roundtable.roundtable import Roundtable
    from aurora.config import AuroraConfig
    from aurora.core.engine import ResearchEngine
    cfg = AuroraConfig(objective="x", campaign_id="run1", ideation={"live": True})
    engine = ResearchEngine(cfg)
    engine._roundtable = Roundtable(cfg, router=None)
    from aurora.api.routes.campaigns import ExperimentRunState, _ensure_campaign
    run = ExperimentRunState("run1", cid, "Run 1", "", "supervised")
    run.engine = engine
    _ensure_campaign(cid).experiments["run1"] = run
    assert client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages", json={"text": "Consider weather."}).status_code == 200
    engine._roundtable.give_floor()
    assert "### Researcher (the researcher) — The researcher speaks\n\nConsider weather." in engine._roundtable.transcript()


def test_remark_tags_as_models_write_them():
    from aurora.brainstorm.live import _TAG
    assert [(_TAG.match(t).group(1).upper(), _TAG.match(t).group(2)) for t in
            ["**[CONCERN]** It may not hold.", "- [QUESTION]: Why?", "[BUILD-ON] Add a control."]] == [
        ("CONCERN", "It may not hold."), ("QUESTION", "Why?"), ("BUILD-ON", "Add a control.")]


def test_promote_with_the_live_round_table(client, model):
    cid = project(client)
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at Cu steps."}).json()["card"]
    client.post(f"/api/brainstorm/cards/{idea['card_id']}/promote", json={"ideation": "brainstorm"})
    prop = [m for m in client.get(f"/api/campaigns/{cid}/chat/history").json() if m.get("proposal")][0]["proposal"]
    assert prop["params"]["ideation_live"] is True and prop["skip_phases"] == []
    from aurora.api.routes.campaigns import StartExperimentRequest, _ensure_campaign, _experiment_config
    cfg = _experiment_config(_ensure_campaign(cid), "r9", StartExperimentRequest(instruction="x", ideation_live=True))
    assert cfg.ideation.live and cfg.ideation.mode == "roundtable"


# -- the literature's additions ----------------------------------------------------------------

def _pdf(lines):
    """A one-page PDF with these lines of text."""
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({x}) Tj 0 -16 Td" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode()


def test_uploaded_pdfs_are_recognised(client):
    cid = project(client)
    L = f"/api/campaigns/{cid}/literature"
    r = client.post(L + "/upload?name=behrens.pdf", content=_pdf([BEHRENS.title, "Malte Behrens", f"doi: {BEHRENS.doi}"])).json()
    assert r["added"] and r["key"] == "Behrens12" and r["file"] == "papers/Behrens12.pdf"
    by_title = client.post(L + "/upload?name=x.pdf", content=_pdf([KULD.title, "Stine Kuld", "Abstract", "We quantify."])).json()
    assert by_title["added"] and by_title["key"] == "Kuld16"
    own = client.post(L + "/upload?name=my_thesis.pdf", content=_pdf(["Zinc on copper: my thesis", "Abstract"])).json()
    assert not own["added"] and own["file"] == "papers/my_thesis.pdf"
    lit = client.get(L).json()
    assert {p["key"]: p["source"] for p in lit["papers"]} == {"Behrens12": "upload", "Kuld16": "upload"}
    assert [r["resolution"] for r in lit["refs"]] == ["have"]
    assert client.get(f"/api/campaigns/{cid}/files/raw?path=papers/Behrens12.pdf").content.startswith(b"%PDF")
    assert client.post(L + "/upload", content=b"not a pdf").status_code == 400


def test_ris_pasted_ids_the_projects_rule_and_newer_versions(client):
    cid = project(client)
    L = f"/api/campaigns/{cid}/literature"
    ris = (f"TY  - JOUR\nTI  - {KULD.title}\nAU  - Kuld, Stine\nPY  - 2016\nDO  - {KULD.doi}\nER  - \n"
           "TY  - JOUR\nTI  - A paper nobody wrote\nAU  - Nobody\nPY  - 2020\nER  - \n")
    assert [(k, r.title[:10]) for k, r in parse_ris(ris)] == [("ris1", KULD.title[:10]), ("ris2", "A paper no")]
    r = client.post(L + "/import", json={"bibtex": ris}).json()
    assert [e["status"] for e in r["entries"]] == ["verified", "not_found"]
    r = client.post(L + "/papers/many", json={"text": f"{BEHRENS.doi}\nhttps://arxiv.org/abs/1706.03762v1, junk"}).json()
    assert [e["key"] for e in r["entries"]] == ["Behrens12", "Vaswani17"]
    # the project's own rule for papers the agents find
    assert client.get(L).json()["rule"] == "cited"
    assert client.put(L + "/rule", json={"add_found": "all"}).json()["rule"] == "all"
    from aurora.api.routes.campaigns import StartExperimentRequest, _ensure_campaign, _experiment_config
    assert _experiment_config(_ensure_campaign(cid), "r1", StartExperimentRequest(instruction="x")).literature.add_found == "all"
    # v1 was cited; arXiv has v7
    library.upsert_paper(Paper.from_dict({**VASWANI.to_dict(), "arxiv_version": 7, "arxiv_comment": "v7: typos fixed"}))
    vas = next(p for p in client.get(L).json()["papers"] if p["key"] == "Vaswani17")
    assert vas["newer_version"] and vas["cited_version"] == 1 and vas["arxiv_version"] == 7
    vas = next(p for p in client.put(L + f"/papers/{vas['paper_id']}/version", json={"version": 0}).json()["papers"] if p["key"] == "Vaswani17")
    assert not vas["newer_version"] and vas["cited_version"] == 7
