"""Results with provenance: recording them, the report, taking it away, reproducing, comparing runs."""

import asyncio
import io
import tempfile
import time
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from aurora.results import report as rp
from aurora.results import results_client
from aurora.results.agent_service import ResultService
from aurora.results.ledger import ResultsLedger, result_refs

REPORT = """# Weekend demand at Citi Bike stations

We fit station-level models. The weekend elasticity is **-0.42** {R2} (an earlier fit gave {R1}),
and 61% of stations are leisure-dominated {R3}. This agrees with [Behrens12].

![Weekend demand by station type](figures/weekend.png)

| model | AIC |
|---|---|
| pooled | 1203 {R4} |

- one
- two

<script>alert(1)</script>

## References

- **[Behrens12]** Behrens ...
"""


def _workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / "analysis").mkdir(parents=True)
    (ws / "analysis" / "fit.py").write_text("print('fit')\n")
    (ws / "data").mkdir()
    (ws / "data" / "trips.csv").write_text("a,b\n1,2\n")
    (ws / "results").mkdir()
    (ws / "results" / "fit.csv").write_text("elasticity\n-0.42\n")
    return ws


def test_a_result_keeps_its_script_as_it_was(tmp_path):
    _workspace(tmp_path)
    led = ResultsLedger(tmp_path)
    r1 = led.record("weekend elasticity", "-0.37", script="analysis/fit.py", command="python analysis/fit.py",
                    inputs=["/app/data/trips.csv"], outputs=["results/fit.csv"], job="fit")
    (tmp_path / "workspace" / "analysis" / "fit.py").write_text("print('changed')\n")
    r2 = led.record("Weekend elasticity", -0.42, uncertainty=0.05, script="analysis/fit.py", job="fit")
    assert r1["id"] == "R1" and r2["id"] == "R2" and r2["supersedes"] == "R1"
    assert (tmp_path / r1["script"]["snapshot"]).read_text() == "print('fit')\n"
    assert r1["inputs"][0]["path"] == "data/trips.csv" and r1["inputs"][0]["exists"]
    assert [r["id"] for r in led.all(current=True)] == ["R2"]
    assert ResultsLedger.show_value(r2) == "-0.42 ± 0.05"
    assert "{R2} Weekend elasticity = -0.42 ± 0.05" in led.table()
    assert result_refs("a {R2} b {R1} c {R2}") == ["R2", "R1"]
    with pytest.raises(ValueError):
        led.record("", 1)
    bad = led.record("escape", 1, script="../../etc/passwd")
    assert bad["script"]["exists"] is False


async def test_the_agents_record_results_through_the_command(tmp_path, monkeypatch):
    _workspace(tmp_path)
    svc = ResultService(ResultsLedger(tmp_path), socket_dir=Path(tempfile.mkdtemp(prefix="aurora-r-")))
    await svc.start()
    monkeypatch.setenv("AURORA_RESULTS_SOCKET", str(svc.socket_path))
    monkeypatch.setenv("AURORA_AGENT_JOB", "fit-models")

    def cmd(*argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = results_client.main(["results", *argv])
        return code, out.getvalue() + err.getvalue()
    try:
        monkeypatch.chdir(tmp_path / "workspace" / "analysis")
        code, text = await asyncio.to_thread(cmd, "record", "weekend elasticity", "-0.42", "--unit", "",
                                             "--script", "fit.py", "--command", "python fit.py",
                                             "--outputs", "../results/fit.csv")
        assert code == 0 and "Recorded R1" in text and "{R1}" in text
        r = ResultsLedger(tmp_path).get("R1")
        assert r["job"] == "fit-models" and r["script"]["path"] == "analysis/fit.py" and r["outputs"][0]["exists"]
        code, text = await asyncio.to_thread(cmd, "record", "share leisure", "0.61")
        assert "cannot be traced" in text
        code, text = await asyncio.to_thread(cmd, "show", "R1")
        assert "script: analysis/fit.py (copied)" in text
        code, text = await asyncio.to_thread(cmd, "list")
        assert "{R2} share leisure" in text
    finally:
        await svc.stop()


def test_the_report_as_html_and_latex(tmp_path):
    ws = _workspace(tmp_path)
    led = ResultsLedger(tmp_path)
    for name, v in (("weekend elasticity", -0.37), ("weekend elasticity", -0.42), ("leisure share", 0.61)):
        led.record(name, v, script="analysis/fit.py", command="python analysis/fit.py")
    html = rp.render_html(REPORT, image_url=lambda src: "/img?x=" + src)
    assert '<button type="button" class="res-chip" data-result="R2">R2</button>' in html
    assert 'data-cite="Behrens12"' in html and "<script" not in html and "alert(1)" not in html
    assert '<img alt="Weekend demand by station type" src="/img?x=figures/weekend.png"' in html
    tex = rp.to_latex(REPORT, results=led.all(), figure_names={"figures/weekend.png": "weekend.png"})
    assert r"\title{Weekend demand at Citi Bike stations}" in tex
    assert r"\hyperref[res:R2]{R2}" in tex and r"\textsuperscript{R4?}" in tex
    assert r"\citep{Behrens12}" in tex and r"\includegraphics[width=0.8\linewidth]{figures/weekend.png}" in tex
    assert r"\textbf{-0.42}" in tex and r"61\%" in tex and r"\begin{itemize}" in tex
    assert r"\label{res:R2}R2 & weekend elasticity & -0.42" in tex
    assert "References" not in tex.split(r"\appendix")[0].split(r"\maketitle")[1]
    assert ws.exists()


def _run(c, cid, tmp_name="r1", report=REPORT, image="python:3.12-slim"):
    from aurora.api.routes.campaigns import ExperimentRunState, _ensure_campaign
    camp = _ensure_campaign(cid)
    run = ExperimentRunState(tmp_name, cid, f"run {tmp_name}", "m", "supervised")
    run.started_at = time.time() - 3600
    run.status = "completed"
    run.finished_at = time.time()
    root = run.campaign_dir
    _workspace(root)
    (root / "workspace" / "figures").mkdir()
    (root / "workspace" / "figures" / "weekend.png").write_bytes(b"\x89PNG fake")
    (root / "workspace" / ".aurora").mkdir()
    (root / "workspace" / ".aurora" / "environment.txt").write_text("Python 3.12.1\nnumpy==2.3.1\npandas==2.3.0\n")
    w = root / "phases" / "writing"
    w.mkdir(parents=True)
    (w / "draft_1.md").write_text(report.replace("figures/weekend.png", "../../workspace/figures/weekend.png"))
    (root / "config.yaml").write_text(yaml.safe_dump({"models": {"agent": "anthropic/claude-sonnet-5.5"},
                                                      "sandbox": {"image": image}, "skip_phases": ["ideation"]}))
    (root / "artifacts.json").write_text('{"claims": [{"statement": "Weekends differ", "status": "accepted"}]}')
    camp.experiments[tmp_name] = run
    return run


def test_report_export_reproduce_and_compare():
    from aurora.api.app import create_app
    c = TestClient(create_app())
    cid = c.post("/api/campaigns/quick", json={"prompt": "Citi Bike"}).json()["campaign_id"]
    a, b = _run(c, cid, "ra"), _run(c, cid, "rb", image="python:3.11-slim")
    for run, v in ((a, -0.42), (b, -0.30)):
        led = ResultsLedger(run.campaign_dir)
        led.record("weekend elasticity", v, script="analysis/fit.py", command="python analysis/fit.py",
                   outputs=["results/fit.csv"])
        led.record("leisure share", 0.61, command="python analysis/share.py")
    ResultsLedger(b.campaign_dir).record("AIC pooled", 1203, command="python analysis/fit.py --aic")
    rep = c.get(f"/api/campaigns/{cid}/experiments/ra/report").json()
    assert rep["title"] == "Weekend demand at Citi Bike stations" and 'data-result="R2"' in rep["html"]
    assert rep["figures"][0]["url"].endswith("path=workspace/figures/weekend.png")
    assert c.get(rep["figures"][0]["url"]).status_code == 200
    ov = rep["overview"]
    assert ov["models"]["agent"] == "anthropic/claude-sonnet-5.5" and ov["claims"][0]["statement"] == "Weekends differ"
    assert [r["id"] for r in ov["results"]] == ["R1", "R2"] and rep["environment"]
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/campaigns/{cid}/experiments/ra/export?what=report").content))
    names = set(z.namelist())
    assert {"report.md", "report.tex", "figures/weekend.png", "results.csv", "reproduce.sh", "README.txt",
            "provenance/R1/fit.py", "environment.txt", "workspace/.aurora/requirements-reproduce.txt"} <= names
    sh = z.read("reproduce.sh").decode()
    assert "IMAGE=python:3.12-slim" in sh and "run 'python analysis/fit.py'" in sh and "run 'python analysis/share.py'" in sh
    assert sh.index("analysis/fit.py") < sh.index("analysis/share.py")
    assert z.read("workspace/.aurora/requirements-reproduce.txt").decode() == "numpy==2.3.1\npandas==2.3.0\n"
    w = zipfile.ZipFile(io.BytesIO(c.get(f"/api/campaigns/{cid}/experiments/ra/export?what=workspace").content))
    assert "workspace/analysis/fit.py" in w.namelist() and "export/report.tex" in w.namelist()
    assert c.get(f"/api/campaigns/{cid}/experiments/ra/reproduce.sh").text.startswith("#!/usr/bin/env bash")
    cmp = c.get(f"/api/campaigns/{cid}/compare?runs=ra,rb").json()
    assert [r["run_id"] for r in cmp["runs"]] == ["ra", "rb"]
    row = next(x for x in cmp["results"] if x["name"] == "weekend elasticity")
    assert [v["value"] for v in row["values"]] == [-0.42, -0.30]
    aic = next(x for x in cmp["results"] if x["name"] == "AIC pooled")
    assert aic["values"][0] is None and aic["values"][1]["value"] == 1203
    assert cmp["runs"][1]["settings"]["image"] == "python:3.11-slim"
    # Overleaf: a git URL and a token are needed
    r = c.post(f"/api/campaigns/{cid}/experiments/ra/overleaf", json={"url": "https://example.com/x"})
    assert r.status_code == 400 and "git URL" in r.json()["detail"]
    r = c.post(f"/api/campaigns/{cid}/experiments/ra/overleaf", json={"url": "https://www.overleaf.com/project/6ab67ebabb74dfc9f2542687"})
    assert r.status_code == 400 and "token" in r.json()["detail"]


def test_overleaf_push_into_a_git_repo(tmp_path, monkeypatch):
    """The push itself, against a local bare repository standing in for Overleaf."""
    import subprocess
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "main.tex").write_text("paper")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD"], check=True)
    root = tmp_path / "run"
    _workspace(root)
    (root / "phases" / "writing").mkdir(parents=True)
    (root / "phases" / "writing" / "draft_1.md").write_text(REPORT)
    data = rp.export_zip(root, what="report", image="python:3.12", run_name="r", bib="@article{Behrens12,}")
    from aurora.api.routes import run_results
    # git cannot reach https here: the same push, with the URL pointing at the bare repository
    orig = subprocess.run

    def fake_run(args, **kw):
        args = [a.replace("https://git:tok@remote", str(bare)) for a in args]
        return orig(args, **kw)
    monkeypatch.setattr(run_results.subprocess, "run", fake_run)
    msg = run_results._push("https://remote", "tok", "aurora/r", data, ("Linda", "l@x"))
    assert "aurora/r/" in msg
    check = tmp_path / "check"
    subprocess.run(["git", "clone", "-q", str(bare), str(check)], check=True)
    assert (check / "aurora" / "r" / "report.tex").exists() and (check / "main.tex").read_text() == "paper"
    log = subprocess.run(["git", "-C", str(check), "log", "-1", "--format=%an"], capture_output=True, text=True).stdout
    assert log.strip() == "Linda"
    assert "Nothing changed" in run_results._push("https://remote", "tok", "aurora/r", data, ("Linda", "l@x"))
