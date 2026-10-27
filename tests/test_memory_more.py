"""More of the memory around the researcher: corrections that stick, answers that join the plan, claims as brainstorm
evidence, a paper told from a PDF reader's file name, quick thoughts, decisions that interrupt, a manuscript checked
from its PDF, quiet citation hints, overlaps with a manuscript, and acting on the day's digest."""

import json
import time

import pytest

from aurora.literature.library import library
from aurora.literature.records import Paper
from aurora.memory import alerts, surface, voice
from aurora.memory.store import mem_store
from tests.test_brainstorm import _reply
from tests.test_memory import EN, ZH, save
from tests.test_memory import client as client  # noqa: F401  (the fixture)
from tests.test_memory_entry_points import EntryModel


class MoreModel(EntryModel):
    def __init__(self):
        super().__init__()
        self.prompts = {}

    async def create(self, *, model, messages, **kw):
        prompt = messages[-1]["content"]
        task = prompt.split("\n", 1)[0].removeprefix("TASK: ")
        self.prompts[task] = prompt
        if task == "memory_voice_compare":
            self.tasks.append(task)
            return _reply(json.dumps({"speech": "One measures latency, the other throughput.",
                                      "answer": "[0] is about batch 1 latency; [1] about large batches."}))
        if task == "memory_overlap":
            self.tasks.append(task)
            return _reply(json.dumps({"overlap": "high", "why": "It reports the same speedup claim."}))
        if task == "memory_split_references":
            self.tasks.append(task)
            return _reply(json.dumps({"entries": ["Lovelace, A. (2024). Sparse attention at scale. NeurIPS.",
                                                  "Babbage, C. (2023). Engines of difference. Journal of Machines."]}))
        return await super().create(model=model, messages=messages, **kw)


@pytest.fixture()
def model(monkeypatch):
    from aurora.brainstorm import agents as ag
    from aurora.review import llm
    fake = MoreModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "RETRY_WAIT", 0)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    for d in (surface._contexts, surface._history, surface._inferred, voice._digests, voice._undo, voice._pending,
              voice._turns):
        d.clear()
    return fake


def _paper(title="Sparse attention at scale", doi="10.1/xyz", abstract=""):
    return library.upsert_paper(Paper(title=title, authors=["Ada Lovelace"], year=2024, doi=doi, abstract=abstract))


def test_a_split_is_remembered_when_aligning_later(client, model):
    s1 = save(client, url="https://www.zhihu.com/question/1/answer/2", text=ZH)
    save(client, url="https://x.com/someone/status/1", text=EN)
    uid = s1["result"]["units"][0]
    ev = [e for e in client.get(f"/api/memory/units/{uid}").json()["evidence"] if "x.com" in e["url"]][0]
    new = client.post(f"/api/memory/evidence/{ev['evidence_id']}/split").json()
    save(client, url="https://x.com/someone/status/9", text=EN + " Again.")
    align = model.prompts["memory_align"]
    assert "the researcher said this is NOT the same claim as" in align
    assert any(m["kind"] == "split_from" for m in mem_store.marks([new["unit_id"]]))


def test_an_answer_to_ask_me_joins_the_research_plan(client, model):
    from aurora.brainstorm.store import bs_store
    chat = bs_store.new_chat("user:local", "local", "Sparse attention")
    q = mem_store.add_question("local", "Does sparse attention save compute for single requests?", origin="mentor",
                               ref=chat["chat_id"])
    save(client, url="https://www.zhihu.com/question/1/answer/2", text=ZH)
    a = client.post("/api/memory/ask", json={"ctx": f"q:{q['question_id']}"}).json()
    client.post(f"/api/memory/asks/{a['ask_id']}/answer", json={"text": "Mine is batch 1, so I lean to it vanishing."})
    (ins,) = bs_store.insights(chat["chat_id"])
    assert ins["section"] == "evidence" and ins["quote"].startswith("Mine is batch 1") and ins["origin"] == "you"


def test_a_claim_goes_onto_a_brainstorm_board_and_agents_can_search_the_memory(client, model):
    import asyncio

    from aurora.brainstorm import agents as ag
    from aurora.brainstorm.store import bs_store
    s = save(client, url="https://x.com/someone/status/1", text=EN)
    uid = s["result"]["units"][0]
    idea = bs_store.add_card("user:local", "local", "idea", "Sparse attention for chat serving")
    card = client.post(f"/api/memory/units/{uid}/to-brainstorm", json={"card_id": idea["card_id"], "rel": "challenges"}).json()
    assert card["unit_id"] == uid and card["source"].startswith("From your memory") and card["type"] == "material"
    assert any(lk["rel"] == "challenges" for lk in bs_store.links({card["card_id"], idea["card_id"]}))
    found = asyncio.run(ag.search_memory("local", "sparse attention speedup at batch 1"))
    assert uid in found
    other = client.post(f"/api/memory/units/{uid}/to-brainstorm", json={"space": "user:someone-else"})
    assert other.status_code == 404


def test_a_paper_in_preview_is_told_from_its_file_name(client, model):
    p = _paper()
    library.add_item("", p["paper_id"], source="person", owner="local")
    ctx = client.post("/api/memory/context", json={"app": "Preview",
                                                   "title": "Lovelace - 2024 - Sparse attention at scale.pdf – Page 3 of 12"}).json()
    assert ctx["paper_id"] == p["paper_id"] and ctx["page"] == "3" and ctx["title"] == "Sparse attention at scale"
    other = client.post("/api/memory/context", json={"app": "Preview", "title": "groceries.pdf"}).json()
    assert other["paper_id"] == ""


def test_a_quick_thought_is_kept_as_your_note_about_what_is_in_front(client, model):
    client.post("/api/memory/context", json={"app": "Chrome", "title": "A thread on sparse attention"})
    t = client.post("/api/memory/thought", json={"text": "batch size matters more than the kernel"}).json()
    assert t["intent"] == "note" and t["undo_id"]
    s = mem_store.sources("local", 1)[0]
    assert s["platform"] == "note" and "A thread on sparse attention" in s["title"]


def test_an_experiment_waiting_for_you_interrupts_until_decided(client):
    alerts.decision_opened("local", "g1", "Citi Bike", "The live auditor escalated", "http://x/campaigns/p/experiments/r")
    for i in range(3):                                   # the daily quota of overlaps does not hold decisions back
        alerts.raise_alert("local", "overlap", f"p{i}", "…", "…")
    alerts.decision_opened("local", "g2", "Citi Bike", "Another one", "")
    now = {a["key"]: a for a in client.get("/api/memory/alerts?unseen=true").json() if a["kind"] == "decision"}
    assert now["g1"]["tier"] == "now" and now["g2"]["tier"] == "now" and now["g1"]["ref"]["url"].startswith("http://x/")
    alerts.decision_closed("local", "g1")
    left = [a["key"] for a in client.get("/api/memory/alerts?unseen=true").json() if a["kind"] == "decision"]
    assert left == ["g2"]


def test_a_manuscript_pdf_is_checked_numbered_or_author_year(client, model, monkeypatch):
    import asyncio

    from aurora.literature import pdfs
    from aurora.memory import audit
    numbered = ("Sparse Gains\nAbstract\nWe study sparse attention. " + "Long context matters. " * 8 +
                "\n1 Introduction\nSparse attention gives 3x [1, 2]. Engines differ [2].\n"
                "References\n[1] Lovelace A. Sparse attention at scale. NeurIPS 2024.\n"
                "[2] Babbage C. Engines of difference. 2023.\n[3] Nobody. Unused. 2020.\n")
    monkeypatch.setattr(pdfs, "_text", lambda data, pages=2: (numbered, {}))
    tex, entries, title = asyncio.run(audit.from_pdf(None, b"%PDF"))
    assert title == "Sparse Gains" and [k for k, _ in entries] == ["ref1", "ref2", "ref3"]
    assert [c["keys"] for c in audit.citations(tex)] == [["ref1", "ref2"], ["ref2"]]
    assert "We study sparse attention" in audit.abstract_of(tex)
    author_year = ("Title\nAbstract\nx\n1 Introduction\nSparse attention gives 3x (Lovelace, 2024). "
                   "Babbage (2023) disagrees.\nReferences\nLovelace, A. (2024). Sparse attention at scale. NeurIPS.\n"
                   "Babbage, C. (2023). Engines of difference. Journal of Machines.\n")
    monkeypatch.setattr(pdfs, "_text", lambda data, pages=2: (author_year, {}))
    from aurora.api.routes.memory import _access
    access, _ = _access()
    tex, entries, _ = asyncio.run(audit.from_pdf(access, b"%PDF"))
    assert [k for k, _ in entries] == ["lovelace2024", "babbage2023"]
    assert [c["keys"] for c in audit.citations(tex)] == [["lovelace2024"], ["babbage2023"]]
    bad = client.post("/api/memory/audit", json={"pdf": "bm90IGEgcGRm"})        # "not a pdf"
    assert bad.status_code == 400


def test_a_quiet_hint_when_papers_you_have_may_back_the_sentence(client, model):
    p = _paper(abstract="The sparse attention speedup vanishes at batch size 1 on a single GPU.")
    library.add_item("", p["paper_id"], source="person", owner="local")
    h = client.post("/api/memory/cite/hint", json={"sentence": "Sparse attention gives no speedup at batch size 1 in our setting."}).json()
    assert h["papers"] == 1 and h["top"] == ["Sparse attention at scale"]
    assert "memory_cite" not in model.tasks                                    # no model was asked
    cold = client.post("/api/memory/cite/hint", json={"sentence": "Model collapse needs synthetic data to replace real data."}).json()
    assert cold["papers"] == 0


def test_a_new_paper_overlapping_a_checked_manuscript_interrupts(client, model):
    alerts.remember_manuscript("local", "au_1", "Sparse attention for single requests",
                               "We show that the sparse attention speedup vanishes at batch size 1, and why. " * 3)
    p = _paper(title="Sparse attention speedup at batch size 1", doi="10.1/new")
    src = mem_store.add_source("local", platform="paper", url="https://doi.org/10.1/new", title=p["paper"].title)
    mem_store.update_source(src["source_id"], state="done", paper_id=p["paper_id"])
    mem_store.save_snapshot(src["source_id"], "The sparse attention speedup vanishes at batch size 1. " * 5, "")
    import asyncio

    from aurora.api.routes.memory import _access
    access, models = _access()
    a = asyncio.run(alerts.check_overlap(access, models, "local", src["source_id"]))
    assert a and "manuscript" in a["title"] and "manuscript" in model.prompts["memory_overlap"]


def test_acting_on_the_digest_to_read_and_compare(client, model):
    p = _paper()
    assert client.post("/api/memory/to-read", json={"paper_id": p["paper_id"]}).json() == {"added": True}
    assert library.has_item("", p["paper_id"], owner="local")
    s1 = save(client, url="https://www.zhihu.com/question/1/answer/2", text=ZH)
    s2 = save(client, url="https://example.org/collapse", text="Blog: model collapse only appears when synthetic data "
                                                                 "replaces real data instead of accumulating with it.")
    a, b = s1["result"]["units"][0], s2["result"]["units"][0]
    r = client.post("/api/memory/compare", json={"a": {"unit_id": a}, "b": {"unit_id": b}}).json()
    assert r["text"].startswith("[0]") and r["short"]
    assert client.post("/api/memory/compare", json={"a": {"unit_id": a}, "b": {"unit_id": "u_nope"}}).status_code == 404
    worth = client.get("/api/memory/digest/worth").json()
    assert isinstance(worth, list)
    time.sleep(0)


def test_a_questions_synthesis_starts_from_the_map_of_its_field(client, model):
    from aurora.brainstorm.store import bs_store
    from aurora.review.store import review_store
    chat = bs_store.new_chat("user:local", "local", "Sparse attention")
    q = mem_store.add_question("local", "Does sparse attention save compute for single requests?", origin="mentor",
                               ref=chat["chat_id"])
    m = review_store.new_map("user:local", "local", "Sparse attention for serving", chat_id=chat["chat_id"])
    review_store._x("UPDATE rv_maps SET state='ready', version=1 WHERE map_id=?", (m["map_id"],))
    save(client, url="https://www.zhihu.com/question/1/answer/2", text=ZH)
    s = client.get(f"/api/memory/questions/{q['question_id']}/synthesis").json()
    assert s["map"]["map_id"] == m["map_id"]
    assert "The map of the field around it" in model.prompts["memory_synthesis"]
