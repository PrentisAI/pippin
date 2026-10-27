"""Zoom out and zoom in: a map built as a chain (need, definition, design space, ...), its loops back up the
chain, and a deep read whose question 3 never sees the method."""

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from aurora.literature.library import library
from aurora.literature.records import Paper
from aurora.review import deepread, fieldmap
from aurora.review import llm as rllm
from aurora.review.store import review_store
from tests.test_brainstorm_more import _pdf
from tests.test_literature import FakeIndexes


def _paper(i: int, title: str, year: int, abstract: str) -> Paper:
    return Paper(title=title, authors=[f"Ann Author{chr(97 + i)}"], year=year, venue="CoRL", doi=f"10.1/mem{i}",
                 abstract=abstract)


WORLD = [
    _paper(i, f"Robot memory method number {i} with frames", 2016 + i % 10,
           f"We store frames of the episode in a memory bank number {i} and read them with attention at every step.")
    for i in range(14)
]
# the most recent paper is the one that does not fit until the framework is revised
WORLD[13] = _paper(13, "A planner that picks keyframes as memory for the controller", 2025,
                   "A high level model selects keyframes from the history and passes them to a low level policy.")


def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))])


def _keys(text):
    return list(dict.fromkeys(re.findall(r"\[([A-Z][A-Za-z]+\d\d[a-z]?)\]", text)))


class FieldModel:
    """Each task answered as a careful model would, from what its prompt gives it."""

    def __init__(self):
        self.prompts: dict[str, list[str]] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, **kw):
        text = messages[-1]["content"] if messages[-1]["role"] == "user" else messages[0]["content"]
        task = re.match(r"TASK: (\w+)", text).group(1) if text.startswith("TASK:") else "?"
        self.prompts.setdefault(task, []).append(text)
        keys = _keys(text)
        newest = "Authorn25"
        out: object = {}
        if task == "need":
            out = {"term": "memory", "why": [{"text": "A robot sees part of the world at a time.", "label": "inf"}],
                   "examples": [{"task": "Put the cup back", "needs": "where it was"}],
                   "formal": [{"text": "A POMDP: the action depends on the history.", "label": "lit"}],
                   "capabilities": [{"name": "Remember where things are", "info": "positions", "timescale": "minutes", "example": "cup"},
                                    {"name": "Remember what happened", "info": "events", "timescale": "hours", "example": "stove"}]}
        elif task == "scope":
            out = {"topic": "Memory for robots", "question": "How do robots keep what is out of view?",
                   "in_scope": ["robot memory methods"], "out_of_scope": ["generic vision-language models: building blocks"],
                   "subquestions": [{"text": "memory inside policies"}, {"text": "memory for planners"}],
                   "criteria": {"include": ["a method or system that keeps information for a robot"],
                                "exclude": [{"text": "not about memory"}, {"text": "a building block only"}]},
                   "keywords": ["robot", "memory"], "ask": ["Navigation too?"] if "amends" not in text else []}
        elif task == "discover":
            if any(f"Find: {x}" in text for x in ("recent surveys", "foundational", "peer-reviewed")):
                out = {"papers": []}
            elif "Find: papers on this sub-question: memory inside policies" in text:
                out = {"papers": [{"title": WORLD[i].title, "year": WORLD[i].year, "first_author": WORLD[i].authors[0]}
                                  for i in (0, 2, 4, 6, 8, 10, 12)] + [{"title": "A made-up paper on memory", "year": 2024}]}
            else:
                out = {"papers": [{"title": WORLD[i].title, "year": WORLD[i].year, "first_author": WORLD[i].authors[0]}
                                  for i in (13, 9, 1, 3, 5, 7, 11)]}
        elif task == "landmarks":
            out = {"papers": [{"title": WORLD[0].title, "first_author": "Ann Authora", "year": 2016},
                              {"title": "A paper the model made up about memory", "first_author": "Nobody", "year": 2019}]}
        elif task in ("screen", "screen_fulltext", "adjudicate"):
            second = model.startswith("google/")
            recs = []
            for k in keys:
                y = int(k[-2:])
                rec = {"id": k, "d": "include", "code": "", "why": "a memory method", "subs": ["s2" if y % 2 else "s1"],
                       "kind": "benchmark" if k == "Authorm18" else "method"}
                if task == "screen" and second and k == "Authorl17":
                    rec = {"id": k, "d": "exclude", "code": "E1", "why": "not about memory", "subs": [], "kind": "other"}
                recs.append(rec)
            out = {"records": recs}
        elif task == "corpus_edit":
            out = {"remove": ["Authorb17"], "add": [{"title": WORLD[11].title, "first_author": "Ann Authorl", "year": 2017}],
                   "note": "dropped one, added one"}
        elif task == "card":
            abstract = re.search(r"its abstract only\):\n(.*?)\n\nExtract", text, re.S).group(1)
            out = {"sub_problem": "keeping what the robot saw", "choice": f"store frames ({keys[0]})", "assumptions": ["static scene"],
                   "limitations": [{"text": "fails when objects move", "stated": True}],
                   "results": [{"claim": "better recall", "benchmark": "a home"}], "capabilities": ["c1", "c9"],
                   "term_use": "memory means stored frames",
                   "quotes": [{"field": "choice", "text": " ".join(abstract.split()[:7])}, {"field": "x", "text": "words it never said"}]}
        elif task == "definition":
            out = {"communities": [{"community": "imitation learning", "meaning": "a window of frames", "examples": keys[:2], "label": "lit"}],
                   "terms": [], "conflicts": [{"question": "Are weights memory?", "positions": [{"view": "no", "who": keys[:1]}]}],
                   "working": {"text": "What the robot keeps from its past to act later.",
                               "components": [{"name": "What is kept", "phrase": "what the robot keeps"},
                                              {"name": "Who reads it", "phrase": "to act later"}],
                               "boundaries": [{"question": "Are weights memory?", "decision": "no", "why": "scope", "consequence": "fewer papers"}],
                               "covers": [{"capability": "c1", "how": "positions are kept"}]}}
        elif task == "framework":
            dims = [{"name": "What is kept", "component": "k1", "values": [{"label": "Frames", "meaning": ""}, {"label": "Latent", "meaning": ""}],
                     "explains": "cost"},
                    {"name": "Who reads it", "component": "k2", "values": [{"label": "Controller", "meaning": ""}, {"label": "Planner", "meaning": ""}],
                     "explains": "latency"}]
            if "Revise it" in text:
                dims[1]["values"].append({"label": "Planner for the controller", "meaning": "picks what the controller sees"})
            out = {"name": "Kept and read", "dims": dims}
        elif task == "place":
            revised = "Planner for the controller" in text
            pl = []
            for k in keys:
                if k == newest and not revised:
                    pl.append({"key": k, "values": {"d1": "a"}, "strain": True, "note": "a planner reads it for the controller"})
                else:
                    pl.append({"key": k, "values": {"d1": "a" if k[-1] in "02468" else "b", "d2": "c" if k == newest else "a"},
                               "strain": False, "evidence": "We store frames of the episode"})
            out = {"placements": pl}
        elif task == "judge":
            out = {"score": 0.7, "reason": "where memory is read explains latency"}
        elif task == "progress":
            out = {"status": "partly", "headline": "Works in static scenes.", "best": keys[:1],
                   "results": [{"key": keys[0] if keys else "", "claim": "better recall", "benchmark": "a home"}],
                   "conditions": [{"text": "Static scenes only.", "label": "inf"}],
                   "limitations": [{"text": "fails when objects move", "keys": keys[:3]}], "conflicts": [], "cost": [], "eval_gap": []}
        elif task == "trends":
            out = {"timeline": [{"years": "2016–2020", "name": "Frames", "driver": "new simulators", "text": "x", "turning": keys[:1]}],
                   "trends": [{"title": "Back into the policy", "text": "y", "label": "inf", "strength": "moderate", "based": "z"}]}
        elif task == "assumptions":
            out = {"assumptions": [{"text": "Scenes are static", "keys": keys[:4], "why": "easy", "overturn": "moving objects",
                                    "challengers": [], "label": "inf"}]}
        elif task == "problems":
            out = {"problems": [{"title": "Remembering objects that move", "capability": "c1", "importance": "high",
                                 "gap": "most methods assume static scenes", "evidence": keys[:2],
                                 "diagnosis": {"cause": "assumption", "text": "maps have no time"}, "why_now": "video models",
                                 "approaches": [{"text": "keep time in the map", "from": "dropped assumption", "label": "spec"}],
                                 "first_experiment": "move five objects", "failure": "too slow", "search": "robot memory"}],
                   "adjacent": [{"field": "LLM agents", "same": "bounded context", "borrow": "paging", "checks": [{"ok": False, "text": "too slow"}]}]}
        elif task == "closest":
            out = {"closest": [{"key": keys[-1], "difference": "it keeps no time"}], "novel": "partly", "note": "close"}
        elif task == "read_next":
            out = {"read": [{"key": keys[0], "why": "turning point", "focus": [3, 9]}]}
        elif task == "challenge":
            out = {"support": [{"text": "RAG calls weights parametric memory (Lewis et al. 2020).", "label": "lit"}],
                   "options": [{"boundary": "Weights updated from the robot's own experience count as memory",
                                "dimensions": "a new one: where it lives", "papers": "continual learning", "progress": "c2",
                                "search": "robot memory"},
                               {"boundary": "Keep it as it is", "dimensions": "none", "papers": "none", "progress": "none"}]}
        elif task == "place_ideas":
            ids = re.findall(r"- (c_\w+):", text)
            out = {"ideas": [{"card_id": i, "values": {"d1": "a"}, "problem": "q1", "note": "next to the frame banks"} for i in ids]}
        elif task == "relevant":
            out = {"relevant": keys}
        elif task == "contributions":
            out = {"drop": [1]}
        elif task == "deep_read_q3":
            out = {"moves": [{"text": "Earlier maps were static.", "kind": "failure mode", "label": "inf"},
                             {"text": "Combine them.", "kind": "combination", "label": "inf"}],
                   "summary": [{"text": "Anyone who saw static maps fail could get here.", "label": "inf"}]}
        elif task == "deep_read_q3_check":
            out = {"moves": [{"i": 0, "support": "stated", "quote": "Robots forget what they saw"}, {"i": 1, "support": "consistent"}],
                   "authors_say": [{"text": "They start from forgetting.", "label": "paper", "quote": "Robots forget what they saw"}]}
        elif task.startswith("deep_read_q") or task == "deep_read_followup":
            n = task[len("deep_read_q"):]
            out = {"paragraphs": [{"text": f"Answer {n}.", "label": "paper", "quote": "We merge tokens"},
                                  {"text": "A guess.", "label": "spec", "quote": "not in the paper at all, surely"}],
                   "example": "a kitchen", "steps": [{"what": "merge", "example": "two mugs"}],
                   "rows": [{"question": "Does it work?", "experiment": "a home", "answer": "yes"}],
                   "idea": "Keep time in the memory", "from": "the static assumption", "search": "robot memory"}
        return _reply(json.dumps(out))


@pytest.fixture()
def model(monkeypatch):
    fake = FieldModel()
    monkeypatch.setattr(rllm, "client_factory", lambda access: fake)
    monkeypatch.setattr(rllm, "RETRY_WAIT", 0)
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes(papers=WORLD))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    with TestClient(create_app()) as c:          # one event loop for the whole test: builds run in the background
        yield c


def settle(c, url, done, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(url).json()
        if done(r):
            return r
        time.sleep(0.1)
    raise AssertionError(f"not settled: {r}")


def ready(c, map_id):
    return settle(c, f"/api/review/maps/{map_id}", lambda r: r["state"] in ("ready", "failed") and not r["busy"])


def test_scores_are_computed_not_guessed():
    a = ["x", "x", "y", "y"]
    assert fieldmap._nmi(a, ["p", "p", "q", "q"]) == pytest.approx(1.0)       # one decides the other
    assert fieldmap._nmi(a, ["p", "q", "p", "q"]) == pytest.approx(0.0)       # independent
    fw = {"dims": [{"id": "d1"}, {"id": "d2"}],
          "placements": {"1": {"values": {"d1": "a", "d2": "a"}, "strain": False}, "2": {"values": {"d1": "b", "d2": "a"}, "strain": True},
                         "3": {"values": {"d1": "a", "d2": "b"}, "strain": False}, "4": {"values": {"d1": "b", "d2": "b"}, "strain": False}},
          "held": {"5": {"values": {}, "strain": True}}}
    s = fieldmap.score(fw)
    assert (s["fit"], s["held"], s["orth"], s["n_fit"], s["n_held"]) == (0.75, 0.0, 1.0, "3/4", "0/1")
    assert fieldmap.periods([2016, 2017, 2018, 2019, 2020, 2021]) == [(2016, 2017), (2018, 2019), (2020, 2021)]


def test_a_map_is_built_as_a_chain_and_revised_by_its_loops(client, model):
    m = client.post("/api/review/spaces/global/maps", json={"topic": "Memory for robots", "brief": "manipulation",
                                                             "size": 12}).json()
    r = ready(client, m["map_id"])
    assert r["state"] == "ready" and r["version"] == 1, r["error"]
    c = r["content"]
    # 0. why, with the capabilities that anchor the rest
    assert [x["id"] for x in c["need"]["capabilities"]] == ["c1", "c2"] and c["need"]["term"] == "memory"
    # scope first: the protocol given to the model is ARS's own text
    assert "Topic Decomposition" in model.prompts["scope"][0] and "Inclusion/Exclusion Criteria" in model.prompts["scope"][0]
    assert [x["id"] for x in c["scope"]["subquestions"]] == ["s1", "s2"] and c["scope"]["criteria"]["exclude"][0]["code"] == "E1"
    # papers named by the web search or the model are kept only when the indexes have them
    pr = c["prisma"]
    assert pr["not_found"] == 2 and pr["identified"]["web"] == 15 and pr["identified"]["known to the model"] == 2
    assert not any("made up" in p["title"] for p in r["papers"])
    # two screenings with ARS's decision rules; the disagreement was adjudicated
    assert "Labels and codes" in model.prompts["screen"][0] and len(model.prompts["screen"]) >= 2
    assert pr["conflicts"] == 1 and "Conflicts" in model.prompts["adjudicate"][0]
    assert c["screening"][library.paper("Authorl17")["paper_id"]]["by"] == "adjudicated"
    # the papers: checked ones only, some held out, cards keep only quotes the text has
    roles = {p["key"]: p["role"] for p in r["papers"]}
    assert roles["Authorn25"] == "held_out" and list(roles.values()).count("held_out") >= 2
    core = next(p for p in r["papers"] if p["role"] == "core")
    card = core["card"]
    assert [q["text"] for q in card["quotes"]] == [" ".join(core["abstract"].split()[:7])] and card["source"] == "abstract"
    assert card["capabilities"] == ["c1"]                         # an unknown capability id is dropped
    # 1. the definition, split into components that the dimensions come from
    assert [k["id"] for k in c["definition"]["working"]["components"]] == ["k1", "k2"]
    # 2–3. three frameworks (one per seat model), scored on the same papers; the misfit is a proposal
    fws = c["frameworks"]
    assert len(fws) == 3 and len({f["by"] for f in fws}) == 3 and c["chosen"] == fws[0]["id"]
    fw = fws[0]
    assert [d["component"] for d in fw["dims"]] == ["k1", "k2"]
    assert fw["held"][library.paper("Authorn25")["paper_id"]]["strain"] and fw["scores"]["disc"] == 0.7
    assert fw["scores"]["n_held"].startswith(f"{len(fw['held']) - 1}/")
    placed = next(iter(fw["placements"].values()))
    assert placed["verified"] is True                             # the evidence is the paper's own words
    (prop,) = [p for p in c["proposals"] if p["state"] == "pending"]
    assert "for the controller" in prop["text"]
    # held-out papers are placed apart: the call that places them sees no other paper
    held_prompt = [p for p in model.prompts["place"] if "[Authorn25]" in p][0]
    assert len(_keys(held_prompt.split("The design space")[1])) <= len(fw["held"])
    # a benchmark is evidence of what the field measures: read, used for progress, never placed
    bench = library.paper("Authorm18")["paper_id"]
    assert roles["Authorm18"] == "benchmark" and bench not in fw["placements"] and bench not in fw["held"]
    assert any("(a benchmark, 2018)" in p for p in model.prompts["progress"])
    # 4. progress per capability, 5–6 trends and assumptions, 7. problems with the closest work searched for
    assert {p["capability"]: p["status"] for p in c["progress"]} == {"c1": "partly", "c2": "partly"}
    assert c["timeline"][0]["turning"] and c["assumptions"][0]["text"] == "Scenes are static"
    (prob,) = c["problems"]
    assert prob["diagnosis"]["cause"] == "assumption" and prob["novelty"] == "partly" and prob["closest"]
    assert "Sparse regions" in model.prompts["problems"][0] and c["sparse"]
    assert c["read_next"][0]["focus"] == [3, 9]
    assert r["content"]["distribution"]["periods"]
    # the loop from a misfit: accept the revision, everything is placed again, version 2
    client.post(f"/api/review/maps/{m['map_id']}/proposals/{prop['id']}/accept")
    r = ready(client, m["map_id"])
    fw = next(f for f in r["content"]["frameworks"] if f["id"] == r["content"]["chosen"])
    assert r["version"] == 2 and "did not fit" in r["versions"][-1]["reason"]
    assert [v["label"] for v in fw["dims"][1]["values"]][-1] == "Planner for the controller"
    assert not fw["held"][library.paper("Authorn25")["paper_id"]]["strain"]
    assert next(p for p in r["content"]["proposals"] if p["id"] == prop["id"])["state"] == "accepted"
    # the loop from a challenge: consequences first (nothing changes), then an option applied
    client.post(f"/api/review/maps/{m['map_id']}/challenges", json={"text": "Aren't the model's weights memory too?"})
    r = ready(client, m["map_id"])
    (ch,) = r["content"]["challenges"]
    assert ch["state"] == "pending" and ch["support"][0]["label"] == "lit" and ch["options"][0]["joins"]
    assert r["version"] == 2
    client.post(f"/api/review/maps/{m['map_id']}/challenges/{ch['id']}/apply", json={"option": "o1"})
    r = ready(client, m["map_id"])
    assert r["version"] == 3 and r["content"]["decided"] == ["Weights updated from the robot's own experience count as memory"]
    assert "own experience count as memory" in model.prompts["definition"][-1]
    # a placement the researcher disputes is placed again with their note
    pid = core["paper_id"]
    client.post(f"/api/review/maps/{m['map_id']}/papers/{pid}/replace", json={"note": "It keeps latents, not frames"})
    r = ready(client, m["map_id"])
    assert r["version"] == 4 and "It keeps latents" in model.prompts["place"][-1]
    # an unsolved problem goes to the brainstorm with its evidence
    got = client.post(f"/api/review/maps/{m['map_id']}/problems/q1/brainstorm").json()
    card = bs_store.card(got["card_id"])
    assert card["type"] == "idea" and card["text"].startswith("Remembering objects that move") and got["links"] >= 2
    # versions are kept, each with its reason
    assert [v["version"] for v in client.get(f"/api/review/maps/{m['map_id']}").json()["versions"]] == [1, 2, 3, 4]
    assert client.get(f"/api/review/maps/{m['map_id']}/versions/1").json()["chosen"]


def test_new_papers_join_and_a_map_is_its_owners(client, model):
    m = client.post("/api/review/spaces/global/maps", json={"topic": "Memory for robots", "size": 12}).json()
    r = ready(client, m["map_id"])
    extra = library.upsert_paper(_paper(20, "Robot memory for objects that move", 2025, "We track moved objects."))
    library.add_item("", extra["paper_id"], source="person", owner="local")
    assert client.get(f"/api/review/maps/{m['map_id']}").json()["new_papers"] >= 1
    client.post(f"/api/review/maps/{m['map_id']}/check-new")
    r = ready(client, m["map_id"])
    assert {p["key"]: p["role"] for p in r["papers"]}[extra["key"]] == "joined"
    assert "placed" in r["versions"][-1]["reason"]
    # nobody else sees a private map
    review_store._x("UPDATE rv_maps SET owner='someone' WHERE map_id=?", (m["map_id"],))
    assert client.get(f"/api/review/maps/{m['map_id']}").status_code == 404
    assert client.get("/api/review/spaces/global/maps").json() == []


def test_a_build_that_stops_can_resume(client, model, monkeypatch):
    real = model.create

    async def failing(*, model, messages, **kw):
        if messages[-1]["content"].startswith("TASK: progress"):
            raise RuntimeError("the provider is down")
        return await real(model=model, messages=messages, **kw)

    model.chat.completions.create = failing
    m = client.post("/api/review/spaces/global/maps", json={"topic": "Memory for robots", "size": 12}).json()
    r = ready(client, m["map_id"])
    assert r["state"] == "failed" and "provider is down" in r["error"]
    assert [s["done"] for s in r["steps"]][:7] == [True] * 7 and not r["steps"][7]["done"]
    model.chat.completions.create = real
    client.post(f"/api/review/maps/{m['map_id']}/run", json={})
    r = ready(client, m["map_id"])
    assert r["state"] == "ready" and r["version"] == 1 and r["content"]["progress"]


# -- zoom in -------------------------------------------------------------------------------------

PAPER_LINES = ["Keyframe memory for robot policies", "1 Introduction", "Robots forget what they saw.",
               "In this paper we propose a memory bank of tokens.", "2 Related Work", "Earlier maps were static.",
               "3 Method", "We merge tokens that look alike.", "4 Experiments", "It works in a home."]


def _with_pdf() -> str:
    key = library.upsert_paper(WORLD[13])["key"]
    earlier = library.upsert_paper(WORLD[2])        # an earlier paper the authors could have known
    library.add_item("", earlier["paper_id"], source="person", owner="local")
    folder = Path(os.environ["AURORA_HOME"]) / "library" / "local" / "papers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.pdf").write_bytes(_pdf(PAPER_LINES))
    return key


def test_the_authors_path_is_built_without_the_method():
    intro = "Robots forget what they saw.\n\nIn this paper we propose a memory bank. It beats all baselines.\n\nMaps were static."
    assert deepread.without_contributions(intro) == "Robots forget what they saw.\n\nIt beats all baselines.\n\nMaps were static."
    assert deepread.without_contributions(intro, {1}) == "Robots forget what they saw.\n\nMaps were static."
    s = deepread.sections("Title\n1 Introduction\nWhy.\n2 Related Work\nBefore.\n3 Method\nHow.")
    assert s["intro"].strip() == "Why." and s["related"].strip() == "Before."
    # headings as PDFs extract them: small capitals split, the number on a line of its own
    s = deepread.sections("Title\n1 I NTRODUCTION\nWhy.\n2\nR ELATED WORK\nBefore.\n3 M ETHOD\nHow.")
    assert s["intro"].strip() == "Why." and s["related"].strip() == "Before."


def test_a_deep_read_answers_twelve_questions_and_hides_the_path_until_you_guess(client, model):
    key = _with_pdf()
    r = client.post("/api/review/reads", json={"paper": key, "space": "global"}).json()
    rid = r["read_id"]
    r = settle(client, f"/api/review/reads/{rid}",
               lambda x: not x["busy"] and all(a.get("state") in ("done", "hidden", "failed") for a in x["answers"].values()))
    # the AI answered all twelve, but none counts as answered and none shows until the reader answers or asks to see it
    assert len(r["answers"]) == 12 and all(a == {"state": "hidden", "ready": True} for a in r["answers"].values())
    assert r["answered"] == [] and r["ready"] == list(range(1, 13))
    assert client.post(f"/api/review/reads/{rid}/followups", json={"question": 1, "text": "why?"}).status_code == 400
    r = client.patch(f"/api/review/reads/{rid}", json={"question": 1, "answer": "Robots forget; the paper keeps tokens."}).json()
    assert r["answered"] == [1] and r["mine"]["1"]["text"].startswith("Robots forget")
    r = client.patch(f"/api/review/reads/{rid}", json={"question": 7, "show": True}).json()
    assert r["answered"] == [1] and r["answers"]["2"]["state"] == "hidden"
    r = client.patch(f"/api/review/reads/{rid}", json={"question": 5, "show": True}).json()
    a1 = r["answers"]["1"]
    # a quote the paper has keeps its page; one it does not have is dropped
    assert a1["paragraphs"][0]["quote"] == "We merge tokens" and a1["paragraphs"][0]["page"] == 0
    assert a1["paragraphs"][1]["quote"] == "" and a1["paragraphs"][1]["label"] == "spec"
    assert r["answers"]["7"]["rows"][0]["question"] == "Does it work?" and r["answers"]["5"]["steps"]
    # question 3 never saw the method, the experiments or the paper's own contribution
    q3 = model.prompts["deep_read_q3"][0]
    assert "Robots forget what they saw" in q3 and "Earlier maps were static" in q3
    assert "We merge tokens" not in q3 and "It works in a home" not in q3 and "we propose" not in q3.lower()
    assert "[Authorc18]" in q3                                       # an earlier paper of the literature
    # question 12 waited for 9 and 11, and its closest work was searched for
    assert "What the reading found so far" in model.prompts["deep_read_q12"][0]
    r = client.patch(f"/api/review/reads/{rid}", json={"question": 12, "show": True}).json()
    assert r["answers"]["12"]["novelty"] == "partly" and r["answers"]["12"]["closest"]
    # you guess first, then the path shows, with what the authors themselves say
    r = client.patch(f"/api/review/reads/{rid}", json={"guess": "They saw static maps fail.", "revealed": True, "ticks": [0]}).json()
    q3a = r["answers"]["3"]
    assert [m["support"] for m in q3a["moves"]] == ["stated", "consistent"] and q3a["moves"][0]["page"] == 0
    assert q3a["authors_say"][0]["label"] == "paper" and r["guess"] == "They saw static maps fail." and r["ticks"] == [0]
    assert r["answered"] == [1, 3]
    client.patch(f"/api/review/reads/{rid}", json={"question": 9, "show": True})
    # more questions, one more round, and the idea into the brainstorm
    r = client.post(f"/api/review/reads/{rid}/followups", json={"question": 9, "text": "Is it tested anywhere?"}).json()
    assert r["answers"]["9"]["followups"][0]["q"] == "Is it tested anywhere?"
    got = client.post(f"/api/review/reads/{rid}/idea").json()
    assert bs_store.card(got["card_id"])["text"] == "Keep time in the memory" and got["links"] >= 2
    assert client.get("/api/review/reads", params={"paper": key}).json()["read_id"] == rid


# -- in the conversation ---------------------------------------------------------------------------

def _call(name, **args):
    return SimpleNamespace(id=f"call_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ChatModel:
    """The brainstorm's agents: the Librarian proposes a map or a deep read, anyone passes on a challenge."""

    def __init__(self):
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, tools=None, **kw):
        if not tools:
            return _reply("Librarian")
        self.seen.append(messages)
        asked = messages[-1]["content"]
        done = [m.get("tool_call_id") for m in messages if m["role"] == "tool"]
        names = {t["function"]["name"] for t in tools}
        if "weights" in asked and "revise_map" in names and "call_revise_map" not in done:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[
                _call("revise_map", what="definition", text="Aren't the model's weights memory too?")]))])
        if "deep-read" in asked and "call_deep_read" not in done:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[
                _call("deep_read", paper_key="Authorn25")]))])
        if "been done" in asked and "call_map_field" not in done:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[
                _call("map_field", topic="Memory for robots", brief="manipulation")]))])
        return _reply("Here is what I think.")


@pytest.fixture()
def agents(monkeypatch):
    from aurora.brainstorm import agents as ag
    fake = ChatModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    return fake


def settle_chat(c, chat_id, done, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if not r["pending"] and done(r["messages"]):
            return r["messages"]
        time.sleep(0.1)
    raise AssertionError(f"the conversation did not get there: {[m['text'][:60] for m in r['messages']]}")


def widgets(msgs, kind):
    return [m for m in msgs if (m.get("widget") or {}).get("type") == kind]


def test_the_map_lives_in_the_conversation(client, model, agents):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    # a question about a whole field: the Librarian proposes to map it, and nothing runs until you say so
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "What has been done on memory for robots?"})
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "proposal"))
    (prop,) = widgets(msgs, "proposal")
    assert prop["widget"]["action"] == "map_field" and prop["widget"]["state"] == "pending" and prop["widget"]["calls"] > 30
    assert review_store.maps("user:local") == []
    m = client.post(f"/api/brainstorm/messages/{prop['msg_id']}/act", json={"choice": "start"}).json()
    assert m["widget"]["state"] == "started"
    map_id = m["widget"]["map_id"]
    # first the scope and the criteria, before any search: amended in conversation, then confirmed
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "scope"))
    (sc,) = widgets(msgs, "scope")
    assert "The map answers" in sc["text"] and "E1: not about memory" in sc["text"] and "Navigation too?" in sc["text"]
    assert review_store.map(map_id)["state"] == "waiting" and "pool" not in review_store.map(map_id)["content"]
    r = client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "Leave navigation out, please"}).json()
    assert r["agents"] == []
    msgs = settle_chat(client, cid, lambda ms: len(widgets(ms, "scope")) == 2 and widgets(ms, "scope")[-1]["widget"]["state"] == "waiting")
    assert widgets(msgs, "scope")[0]["widget"]["state"] == "superseded"
    assert review_store.map(map_id)["content"]["scope"]["amended"] == ["Leave navigation out, please"]
    client.post(f"/api/brainstorm/messages/{widgets(msgs, 'scope')[-1]['msg_id']}/act", json={"choice": "go"})
    # then the papers, with the counts at each stage: amended in conversation, then confirmed
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "corpus"))
    (cp,) = widgets(msgs, "corpus")
    assert "Records identified" in cp["text"] and "In the map, by sub-question" in cp["text"]
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "Drop Authorb17, and add the eleventh memory paper"})
    msgs = settle_chat(client, cid, lambda ms: len(widgets(ms, "corpus")) == 2 and widgets(ms, "corpus")[-1]["widget"]["state"] == "waiting")
    roles = {library.paper(r["paper_id"])["key"]: r["role"] for r in review_store.papers(map_id)}
    assert "Authorb17" not in roles and roles.get("Authorl17") in ("core", "held_out")
    client.post(f"/api/brainstorm/messages/{widgets(msgs, 'corpus')[-1]['msg_id']}/act", json={"choice": "go"})
    ready(client, map_id)
    # each step's finding is told in the conversation
    msgs = settle_chat(client, cid, lambda ms: any(w["widget"]["step"] == "read_next" for w in widgets(ms, "map_step")))
    steps = [w["widget"]["step"] for w in widgets(msgs, "map_step")]
    assert steps[0] == "need" and {"papers", "definition", "frameworks", "progress", "problems", "read_next"} <= set(steps)
    told = {w["widget"]["step"]: w["text"] for w in widgets(msgs, "map_step")}
    assert "2 capabilities" in told["need"] and "Working definition" in told["definition"] and "say so here" in told["definition"]
    assert "Remembering objects that move" in told["problems"]
    # from now on the agents read the map with every turn
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "@Skeptic is my idea new?"})
    settle_chat(client, cid, lambda ms: ms[-1]["who"] == "Skeptic")
    assert "## The map of the field: Memory for robots" in agents.seen[-1][1]["content"]
    # disputing the map in conversation: the options come back here, and one is applied with a click
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "@Librarian aren't the model's weights memory too?"})
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "challenge"))
    (chm,) = widgets(msgs, "challenge")
    assert "Ways to answer it" in chm["text"] and chm["widget"]["state"] == "pending"
    client.post(f"/api/brainstorm/messages/{chm['msg_id']}/act", json={"choice": "o1"})
    ready(client, map_id)
    msgs = settle_chat(client, cid, lambda ms: any(w["widget"]["step"] == "version" for w in widgets(ms, "map_step")))
    assert any("version 2" in w["text"] for w in widgets(msgs, "map_step"))
    # the space's ideas are placed on the map beside the conversation
    idea = bs_store.add_card("user:local", "local", "idea", "Keep time in the memory", by="local", chat_id=cid)
    client.get("/api/review/spaces/global/pane")
    pane = settle(client, "/api/review/spaces/global/pane", lambda r: idea["card_id"] in (r["content"].get("ideas") or {}))
    assert pane["content"]["ideas"][idea["card_id"]]["problem"] == "q1"


def test_a_deep_read_asks_for_your_guess_in_the_conversation(client, model, agents):
    key = _with_pdf()
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": f"@Librarian please deep-read {key}"})
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "proposal"))
    (prop,) = widgets(msgs, "proposal")
    assert prop["widget"]["action"] == "deep_read" and prop["widget"]["key"] == key
    client.post(f"/api/brainstorm/messages/{prop['msg_id']}/act", json={"choice": "start"})
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "guess"), timeout=40)
    (g,) = widgets(msgs, "guess")
    assert g["widget"]["state"] == "waiting" and "how would you have" in g["text"]
    # the next message is the guess: no agent answers it; the authors' path follows
    r = client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "They saw that static maps fail."}).json()
    assert r["agents"] == []
    msgs = settle_chat(client, cid, lambda ms: widgets(ms, "path"))
    (path,) = widgets(msgs, "path")
    assert "How the authors may have got there" in path["text"] and "the authors say so" in path["text"]
    read = review_store.read_by_id(g["widget"]["read_id"])
    assert read["guess"] == "They saw that static maps fail." and read["revealed"]
    assert widgets(msgs, "guess")[0]["widget"]["state"] == "answered"


def test_answers_in_another_shape_are_taken_as_meant():
    assert rllm.shape([{"title": "a"}], "papers") == {"papers": [{"title": "a"}]}
    assert rllm.shape([{"x": 1}, 2], "") == {"x": 1}
    assert rllm.shape("text", "") == {} and rllm.shape([1], None) == [1] and rllm.shape({"a": 1}, "b") == {"a": 1}
