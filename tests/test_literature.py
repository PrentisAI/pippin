"""Real literature: papers come from the indexes, every reference is checked, made-up ones are flagged."""

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.agents.base import AgentContext
from aurora.agents.roundtable.seat import RoundtableSeat
from aurora.literature import indexes as indexes_mod
from aurora.literature.bibtex import parse as parse_bibtex
from aurora.literature.bibtex import to_bibtex
from aurora.literature.check import Reference, check, references_in
from aurora.literature.indexes import Found, Indexes, _arxiv_papers, _crossref_paper, _openalex_paper
from aurora.literature.library import library
from aurora.literature.records import Paper, clean_arxiv, clean_doi, clean_title, family_name, title_similarity
from aurora.literature.service import ProjectLiterature
from aurora.llm.base import LLMProvider, LLMResponse, TokenUsage

BEHRENS = Paper(title="The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts",
                authors=["Malte Behrens", "Felix Studt", "Igor Kasatkin"], year=2012, venue="Science",
                doi="10.1126/science.1219831", cited_by=2600, found_in=["crossref"])
KULD = Paper(title="Quantifying the promotion of Cu catalysts by ZnO for methanol synthesis",
             authors=["Stine Kuld", "Max Thorhauge"], year=2016, venue="Science", doi="10.1126/science.aaf0718",
             cited_by=800, found_in=["openalex"])
VASWANI = Paper(title="Attention Is All You Need", authors=["Ashish Vaswani", "Noam Shazeer"], year=2017,
                arxiv_id="1706.03762", found_in=["arxiv"])
WORLD = [BEHRENS, KULD, VASWANI]


class FakeIndexes(Indexes):
    """The indexes, as a list of known papers."""

    def __init__(self, papers=WORLD, down=False):
        super().__init__(use=("crossref", "openalex", "arxiv"))
        self.papers, self.down, self.searched = papers, down, []

    def _found(self, papers):
        if self.down:
            return Found(failed=list(self.use))
        return Found(papers=[Paper.from_dict(p.to_dict()) for p in papers],
                     raw=[Paper.from_dict(p.to_dict()) for p in papers], reached=list(self.use))

    async def by_doi(self, doi):
        return self._found([p for p in self.papers if p.doi == clean_doi(doi)])

    async def by_arxiv(self, arxiv_id):
        return self._found([p for p in self.papers if p.arxiv_id == clean_arxiv(arxiv_id)])

    async def look_up(self, title, authors="", year=None):
        return self._found([p for p in self.papers if title_similarity(title, p.title) > 0.5])

    async def search(self, query, limit=10):
        self.searched.append(query)
        words = set(query.lower().split())
        return self._found([p for p in self.papers if words & set(p.title.lower().split())][:limit])


@pytest.fixture()
def fake(monkeypatch):
    idx = FakeIndexes()
    monkeypatch.setattr(indexes_mod, "_shared", idx)
    return idx


# -- text helpers ---------------------------------------------------------------------------------

def test_identifiers_titles_and_names():
    assert clean_doi("https://doi.org/10.1126/Science.1219831.") == "10.1126/science.1219831"
    assert clean_arxiv("https://arxiv.org/abs/1706.03762v7") == "1706.03762"
    assert clean_arxiv("10.1126/science.1219831") == ""
    assert clean_title("over Cu/ZnO/Al\n <sub>2</sub>\n O\n <sub>3</sub>\n Industrial") == "over Cu/ZnO/Al2O3 Industrial"
    assert family_name("Behrens, Malte") == family_name("M. Behrens") == "behrens"
    assert title_similarity("Attention is all you need", "Attention Is All You Need.") == 1.0
    assert title_similarity("Attention is all you need", "Attention is all you need in speech separation") < 0.9
    assert BEHRENS.short_key() == "Behrens12" and BEHRENS.cites_per_year() > 0


def test_index_records_are_read_from_each_index():
    oa = _openalex_paper({"id": "https://openalex.org/W1", "title": "CO<sub>2</sub> to methanol", "publication_year": 2017,
                          "authorships": [{"author": {"display_name": "Shyam Kattel"}}], "cited_by_count": 900,
                          "ids": {"doi": "https://doi.org/10.1126/science.aal3573"},
                          "primary_location": {"source": {"display_name": "Science"}},
                          "abstract_inverted_index": {"Active": [0], "sites": [1]}})
    assert (oa.title, oa.doi, oa.venue, oa.abstract, oa.openalex_id) == (
        "CO2 to methanol", "10.1126/science.aal3573", "Science", "Active sites", "W1")
    cr = _crossref_paper({"DOI": "10.1126/SCIENCE.1219831", "title": ["The active site"], "subtitle": ["a study"],
                          "author": [{"given": "Malte", "family": "Behrens"}], "issued": {"date-parts": [[2012, 5]]},
                          "container-title": ["Science"], "is-referenced-by-count": 10})
    assert (cr.title, cr.authors, cr.year, cr.doi) == ("The active site: a study", ["Malte Behrens"], 2012,
                                                       "10.1126/science.1219831")
    xml = ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
           '<id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All\n You Need</title>'
           '<published>2017-06-12T00:00:00Z</published><summary>Transformers.</summary>'
           '<author><name>Ashish Vaswani</name></author>'
           '<link title="pdf" href="https://arxiv.org/pdf/1706.03762v7"/></entry></feed>')
    (ax,) = _arxiv_papers(xml)
    assert (ax.title, ax.arxiv_id, ax.year, ax.pdf_url) == ("Attention Is All You Need", "1706.03762", 2017,
                                                            "https://arxiv.org/pdf/1706.03762v7")


async def test_an_index_that_does_not_answer_is_not_a_paper_that_does_not_exist():
    def handler(request):
        if "crossref" in request.url.host:
            return httpx.Response(503)
        if "openalex" in request.url.host:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        idx = Indexes(client=client, use=("crossref", "openalex", "arxiv"))
        found = await idx.look_up("A paper nobody wrote", "Nobody", 2024)
        assert found.reached == ["arxiv", "openalex"] and found.failed == ["crossref"]
        result = await check(Reference(title="A paper nobody wrote", authors="Nobody", year=2024), idx)
        assert result.status == "not_found" and "Crossref could not be reached" in result.reason
        offline = Indexes(client=client, use=("crossref",))
        assert (await check(Reference(title="Anything at all"), offline)).status == "unchecked"


# -- checking -------------------------------------------------------------------------------------

async def test_real_papers_are_verified_and_made_up_ones_are_not():
    idx = FakeIndexes()
    ok = await check(Reference(title="The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts",
                               authors="Behrens et al.", year=2012), idx)
    assert ok.status == "verified" and ok.paper.doi == BEHRENS.doi
    assert (await check(Reference(doi="10.1126/science.1219831"), idx)).verified
    assert (await check(Reference(arxiv_id="1706.03762v2"), idx)).verified
    # a preprint year and a journal year may differ by one
    assert (await check(Reference(title=VASWANI.title, authors="Vaswani", year=2018), idx)).verified
    made_up = await check(Reference(title="Weather effects on bike-share ridership: a meta-analysis",
                                    authors="Smith, Lee", year=2024), idx)
    assert made_up.status == "not_found" and made_up.paper is None
    wrong_author = await check(Reference(title=KULD.title, authors="Smith", year=2016), idx)
    assert wrong_author.status == "not_found" and wrong_author.closest.doi == KULD.doi
    wrong_year = await check(Reference(title=KULD.title, authors="Kuld", year=2009), idx)
    assert wrong_year.status == "not_found"
    # a real DOI attached to a paper it does not belong to
    mismatch = await check(Reference(title="Graph networks for bike demand", authors="Smith", year=2024,
                                     doi="10.1126/science.1219831"), idx)
    assert mismatch.status == "mismatch" and "belongs" not in mismatch.reason and BEHRENS.title[:20] in mismatch.reason
    # a wrong DOI on a real title: the title wins
    fixed = await check(Reference(title=KULD.title, authors="Kuld", year=2016, doi="10.1126/science.1219831"), idx)
    assert fixed.verified and fixed.paper.doi == KULD.doi


def test_references_are_found_in_what_an_agent_wrote():
    keys, refs = references_in(
        "As shown [Behrens12] and [ref: Weather effects on bike-share; Smith, Lee; 2024], see "
        "https://arxiv.org/abs/1706.03762 and doi:10.1126/science.aaf0718. [QUESTION] is a tag, not a key. "
        "Grouped: [Wang25, Gumaan25] [Kuld16; Behrens12].")
    assert keys == ["Behrens12", "Wang25", "Gumaan25", "Kuld16"]
    assert [(r.title, r.year, r.doi, r.arxiv_id) for r in refs] == [
        ("Weather effects on bike-share", 2024, "", ""), ("", None, "", "1706.03762"), ("", None, "10.1126/science.aaf0718", "")]


def test_bibtex_round_trip():
    entries = [{"key": "Behrens12", "paper": BEHRENS}, {"key": "Vaswani17", "paper": VASWANI}]
    text = to_bibtex(entries)
    assert "@article{Behrens12," in text and "@misc{Vaswani17," in text and "eprint = {1706.03762}" in text
    parsed = parse_bibtex(text + '\n@comment{x}\n@book{k, title = "A {Book}", author = "Doe, J.", year = 1999}')
    assert [(k, r.title, r.year, r.doi, r.arxiv_id) for k, r in parsed] == [
        ("Behrens12", BEHRENS.title, 2012, BEHRENS.doi, ""), ("Vaswani17", VASWANI.title, 2017, "", "1706.03762"),
        ("k", "A Book", 1999, "", "")]
    assert parsed[0][1].first_author == "Malte Behrens"


# -- the library and the import rule ---------------------------------------------------------------

async def test_searches_are_kept_and_the_import_rule_decides_what_goes_in(fake):
    events = []

    async def publish(topic, **data):
        events.append(topic)

    for rule, expected in (("all", 2), ("cited", 0), ("none", 0)):
        lit = ProjectLiterature(f"p-{rule}", run_id="r1", rule=rule, publish=publish)
        found = await lit.search("methanol synthesis", by="p01", context="Ideation")
        assert [e["key"] for e in found] == ["Behrens12", "Kuld16"]
        assert len(lit.items()) == expected
    assert events.count("literature_search") == 3
    # citing: the cited paper goes in under 'cited', not under 'none'
    for rule, expected in (("cited", ["Behrens12"]), ("none", [])):
        lit = ProjectLiterature(f"p-{rule}", run_id="r1", rule=rule)
        cites = await lit.check_text("Zn decorates Cu steps [Behrens12].", by="p01", place="Ideation (p01)")
        assert cites.verified == ["Behrens12"] and cites.clean
        assert [i["key"] for i in lit.items()] == expected
    (search,) = library.searches("p-cited")
    assert [(r["paper_id"] == library.paper("Behrens12")["paper_id"], r["cited"]) for r in search["results"]] == [
        (True, 1), (False, 0)]


async def test_made_up_references_are_flagged_never_added(fake):
    lit = ProjectLiterature("p", run_id="r1", rule="all")
    cites = await lit.check_text(
        "Weekend demand is weather driven [ref: Weather effects on bike-share ridership: a meta-analysis; Smith, Lee; "
        "2024] and promoted by Zn [ref: Quantifying the promotion of Cu catalysts by ZnO for methanol synthesis; "
        "Kuld; 2016]. See also [Nobody99].", by="p02", place="Ideation (p02, presentation)")
    assert cites.verified == ["Kuld16"] and cites.unknown_keys == ["Nobody99"]
    (flag,) = cites.flagged
    assert flag["status"] == "not_found"
    note = cites.note()
    assert "Weather effects" in note and "unsupported" in note and "[Nobody99]" in note
    assert [i["key"] for i in lit.items()] == ["Kuld16"]
    (ref,) = library.refs("p")
    assert ref["status"] == "not_found" and ref["by"] == "p02"
    assert [u["place"] for u in library.uses("p") if u["ref_id"]] == ["Ideation (p02, presentation)"]
    # the same reference again is the same flag; a person's "remove" is respected afterwards
    await lit.check_text("[ref: Weather effects on bike-share ridership: a meta-analysis; Smith; 2024]", by="p03", place="x")
    assert len(library.refs("p")) == 1
    library.resolve_ref(ref["ref_id"], "removed", decided_by="Linda")
    assert (await lit.check_text("[ref: Weather effects on bike-share ridership: a meta-analysis; Smith; 2024]",
                                 by="p03", place="y")).clean
    # the run's bibliography has only real papers
    assert [e["key"] for e in lit.bibliography("r1")] == ["Kuld16"]
    # a key another project knows is not this project's: it does not count as cited here
    other = ProjectLiterature("other-project", run_id="r2")
    cites = await other.check_text("As shown in [Kuld16].", by="p01", place="x")
    assert cites.verified == [] and cites.unknown_keys == ["Kuld16"]


# -- ideation seats search and their references are checked -------------------------------------

class SeatProvider(LLMProvider):
    provider_name = "fake"

    def __init__(self):
        self.prompts = []

    async def generate(self, messages, *, model=None, **kwargs):
        text = messages[-1].content
        self.prompts.append(text)
        if "## Before you write" in text:
            reply = "SEARCH: methanol synthesis active site\nSEARCH: Zn promotion of Cu"
        else:
            reply = ("Prior work: Zn at Cu steps [Behrens12]. A meta-analysis [ref: Weather effects on bike-share "
                     "ridership: a meta-analysis; Smith; 2024] suggests more.")
        return LLMResponse(content=reply, model_id="fake/model", usage=TokenUsage(), raw_response=None)

    async def generate_structured(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


class Router:
    def __init__(self, provider):
        self.provider = provider

    def get_provider_and_model(self, stage, seat_index=0):
        return self.provider, "fake/model"

    async def generate(self, stage, messages, *, seat_index=0, **kwargs):
        return await self.provider.generate(messages, **kwargs)


async def test_a_seat_searches_before_presenting_and_its_made_up_reference_is_flagged(fake):
    provider = SeatProvider()
    seat = RoundtableSeat(Router(provider), seat_index=1)
    seat.literature = ProjectLiterature("proj", run_id="r1", rule="cited")
    assert "[Behrens12]" in seat.system_prompt() and "give the URL or DOI" not in seat.system_prompt()
    c = await seat.prepare(AgentContext(campaign_id="c", current_goal="Methanol"), "Methanol synthesis on Cu/ZnO")
    assert fake.searched == ["methanol synthesis active site", "Zn promotion of Cu"]
    presentation_prompt = provider.prompts[-1]
    assert "## Papers found for your searches" in presentation_prompt and "[Behrens12]" in presentation_prompt
    assert "Verified: Behrens12." in c.content and "Weather effects" in c.content and "unsupported" in c.content
    assert [s["by"] for s in library.searches("proj")] == ["p02", "p02"]
    # without literature nothing changes
    plain = RoundtableSeat(Router(SeatProvider()), seat_index=0)
    assert "give the URL or DOI" in plain.system_prompt() and "## Papers" not in plain.system_prompt()


# -- the console --------------------------------------------------------------------------------

@pytest.fixture()
def client(fake):
    from aurora.api.app import create_app
    return TestClient(create_app())


def test_a_projects_literature_in_the_console(client, fake):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol on Cu/ZnO"}).json()["campaign_id"]
    L = f"/api/campaigns/{cid}/literature"
    assert client.get(L).json()["counts"] == {"papers": 0, "flagged": 0, "checked": 0, "searches": 0}
    # a person's search is kept, adds nothing by itself; adding from it works
    s = client.post(L + "/search", json={"query": "methanol synthesis"}).json()
    assert [p["key"] for p in s["papers"]] == ["Behrens12", "Kuld16"] and s["by"] == "You"
    r = client.post(L + "/papers/add", json={"paper_ids": [s["papers"][0]["paper_id"]], "search_id": s["search_id"]})
    assert r.json()["added"] == 1
    # by DOI: checked first; a made-up title is refused
    r = client.post(L + "/papers", json={"text": "https://doi.org/10.1126/science.aaf0718"}).json()
    assert r["added"] and r["key"] == "Kuld16"
    r = client.post(L + "/papers", json={"title": "A paper nobody wrote", "authors": "Nobody"}).json()
    assert not r["added"] and r["check"]["status"] == "not_found"
    lit = client.get(L).json()
    assert [p["key"] for p in lit["papers"]] == ["Kuld16", "Behrens12"] and lit["searches"][0]["found"] == 2
    assert client.get(L + f"/searches/{s['search_id']}").json()["in_library"] == 2     # Kuld16 came in by DOI
    # BibTeX in and out
    bib = to_bibtex([{"key": "V", "paper": VASWANI}]) + '@article{fake, title={Weather effects on bike-share}, author={Smith}, year={2024}}'
    r = client.post(L + "/import", json={"bibtex": bib}).json()
    assert [(e["cite_key"], e["status"]) for e in r["entries"]] == [("V", "verified"), ("fake", "not_found")]
    out = client.get(L + "/export.bib").text
    assert "@misc{Vaswani17," in out and "Weather" not in out


async def test_deciding_about_a_flagged_reference(client, fake):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    lit = ProjectLiterature(cid, run_id="r1")
    await lit.check_text("[ref: Quantifying promotion of Cu catalysts by ZnO; Smith; 2016]", by="p01", place="Ideation")
    L = f"/api/campaigns/{cid}/literature"
    (ref,) = client.get(L).json()["refs"]
    assert ref["status"] == "not_found" and ref["closest"]["key"] == "Kuld16" and ref["used_in"] == ["Ideation"]
    r = client.post(L + f"/refs/{ref['ref_id']}", json={"action": "replace"}).json()
    assert r["refs"][0]["resolution"] == "replaced" and r["refs"][0]["replaced_by"] == "Kuld16"
    assert [p["key"] for p in r["papers"]] == ["Kuld16"] and r["counts"]["flagged"] == 0
    assert [e["key"] for e in lit.bibliography("r1")] == ["Kuld16"]
    assert client.post(L + f"/refs/{ref['ref_id']}", json={"action": "shrug"}).status_code == 400


def test_the_library_shows_your_papers_and_the_projects_you_can_see(client, fake):
    from aurora.auth.store import auth_store
    from tests.test_accounts import PW
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    client.post(f"/api/campaigns/{cid}/literature/papers", json={"doi": BEHRENS.doi})
    s = client.post("/api/library/search", json={"query": "attention need"}).json()
    assert [p["key"] for p in s["papers"]] == ["Vaswani17"]
    assert client.post("/api/library/save", json={"paper_id": s["papers"][0]["paper_id"]}).json()["added"]
    lib = client.get("/api/library").json()
    assert {(p["key"], p["mine"], len(p["projects"])) for p in lib["papers"]} == {("Vaswani17", True, 0),
                                                                                   ("Behrens12", False, 1)}
    # with accounts, someone outside the project sees neither its papers nor may add to it
    auth_store.add_user("sara@lab.example", "Sara Novak", PW, "admin")
    auth_store.adopt_unowned([cid], auth_store.user_by_email("sara@lab.example")["user_id"])
    auth_store.add_user("linda@lab.example", "Linda Fei", PW, "member")
    from aurora.api.app import create_app
    linda = TestClient(create_app())
    linda.post("/api/auth/login", json={"email": "linda@lab.example", "password": PW})
    assert linda.get("/api/library").json()["papers"] == []
    assert linda.get(f"/api/campaigns/{cid}/literature").status_code == 404
    assert linda.post("/api/library/save", json={"paper_id": library.paper("Behrens12")["paper_id"],
                                                 "campaign_id": cid}).status_code == 404


def test_the_import_rule_reaches_the_run(client):
    from aurora.api.routes.campaigns import StartExperimentRequest, _ensure_campaign, _experiment_config
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    cfg = _experiment_config(_ensure_campaign(cid), "run1", StartExperimentRequest(instruction="x", add_found="all"))
    assert cfg.literature.add_found == "all"
    s = client.get("/api/settings").json()
    assert s["literature"] == {"enabled": True, "add_found": "cited"}
    assert any(e["key"] == "literature.add_found" for e in s["editable"])


def test_search_results_are_ranked_by_what_they_share_with_the_query():
    from aurora.literature.indexes import rank
    on_topic = Paper(title="Station-level bike share demand and weather", authors=["A"], abstract="weather demand")
    partly = Paper(title="Bike share rebalancing", authors=["B"], abstract="station demand")
    off = Paper(title="ZnO nanorods for peroxide sensing", authors=["C"], abstract="a station")
    assert rank("station-level bike share demand weather", [off, partly, on_topic]) == [on_topic, partly]


async def test_screening_compares_with_prior_work_and_the_report_gets_real_references(fake, tmp_path):
    from aurora.config import AuroraConfig
    from aurora.core.engine import ResearchEngine
    from aurora.models.roundtable import RoundtableProposal
    from aurora.state.workspace import WorkspaceManager
    cfg = AuroraConfig(objective="x", campaign_id="run7", workspace={"root": str(tmp_path)})
    engine = ResearchEngine(cfg)
    engine.project_id = "proj"
    engine._workspace = WorkspaceManager(cfg.workspace, "run7")
    proposal = RoundtableProposal(proposal_id="p1", created_at="2026-10-21T00:00:00", title="Methanol synthesis on Zn-decorated Cu", description="d", hypothesis="h",
                                  references=["Behrens12"])
    prior = await engine._prior_work(proposal)
    assert "[Behrens12]" in prior and "Closest prior work" in prior and "The proposal itself cites: Behrens12" in prior
    assert [s["by"] for s in library.searches("proj")] == ["Screening", "Screening"]
    # the report: real references are listed, a made-up one is flagged and left out
    report = tmp_path / "draft_1.md"
    report.write_text("Zn sits at Cu steps [Behrens12] and promotes [ref: Quantifying the promotion of Cu catalysts by "
                      "ZnO for methanol synthesis; Kuld; 2016], unlike [ref: A universal zinc optimum; Smith; 2024].")
    await engine._reference_list(report)
    text = report.read_text()
    assert "## References" in text and "**[Behrens12]**" in text and "**[Kuld16]**" in text and "universal" not in text.split("## References")[1]
    assert "@article{Behrens12," in (tmp_path / "references.bib").read_text()
    assert "Kuld16" in engine._papers_brief() and "Behrens12" in engine._papers_brief()
    assert [r["status"] for r in library.refs("proj")] == ["not_found"]
    # literature off (or mock runs): nothing is searched or checked
    off = ResearchEngine(AuroraConfig(objective="x", campaign_id="r8", literature={"enabled": False}))
    assert off.literature is None and await off._prior_work(proposal) == ""
