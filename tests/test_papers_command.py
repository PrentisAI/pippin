"""`aurora papers`: the research agents search the literature, check references and read papers."""

import asyncio
import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from aurora.literature import indexes as indexes_mod
from aurora.literature import papers_client
from aurora.literature.agent_service import PaperService, parse_citation
from aurora.literature.library import library
from aurora.literature.service import ProjectLiterature
from tests.test_literature import FakeIndexes


def test_citations_as_agents_type_them():
    assert parse_citation("10.1126/science.1219831").doi == "10.1126/science.1219831"
    assert parse_citation("arXiv:1706.03762v2").arxiv_id == "1706.03762"
    r = parse_citation("Behrens et al. (2012). The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts. Science")
    assert r.title.startswith("The active site of methanol synthesis") and r.year == 2012 and "Behrens" in r.authors
    r = parse_citation("Behrens, Studt, Kasatkin et al. The Active Site of Methanol Synthesis over Cu/ZnO/Al2O3 "
                       "Industrial Catalysts. Science 2012")
    assert r.title == "The Active Site of Methanol Synthesis over Cu/ZnO/Al2O3 Industrial Catalysts"
    assert r.authors.startswith("Behrens") and r.year == 2012
    r = parse_citation("Kuld S, Thorhauge M (2016) Quantifying the promotion of Cu catalysts by ZnO for methanol synthesis")
    assert r.title.startswith("Quantifying the promotion") and r.year == 2016


@pytest.fixture()
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    sockets = Path(tempfile.mkdtemp(prefix="aurora-t-"))          # Unix socket paths are short
    lit = ProjectLiterature("p_test", run_id="r1", rule="cited")
    files = tmp_path / "files"
    (files / "papers").mkdir(parents=True)
    svc = PaperService(lit, socket_dir=sockets, workspace_dir=tmp_path / "workspace", project_files=files)
    monkeypatch.setenv("AURORA_PAPERS_SOCKET", str(svc.socket_path))
    monkeypatch.setenv("AURORA_AGENT_JOB", "job_1")
    return svc


def papers(*argv):
    """Run `aurora papers ...` as the agent would; (exit code, what it printed)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = papers_client.main(["papers", *argv])
    return code, out.getvalue() + err.getvalue()


async def test_search_check_show_cite_and_bib(service):
    await service.start()
    try:
        run = lambda *a: asyncio.to_thread(papers, *a)  # noqa: E731  (the client blocks)
        code, text = await run("search", "methanol", "synthesis")
        assert code == 0 and "[Behrens12]" in text and "[Kuld16]" in text
        s = library.searches("p_test")[0]
        assert s["by"] == "Research agent (job_1)" and s["query"] == "methanol synthesis"
        assert library.items("p_test") == []                       # rule "cited": found is not cited

        code, text = await run("check", "10.1126/science.1219831", "Wang et al. 2021. A made-up paper on zinc steps")
        assert code == 0 and "✓ real: [Behrens12]" in text and "✗" in text and "NOT FOUND" in text

        code, text = await run("show", "Behrens12")
        assert code == 0 and "https://doi.org/10.1126/science.1219831" in text and "Not in the project's literature" in text

        code, text = await run("cite", "Behrens12")
        assert code == 0 and "recorded" in text
        assert [i["paper_id"] for i in library.items("p_test")] == [library.paper("Behrens12")["paper_id"]]
        assert any(u["place"] == "Research" for u in library.uses("p_test", "r1"))

        code, text = await run("list")
        assert "1 papers" in text and "[Behrens12]" in text
        code, text = await run("bib", "Behrens12")
        assert "@article{Behrens12" in text or "Behrens12," in text

        code, text = await run("show", "Nobody99")
        assert code == 1 and "no paper" in text
    finally:
        await service.stop()


async def test_a_pdf_from_the_project_files(service):
    await service.start()
    try:
        await asyncio.to_thread(papers, "search", "methanol")
        (service.project_files / "papers" / "Behrens12.pdf").write_bytes(b"%PDF-1.4 fake")
        code, text = await asyncio.to_thread(papers, "pdf", "Behrens12")
        assert code == 0 and "papers/Behrens12.pdf" in text and "the project's files" in text
        assert (service.workspace_dir / "papers" / "Behrens12.pdf").read_bytes().startswith(b"%PDF")
    finally:
        await service.stop()


def test_without_the_service_it_says_so(monkeypatch):
    monkeypatch.delenv("AURORA_PAPERS_SOCKET", raising=False)
    code, text = papers("list")
    assert code == 1 and "not enabled" in text


def test_the_agents_are_told_and_allowed(service):
    assert "aurora papers search" in service.agent_prompt()
    assert service.allowed_tools == ["Bash(aurora papers *)"]
    assert service.agent_env(in_container=True) == {"AURORA_PAPERS_SOCKET": "/aurora/run/papers.sock", "AURORA_PAPERS_APP": "/app"}


def test_vancouver_names_and_a_title_the_project_has_seen(monkeypatch):
    from aurora.literature.records import family_name
    assert family_name("Behrens M") == "behrens" and family_name("Studt FJ") == "studt"

    class Unhelpful(FakeIndexes):              # the indexes do not put the paper first for a bare title
        async def look_up(self, title, authors="", year=None):
            return self._found([])
    monkeypatch.setattr(indexes_mod, "_shared", Unhelpful())
    lit = ProjectLiterature("p_seen")

    async def go():
        await lit.search("methanol synthesis", by="Agent")
        ok = await lit.check_reference(parse_citation(
            "Behrens M, Studt F, Kasatkin I, et al. The active site of methanol synthesis over Cu/ZnO/Al2O3 "
            "industrial catalysts. Science. 2012;336:893-897"))
        wrong_year = await lit.check_reference(parse_citation(
            "Behrens M. The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts. 2019"))
        return ok, wrong_year
    ok, wrong_year = asyncio.run(go())
    assert ok.verified and ok.paper.doi == "10.1126/science.1219831"
    assert not wrong_year.verified


async def test_crossref_is_also_asked_with_formulas_apart():
    import httpx

    from aurora.literature.indexes import Indexes
    asked = []

    def web(request):
        q = request.url.params.get("query.bibliographic", "")
        asked.append(q)
        items = [{"DOI": "10.1126/science.1219831", "title": ["The Active Site of Methanol Synthesis over Cu/ZnO/Al <sub>2</sub> "
                  "O <sub>3</sub> Industrial Catalysts"], "author": [{"family": "Behrens", "given": "Malte"}],
                  "issued": {"date-parts": [[2012]]}}] if "Al 2 O 3" in q else []
        return httpx.Response(200, json={"message": {"items": items}})
    idx = Indexes(client=httpx.AsyncClient(transport=httpx.MockTransport(web)), use=("crossref",))
    found = await idx.look_up("The Active Site of Methanol Synthesis over Cu/ZnO/Al2O3 Industrial Catalysts")
    assert any("Al 2 O 3" in q for q in asked)
    assert found.papers[0].doi == "10.1126/science.1219831"


async def test_a_run_gives_its_agents_the_command(tmp_path, monkeypatch):
    from aurora.config import AuroraConfig
    from aurora.core.engine import ResearchEngine
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    eng = ResearchEngine(AuroraConfig(objective="x", campaign_id="c", workspace={"root": str(tmp_path)},
                                      sandbox={"docker": False}))
    eng._setup()
    await eng._start_papers()
    try:
        extras = eng._agent_extras()
        assert "Bash(aurora papers *)" in extras["extra_tools"] and "AURORA_PAPERS_SOCKET" in extras["extra_env"]
        assert "aurora papers search" in extras["extra_prompt"]
        assert eng._phase_extras("writing")["extra_env"]["AURORA_PAPERS_APP"].endswith("workspace")
        assert eng._phase_extras("audit") == {}
        monkeypatch.setenv("AURORA_PAPERS_SOCKET", extras["extra_env"]["AURORA_PAPERS_SOCKET"])
        code, text = await asyncio.to_thread(papers, "search", "methanol")
        assert code == 0 and "[Behrens12]" in text
    finally:
        await eng.papers.stop()
    mock = ResearchEngine(AuroraConfig(objective="x", mock_mode=True, campaign_id="m", workspace={"root": str(tmp_path / "m")}))
    mock._setup()
    await mock._start_papers()
    assert mock.papers is None and mock._agent_extras() == {}
