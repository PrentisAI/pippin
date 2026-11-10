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
    assert card["unit_id"] == uid and card["source"].startswith("From your notebook") and card["type"] == "material"
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


def test_a_recording_becomes_words_for_a_message_box(client, monkeypatch, tmp_path):  # noqa: F811
    """Speaking into the brainstorm: the console only recognises the words (whisper), it does nothing with them."""
    import base64
    import stat

    whisper = tmp_path / "whisper-cli"
    whisper.write_text("#!/bin/sh\necho '[00:00:00.000 --> 00:00:02.000]  I want to work on egocentric memory'\n")
    whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.delenv("AURORA_WHISPER", raising=False)
    client.put("/api/settings", json={"changes": {"voice.hearing": "local"}})   # only whisper, on this machine
    assert client.get("/api/memory/voice/status").json()["hear"] is False
    audio = base64.b64encode(b"RIFF....WAVEfmt fake").decode()
    assert client.post("/api/memory/voice/transcribe", json={"audio": audio, "audio_type": "wav"}).status_code == 400
    monkeypatch.setenv("AURORA_WHISPER", f"{whisper} -nt -f {{audio}}")
    assert client.get("/api/memory/voice/status").json()["hear"] is True
    r = client.post("/api/memory/voice/transcribe", json={"audio": audio, "audio_type": "wav"})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "I want to work on egocentric memory"}
    assert client.post("/api/memory/voice/transcribe", json={"audio": "not base64!", "audio_type": "wav"}).status_code == 400


def test_words_while_speaking_use_the_fast_model_and_a_window_cut_to_the_recording(client, monkeypatch, tmp_path):  # noqa: F811
    import base64
    import stat
    import struct

    def whisper(name):
        w = tmp_path / name
        w.write_text(f'#!/bin/sh\necho "{name} $*"\n')
        w.chmod(w.stat().st_mode | stat.S_IEXEC)
        return w
    client.put("/api/settings", json={"changes": {"voice.hearing": "local"}})
    monkeypatch.setenv("AURORA_WHISPER", f"{whisper('main')} -f {{audio}}")
    monkeypatch.setenv("AURORA_WHISPER_LIVE", f"{whisper('small')} -f {{audio}}")
    # two seconds of 16 kHz mono WAV: a window of 2 * 50 + 64 frames
    import math
    pcm = b"\0\0" * 16000 + b"".join(struct.pack("<h", int(8000 * math.sin(i / 5))) for i in range(16000))  # a second of quiet, a second of sound
    wav = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) \
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    audio = base64.b64encode(wav).decode()
    live = client.post("/api/memory/voice/transcribe", json={"audio": audio, "audio_type": "wav", "live": True}).json()["text"]
    assert live.startswith("small --audio-ctx 164 ")
    final = client.post("/api/memory/voice/transcribe", json={"audio": audio, "audio_type": "wav"}).json()["text"]
    assert final.startswith("main -f ") and "--audio-ctx" not in final
    assert client.get("/api/memory/voice/status").json()["live"] == "fast"
    # accurate: the main model while speaking too, still with the window cut
    client.put("/api/settings", json={"changes": {"voice.live": "accurate"}})
    live = client.post("/api/memory/voice/transcribe", json={"audio": audio, "audio_type": "wav", "live": True}).json()["text"]
    assert live.startswith("main --audio-ctx 164 ")
    client.put("/api/settings", json={"reset": ["voice.live"]})


def test_answers_are_read_aloud_by_a_model_that_talks(client, monkeypatch):  # noqa: F811
    """openai/gpt-audio-mini through the model key: the text read word for word, at most about as long as it takes
    to say, and without the silence a talking model can go on with."""
    import array
    import base64
    import json as _json

    import httpx

    from aurora.memory import voice
    rate = 24000
    pcm = array.array("h", [0] * rate + [8000, -8000] * (rate // 2) + [0] * rate * 3).tobytes()   # 1 s quiet, 1 s said, 3 s quiet
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        chunks = [pcm[i:i + 9600] for i in range(0, len(pcm), 9600)]
        events = "".join(f"data: {_json.dumps({'choices': [{'delta': {'audio': {'data': base64.b64encode(c).decode()}}}]})}\n\n"
                         for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=events.encode(), headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40)
    monkeypatch.delenv("AURORA_TTS_BASE_URL", raising=False)
    assert client.get("/api/memory/voice/status").json()["cloud_voice"] is True
    r = client.post("/api/memory/voice/speak", json={"text": "你提到想让 agent 记住用户每天的生活。Which part matters most?"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    seconds = (len(r.content) - 44) / 2 / rate
    assert 1.0 <= seconds <= 1.5                                    # the speech and a breath, not the silences
    body = seen["body"]
    assert body["model"] == "openai/gpt-audio-mini" and body["modalities"] == ["text", "audio"]
    assert body["messages"][-1]["content"].startswith("Line: «你提到") and "Speak exactly that line" in body["messages"][0]["content"]
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert 60 < body["max_tokens"] < 400                            # about as long as it takes to say
    # the browser's voice instead, when chosen
    client.put("/api/settings", json={"changes": {"voice.speech": "browser"}})
    assert client.get("/api/memory/voice/status").json()["cloud_voice"] is False
    client.put("/api/settings", json={"reset": ["voice.speech"]})
    assert voice._trim_silence(b"\0\0" * 1000) == b""


def test_a_reading_that_answers_the_text_is_tried_again_then_refused(client, monkeypatch):  # noqa: F811
    """A model that talks may take "停掉这个实验？" as said to it; its transcript of what it said gives that away."""
    import base64
    import json as _json

    import httpx
    said = ["好的，我明白了，我会停掉这个实验。", "停掉这个实验？"]
    calls = []

    def handler(request):
        calls.append(1)
        words = said[min(len(calls), len(said)) - 1]
        audio = base64.b64encode(b"\x40\x1f\xc0\xe0" * 12000).decode()
        events = (f"data: {_json.dumps({'choices': [{'delta': {'audio': {'data': audio, 'transcript': words}}}]})}\n\n"
                  "data: [DONE]\n\n")
        return httpx.Response(200, content=events.encode(), headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40)
    monkeypatch.delenv("AURORA_TTS_BASE_URL", raising=False)
    r = client.post("/api/memory/voice/speak", json={"text": "停掉这个实验？"})
    assert r.status_code == 200 and len(calls) == 2                 # the second reading was the text
    said[:] = ["好的，我明白了。"]
    calls.clear()
    r = client.post("/api/memory/voice/speak", json={"text": "停掉这个实验？"})
    assert r.status_code == 400 and "did not read the text" in r.json()["detail"] and len(calls) == 2
