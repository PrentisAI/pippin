"""Analysis updates the mental model, and later phases see the current understanding."""

from aurora.config import AuroraConfig
from aurora.core.engine import ResearchEngine
from aurora.core.lifecycle import IterationResult
from aurora.models.experiment import ExperimentJob, JobResult

VERDICT = {
    "sufficient": False, "target_phase": "ideation", "reason": "the main hypothesis failed",
    "findings": [
        {"statement": "Zn segregates to the clean surface", "status": "supported",
         "evidence": ["results/seg.csv"], "confidence": 0.8},
        {"statement": "CO reverses the segregation", "status": "refuted", "evidence": ["results/co.csv"]},
    ],
    "open_questions": ["What about formate?"],
    "mental_model": {
        "active_hypotheses": [
            {"hypothesis_id": "h1", "statement": "Adsorbates stabilise subsurface Zn", "status": "refuted",
             "confidence": 0.2, "contradicting_evidence": ["results/co.csv"]},
            {"hypothesis_id": "h2", "statement": "Formate pins Zn at the surface", "status": "competing",
             "confidence": 0.4},
        ],
        "inferred_mechanisms": ["strain relief"],
        "unresolved_ambiguity": ["coverage dependence"],
        "current_rationale": "Clean-surface segregation holds; the adsorbate story did not.",
    },
}


def make_engine(tmp_path):
    eng = ResearchEngine(AuroraConfig(objective="Zn in Cu(111)", mock_mode=True, campaign_id="u",
                                      workspace={"root": str(tmp_path)}))
    eng._setup()
    eng._store.initialize(objective="Zn in Cu(111)")
    return eng


async def test_analysis_updates_mental_model_findings_and_questions(tmp_path):
    eng = make_engine(tmp_path)
    events = []

    async def record(e):
        events.append(e.name)

    eng.event_bus.subscribe_all(record)
    await eng._apply_analysis(VERDICT)
    model = eng._mm.load()
    assert model.current_rationale.startswith("Clean-surface")
    assert [h.statement for h in model.competing_hypotheses] == ["Formate pins Zn at the surface"]
    artifacts = eng._store.load()
    assert [c.status for c in artifacts.claims] == ["accepted", "rejected"]
    assert artifacts.open_questions == ["What about formate?"]
    assert "mental_model_updated" in events

    text = eng._understanding_text()
    for needle in ("Clean-surface segregation holds", "[refuted, confidence 0.20]", "What about formate?",
                   "[rejected] CO reverses the segregation"):
        assert needle in text
    assert "Current understanding" in eng._campaign_brief(None)
    assert "Formate pins Zn" in eng._build_agent_context(artifacts).mental_model_summary


async def test_invalid_mental_model_keeps_the_previous_one(tmp_path):
    eng = make_engine(tmp_path)
    await eng._apply_analysis(VERDICT)
    await eng._apply_analysis({"sufficient": True,
                               "mental_model": {"active_hypotheses": [{"statement": "no id"}]}})
    assert eng._mm.load().current_rationale.startswith("Clean-surface")


def test_research_job_summary_becomes_the_observation(tmp_path):
    eng = make_engine(tmp_path)
    job = ExperimentJob(job_id="fit", skill_name="Fit the models")
    result = IterationResult(iteration_id="i1", job_id="fit", decision="done",
                             job_result=JobResult(job_id="fit", success=True,
                                                  summary="R2 = 0.97 on held-out data; see /app/fig.png"))
    eng._record_iteration_result(job, result)
    assert eng._store.load().observations[-1].description.startswith("R2 = 0.97")
    assert "R2 = 0.97" in eng._results_summary()


def test_web_search_defaults():
    assert AuroraConfig(objective="x").ideation.web_search is True
    assert AuroraConfig(objective="x", mock_mode=True).ideation.web_search is False
