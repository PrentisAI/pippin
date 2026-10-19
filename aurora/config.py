"""Configuration of an AURORA research campaign.

A campaign is configured by one YAML file (``aurora run --config ...``) or by
the web console.  Every section below maps to a block of that file; all fields
have defaults, so a minimal config only needs an ``objective``::

    objective: "Find out whether ..."

Unknown keys are rejected, so a typo fails at load time instead of being
silently ignored.  See ``docs/configuration.md`` for the full reference.
"""

from __future__ import annotations

import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AutonomyMode(str, Enum):
    """How the campaign handles a human gate (an escalation that needs a decision).

    ``supervised`` waits for the human; ``autonomous`` records the gate and
    continues on its own.  Switchable at runtime from the web console.
    """

    AUTONOMOUS = "autonomous"
    SUPERVISED = "supervised"


class PipelineStage(str, Enum):
    """Internal routing keys: which model answers a direct (non-agent) LLM call."""

    ROUNDTABLE_SEAT = "roundtable_seat"
    ROUNDTABLE_CHAIR = "roundtable_chair"
    IDEA_SCREENING = "idea_screening"
    RESEARCHER = "researcher"
    AUDITOR_TRIAGE = "auditor_triage"
    AUDITOR_DEEP = "auditor_deep"
    SKILLS = "skills"
    ANALYZER = "analyzer"
    WRITER = "writer"


# ---------------------------------------------------------------------------
# Default phase prompts
# ---------------------------------------------------------------------------

SCREENING_DEFAULT = (
    "You are an expert reviewer evaluating research proposals on three axes, "
    "each scored from 0 (worst) to 10 (best):\n\n"
    "1. **Novelty** — Does the proposal address a genuinely open question or "
    "propose a new methodology? Penalise incremental work or well-trodden ground.\n"
    "2. **Feasibility** — Can the proposed methods realistically deliver results "
    "within typical compute budgets and timelines? Consider code maturity, data "
    "availability, and methodological soundness.\n"
    "3. **Significance** — If successful, would the outcomes advance the field "
    "meaningfully? High significance means the results could change practice "
    "or unlock new research directions.\n\n"
    "Return a structured score with a brief explanation justifying each axis. "
    "Compute the composite score as:\n\n"
    "    composite = novelty * 0.35 + feasibility * 0.35 + significance * 0.30"
)

PLANNING_DEFAULT = (
    "You are an expert research planner. Given a research proposal, "
    "decompose it into a directed acyclic graph (DAG) of concrete executable jobs.\n\n"
    "Rules:\n"
    "1. Each job must map to a specific skill or tool from the available list.\n"
    "2. Specify dependencies between jobs using the job name field.\n"
    "3. Include all necessary parameters for each job invocation.\n"
    "4. Order jobs so that data flows forward (no cycles).\n"
    "5. Be as specific as possible about expected outputs and resource needs.\n"
    "6. Start with environment exploration — understand the workspace, "
    "available data, and constraints before planning computation.\n"
    "7. If no registered skill matches a required step, use the closest "
    "skill name and note the gap in the parameters."
)

PREFLIGHT_DEFAULT = (
    "You are the pre-flight checker of a research pipeline. A plan of jobs is about to run at full scale. "
    "Before that, check every job, so that problems surface now and cheaply, not hours into a run.\n\n"
    "For each job:\n"
    "1. Prerequisites. The inputs and data it needs exist or can be obtained (check paths, URLs, sizes, "
    "formats, schemas). The software it needs is installed or installable (import it, check versions). "
    "Its parameters are consistent with each other and with the proposal (units, ranges, settings that "
    "must agree; in a DFT study, for example, slab geometry and pseudopotentials).\n"
    "2. Smoke run. Run the job's critical steps on the smallest representative input (a small sample of "
    "the data, one iteration, a tiny system), under /app/preflight/<job_id>/, within a few minutes. It "
    "must exercise the real code path, not a toy. Measure time and memory, estimate the full run from "
    "them, and compare with the container's limits.\n"
    "3. If the job needs outputs of an earlier job that do not exist yet, build a minimal stand-in input "
    "if you can; otherwise mark the job untestable and say why.\n\n"
    "Fix what is quick to fix in the environment (install a package, fetch a small file) and say so. "
    "Anything that needs the plan changed is a problem. Do not run the full jobs. Keep the files you "
    "make for the checks under /app/preflight/; what you set up for real use (installed packages, "
    "downloaded data) may stay where the jobs expect it."
)

RESEARCH_DEFAULT = (
    "You are an autonomous research agent. You have direct access to "
    "computational tools and should use them to advance the research goal.\n\n"
    "## Methodology\n"
    "1. Read current state — understand what has been done and what remains\n"
    "2. Identify the most promising next step based on the goal and evidence\n"
    "3. Execute the step using available tools\n"
    "4. Analyze results — check correctness, consistency, anomalies\n"
    "5. Record observations with proper evidence citations\n"
    "6. Decide next action: continue, adjust approach, or declare done\n\n"
    "## Evidence Discipline\n"
    "- ALWAYS cite evidence by experiment/observation IDs\n"
    "- CLEARLY distinguish confirmed findings from unverified hypotheses\n"
    "- FLAG any contradictions with existing evidence\n"
    "- If a step fails, diagnose the issue and try a different approach\n"
    "- Keep tool calls efficient — don't repeat identical operations\n\n"
    "## Important\n"
    "- Read files before editing them\n"
    "- Run scripts after changes to verify correctness\n"
    "- Work methodically — fix one issue at a time and verify\n"
    "- When all objectives are met, signal completion"
)

ANALYSIS_DEFAULT = (
    "You are a senior research analyst. You perform rigorous analysis of "
    "experimental results and decide whether the research goal has been met.\n\n"
    "## Your Role\n"
    "1. **Cross-Validation** — Compare results across methods and configurations\n"
    "2. **Statistical Rigor** — Check sample sizes, compute confidence intervals, "
    "assess significance\n"
    "3. **Gap Identification** — Identify what is still missing or inconclusive\n"
    "4. **Progress Assessment** — Evaluate how much of the research goal has been achieved\n"
    "5. **Loop-back Decision** — Decide whether more work is needed, and if so, "
    "which pipeline phase to return to (research for more data, planning for a "
    "revised strategy, or screening to re-evaluate the approach)\n\n"
    "## Evidence Discipline\n"
    "- ALWAYS cite specific experiment IDs when discussing results\n"
    "- ALWAYS report numerical values with appropriate precision and units\n"
    "- FLAG any result that contradicts expected baselines\n"
    "- Distinguish confirmed findings from hypotheses\n\n"
    "## Output\n"
    "Provide structured analysis with: findings summary, quality concerns, "
    "confidence assessment, and whether follow-up is needed (and to which phase)."
)

WRITING_DEFAULT = (
    "You are a technical writer. You produce clear, well-structured reports "
    "based on completed research.\n\n"
    "## Your Role\n"
    "1. **Structure** — Organize findings into a logical narrative\n"
    "2. **Precision** — Use quantitative language with proper units and uncertainties\n"
    "3. **Evidence** — Reference all claims by experiment/observation IDs\n"
    "4. **Completeness** — Ensure all key findings are covered, gaps are noted\n"
    "5. **Clarity** — Write for a knowledgeable reader; avoid unnecessary jargon\n\n"
    "## Output Format\n"
    "Produce a structured report with: executive summary, methodology, "
    "results, discussion, and conclusions."
)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

PHASE_AGENTS = ("screening", "planning", "preflight", "research", "analysis", "writing")


class ModelConfig(_Section):
    """Which model plays each role.

    Model IDs use OpenRouter's ``vendor/model`` form, so one OpenRouter key
    covers every role.  The defaults are the models used in the paper.
    """

    agent: str = Field(
        default="anthropic/claude-sonnet-5.5",
        description="Model of the Claude Code sessions that run screening, planning, "
                    "research, analysis and writing, unless a phase sets its own below.",
    )
    screening: str = Field(default="", description="Model of the screening agent. Empty: models.agent.")
    planning: str = Field(default="", description="Model of the planning agent. Empty: models.agent.")
    preflight: str = Field(default="", description="Model of the pre-flight agent. Empty: models.agent.")
    research: str = Field(default="", description="Model of the research agent. Empty: models.agent.")
    analysis: str = Field(default="", description="Model of the analysis agent. Empty: models.agent.")
    writing: str = Field(default="", description="Model of the writing agent. Empty: models.agent.")
    ideation_seats: list[str] = Field(
        default=["anthropic/claude-sonnet-5.5", "openai/gpt-5.6-sol", "google/gemini-3.1-pro-preview"],
        description="One model per ideation seat. Heterogeneous models give more diverse ideas.",
    )
    ideation_chair: str = Field(
        default="moonshotai/kimi-k3",
        description="Model of the ideation chair, who decides the research direction. "
                    "Pick a family not among the seats so it judges the ideas neutrally.",
    )
    auditor: str = Field(
        default="anthropic/claude-sonnet-5.5",
        description="Model of the milestone auditor that reviews each phase's output.",
    )
    skills: str = Field(
        default="",
        description="Model that decides what to keep as skills: it drafts skills from the agents' runs and "
                    "from your corrections. Empty: models.auditor.",
    )
    supervisor: str = Field(
        default="anthropic/claude-sonnet-5.5",
        description="Model of the live auditor (supervisor) that watches the research agent.",
    )
    frontdesk: str = Field(
        default="anthropic/claude-haiku-5.5",
        description="Model of the front desk agent that talks with the human.",
    )

    def for_phase(self, phase: str) -> str:
        """Model of the agent that runs *phase* (screening, planning, research, analysis, writing)."""
        own = getattr(self, phase, "") if phase in PHASE_AGENTS else ""
        return own or self.agent

    def get_model(self, stage: PipelineStage, seat_index: int = 0) -> str:
        """Model for a direct LLM call routed by *stage*."""
        if stage == PipelineStage.ROUNDTABLE_SEAT:
            return self.ideation_seats[seat_index % len(self.ideation_seats)]
        if stage == PipelineStage.ROUNDTABLE_CHAIR:
            return self.ideation_chair
        if stage in (PipelineStage.AUDITOR_TRIAGE, PipelineStage.AUDITOR_DEEP):
            return self.auditor
        if stage == PipelineStage.SKILLS:
            return self.skills or self.auditor
        return self.for_phase({PipelineStage.IDEA_SCREENING: "screening", PipelineStage.RESEARCHER: "research",
                               PipelineStage.ANALYZER: "analysis", PipelineStage.WRITER: "writing"}.get(stage, ""))


class ProviderConfig(_Section):
    """Credentials for one LLM provider, for models not routed through OpenRouter."""

    provider: str = Field(description="openrouter | anthropic | openai | google | deepseek | mock")
    api_key: str = Field(default="", description="API key. Empty: read the provider's usual environment variable.")
    base_url: str | None = Field(default=None, description="Optional endpoint override.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Provider-specific options.")


class SeatConfig(_Section):
    """One ideation seat with its own name, model and instructions."""

    name: str = Field(description="Display name, e.g. 'Theorist'.")
    model: str = Field(default="", description="Model ID. Empty: take it from models.ideation_seats.")
    prompt: str = Field(default="", description="System prompt for this seat. Empty: ideation.seat_prompt.")


class IdeationConfig(_Section):
    """Multi-agent ideation: several models propose directions and a chair decides."""

    mode: Literal["roundtable", "independent"] = Field(
        default="roundtable",
        description="'roundtable': the seats present their ideas and discuss them in a group "
                    "meeting before the chair decides. 'independent': each seat proposes an idea "
                    "on its own and the chair picks one, without discussion (cheaper).",
    )
    seats: list[SeatConfig] = Field(
        default_factory=list,
        description="Named seats with their own prompts. Empty: one seat per models.ideation_seats.",
    )
    rounds_per_item: int = Field(
        default=2, ge=1,
        description="Roundtable mode: remark/response exchanges per presentation.",
    )
    web_search: bool = Field(
        default=True,
        description="Let seats search the literature and the web while preparing and answering "
                    "(OpenRouter web plugin, a small extra cost per call; seat models must be OpenRouter "
                    "models). Turn it off when evaluating ideation on known problems, so ideas cannot "
                    "simply be looked up.",
    )
    chair_prompt: str = Field(default="", description="System prompt of the chair. Empty: built-in.")
    seat_prompt: str = Field(
        default="",
        description="System prompt of the seats. Empty: built-in. '{seat_id}' is replaced by the seat ID.",
    )


class AgentConfig(_Section):
    """Limits of the Claude Code sessions that do the work."""

    research_max_turns: int = Field(default=200, ge=1, description="Turn limit of one research session.")
    research_timeout_minutes: float = Field(default=60, gt=0, description="Time limit of one research job.")
    phase_max_turns: int = Field(
        default=30, ge=1,
        description="Turn limit of the screening, planning, analysis and writing sessions.",
    )
    preflight_timeout_minutes: float = Field(
        default=20, gt=0, description="Time limit of the pre-flight checks of a plan (all its jobs).",
    )
    preflight_max_revisions: int = Field(
        default=2, ge=0,
        description="How often planning revises the plan when pre-flight finds problems, before you decide.",
    )
    max_parallel_jobs: int = Field(
        default=1, ge=1, le=16,
        description="Research jobs that may run at the same time, each with its own agent, when the plan's "
                    "dependencies allow it. They share the container (CPUs, memory, /app).",
    )
    max_research_iterations: int = Field(
        default=10, ge=1, description="Maximum research jobs executed in one research cycle.",
    )
    max_followup_cycles: int = Field(
        default=2, ge=0,
        description="How often analysis may send the campaign back for more research.",
    )


class SkillsConfig(_Section):
    """How the skill library grows (Section 3.1, Appendix B)."""

    distill: bool = Field(
        default=True,
        description="Self-distillation: after each phase and research job, propose new skills (or "
                    "revisions) from procedures that worked and failures that were diagnosed.",
    )
    from_feedback: bool = Field(
        default=True,
        description="Turn the researcher's reusable corrections and conventions into skills on their "
                    "first occurrence.",
    )
    auto_accept: bool = Field(
        default=False,
        description="Add proposed skills to the library at once. Off: each one waits for you in the web "
                    "console (add to the library, to this project only, or dismiss).",
    )


class SandboxConfig(_Section):
    """Where the agents' commands run."""

    docker: bool = Field(
        default=True,
        description="Run every command in one Docker container per campaign. Without Docker the "
                    "agents can only read and edit files in the workspace (see allow_host_bash).",
    )
    image: str = Field(default="python:3.11-slim", description="Docker image of the campaign container.")
    cpus: float = Field(default=4.0, gt=0, description="CPU limit of the container.")
    memory_mb: int = Field(default=8192, gt=0, description="Memory limit of the container in MB.")
    network: bool = Field(default=True, description="Allow internet access from the container.")
    agent_in_container: bool = Field(
        default=False,
        description="Experimental: run research agents inside the container, with full freedom there. "
                    "The API key stays in a small proxy container; the campaign container gets a random "
                    "token. Needs mount_workspace and an image with glibc (not Alpine).",
    )
    mount_workspace: bool = Field(
        default=True,
        description="Mount <campaign>/workspace as the container's /app, so the agents' files are on "
                    "the host as they are written (no copying). Turn off if Docker runs on another machine.",
    )
    allow_host_bash: bool = Field(
        default=False,
        description="With docker: false, let agents run shell commands directly on this machine. "
                    "Only for trusted tasks on a disposable machine.",
    )
    tests_dir: str = Field(
        default="",
        description="Optional acceptance tests (a directory with test.sh or pytest files) run in the "
                    "container after each research job; a job counts as done when they pass.",
    )


class AuditConfig(_Section):
    """The two auditors (Section 3.3 of the paper)."""

    milestone: bool = Field(
        default=True,
        description="Review every phase hand-off. Verdicts: pass, reject (the phase revises its "
                    "output) or escalate (a human gate).",
    )
    milestone_max_retries: int = Field(
        default=2, ge=0, description="Revisions a rejected phase gets before the gate escalates to the human.",
    )
    live: bool = Field(
        default=True,
        description="Run the live auditor (supervisor) next to the research agent. It can let the "
                    "agent continue, advise it, interrupt it, or escalate to the human.",
    )


class HumanConfig(_Section):
    """How the campaign involves the human."""

    autonomy: AutonomyMode = Field(
        default=AutonomyMode.SUPERVISED,
        description="supervised: escalations wait for a human decision. autonomous: they are "
                    "recorded and the campaign continues.",
    )
    frontdesk: bool = Field(
        default=True,
        description="Run the front desk agent, which answers questions about the running campaign "
                    "and relays instructions to the research agent (duplex interaction).",
    )
    gate_timeout_minutes: float = Field(
        default=60, gt=0, description="How long a human gate waits before taking on_timeout.",
    )
    on_timeout: Literal["continue", "stop"] = Field(
        default="continue", description="What a human gate does when nobody answers in time.",
    )
    approve_termination: bool = Field(
        default=False, description="Ask the human before the campaign finishes.",
    )


class BudgetConfig(_Section):
    """Hard limits; the campaign stops when one is reached."""

    max_hours: float = Field(default=24, gt=0, description="Wall-clock limit of the whole campaign.")
    max_cost_usd: float | None = Field(
        default=None, gt=0,
        description="Limit on the API cost reported by the agent sessions. None: no limit.",
    )


class WorkspaceConfig(_Section):
    """Where the campaign writes its files."""

    root: str = Field(
        default="./workspace",
        description="Directory under which the campaign directory <root>/<campaign_id> is created.",
    )


class PromptConfig(_Section):
    """System prompts of the phase agents. Override one to change how a phase works."""

    screening: str = Field(
        default=SCREENING_DEFAULT,
        description="Screening: scores the proposal for novelty, feasibility and significance.",
    )
    planning: str = Field(default=PLANNING_DEFAULT, description="Planning: turns the proposal into jobs.")
    preflight: str = Field(default=PREFLIGHT_DEFAULT,
                           description="Pre-flight: checks prerequisites and smoke-runs each job before research.")
    research: str = Field(default=RESEARCH_DEFAULT, description="Research: executes the jobs.")
    analysis: str = Field(
        default=ANALYSIS_DEFAULT,
        description="Analysis: checks the results and decides whether more research is needed.",
    )
    writing: str = Field(default=WRITING_DEFAULT, description="Writing: produces the final report.")


PHASES = ("ideation", "screening", "planning", "preflight", "research", "analysis", "writing")

# ---------------------------------------------------------------------------
# Top-level configuration
# ---------------------------------------------------------------------------

class AuroraConfig(_Section):
    """Complete configuration of one research campaign."""

    objective: str = Field(description="The open-ended research objective, in plain language.")
    campaign_id: str = Field(
        default="",
        description="Name of the campaign directory. Empty: derived from the objective and the time.",
    )
    mock_mode: bool = Field(
        default=False,
        description="Run offline with scripted mock models and tools: no API key, no Claude Code, "
                    "no Docker. The mock models replay a scripted materials-science study whatever "
                    "the objective; use it to try the pipeline and in tests.",
    )
    inputs: list[str] = Field(
        default_factory=list,
        description="Files or directories the agents should work with (data, code, papers). They are "
                    "copied to inputs/ in the agents' working directory (/app/inputs in the container).",
    )
    skills: SkillsConfig = Field(default_factory=lambda: SkillsConfig())
    skills_dirs: list[str] = Field(
        default_factory=list,
        description="Directories of Claude Code skills (<name>/SKILL.md) written by you; every agent "
                    "session can use them, next to the skills in the skill library.",
    )
    skip_phases: list[str] = Field(
        default_factory=list,
        description=f"Phases to skip, any of {list(PHASES)}.",
    )
    api_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter-compatible endpoint used by all agents.",
    )
    api_key: str = Field(
        default="",
        description="API key for api_base_url. Empty: OPENROUTER_API_KEY from the environment or .env.",
    )

    models: ModelConfig = Field(default_factory=ModelConfig)
    ideation: IdeationConfig = Field(default_factory=IdeationConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    human: HumanConfig = Field(default_factory=HumanConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    providers: list[ProviderConfig] = Field(
        default_factory=list,
        description="Extra providers for models outside OpenRouter (advanced).",
    )

    @model_validator(mode="after")
    def _finalize(self) -> AuroraConfig:
        unknown = set(self.skip_phases) - set(PHASES)
        if unknown:
            raise ValueError(f"skip_phases has unknown phases {sorted(unknown)}; valid: {list(PHASES)}")
        if not self.campaign_id:
            slug = re.sub(r"[^a-z0-9]+", "-", self.objective.lower()).strip("-")[:40].rstrip("-")
            self.campaign_id = f"{slug or 'campaign'}-{time.strftime('%Y%m%d-%H%M%S')}"
        if self.mock_mode:
            self._apply_mock_mode()
        return self

    def _apply_mock_mode(self) -> None:
        """Every role uses a scripted mock model and nothing external runs."""
        n_seats = max(len(self.models.ideation_seats), len(self.ideation.seats), 1)
        self.models = ModelConfig(
            agent="mock-agent",
            ideation_seats=[f"mock-seat-{i}" for i in range(n_seats)],
            ideation_chair="mock-chair",
            auditor="mock-auditor",
            supervisor="mock-supervisor",
            frontdesk="mock-frontdesk",
        )
        self.ideation.seats = [sc.model_copy(update={"model": ""}) for sc in self.ideation.seats]
        self.ideation.web_search = False
        self.sandbox.docker = False
        self.audit.live = False
        self.human.frontdesk = False

    @property
    def api_key_value(self) -> str:
        """The key for api_base_url: the config value, else OPENROUTER_API_KEY."""
        import os
        return self.api_key or os.environ.get("OPENROUTER_API_KEY", "")

    @property
    def claude_code_base_url(self) -> str:
        """Base URL for Claude Code (Anthropic-compatible endpoint, without /v1)."""
        url = self.api_base_url.rstrip("/")
        return url[: -len("/v1")] if url.endswith("/v1") else url


def load_config(path: str | Path, **overrides: Any) -> AuroraConfig:
    """Load and validate a campaign configuration from a YAML file.

    Keyword *overrides* replace top-level keys of the file (used by the CLI).
    Raises ``FileNotFoundError`` for a missing file and ``pydantic.ValidationError``
    for invalid content.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    raw.update({k: v for k, v in overrides.items() if v is not None})
    _resolve_relative_paths(raw, path.resolve().parent)
    return AuroraConfig.model_validate(raw)


def _resolve_relative_paths(raw: dict[str, Any], base: Path) -> None:
    """Paths written in a config file are relative to that file, like in docker-compose."""
    def resolve(value: str) -> str:
        p = Path(value).expanduser()
        return str(p if p.is_absolute() else (base / p).resolve())

    for key in ("inputs", "skills_dirs"):
        if isinstance(raw.get(key), list):
            raw[key] = [resolve(v) for v in raw[key]]
    sandbox = raw.get("sandbox")
    if isinstance(sandbox, dict) and sandbox.get("tests_dir"):
        sandbox["tests_dir"] = resolve(sandbox["tests_dir"])
    workspace = raw.get("workspace")
    if isinstance(workspace, dict) and workspace.get("root"):
        workspace["root"] = resolve(workspace["root"])
