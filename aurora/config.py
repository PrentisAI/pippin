"""Global configuration for a AURORA research campaign.

Configuration is loaded from a YAML file and validated into frozen Pydantic
models.  Every pipeline stage is mapped to a concrete LLM model identifier
so that model routing is centralised and agents never instantiate providers
directly.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Pipeline stages — each maps to a specific LLM model in the assignment
# ---------------------------------------------------------------------------

class AutonomyMode(str, Enum):
    """Controls how much human interaction a running campaign requests.

    Switchable at runtime so the operator can adjust without restarting.
    """

    AUTONOMOUS = "autonomous"
    SUPERVISED = "supervised"
    COLLABORATIVE = "collaborative"


class PipelineStage(str, Enum):
    """Stages of the AURORA ideation-to-paper pipeline.

    Model assignments for each stage are drawn from Table 6 of the paper.
    """

    ROUNDTABLE_SEAT = "roundtable_seat"
    ROUNDTABLE_CHAIR = "roundtable_chair"
    IDEA_SCREENING = "idea_screening"
    RESEARCHER = "researcher"
    AUDITOR_TRIAGE = "auditor_triage"
    AUDITOR_DEEP = "auditor_deep"
    ANALYZER = "analyzer"
    WRITER = "writer"


# ---------------------------------------------------------------------------
# Configuration sections
# ---------------------------------------------------------------------------

class LLMModelAssignment(BaseModel):
    """Maps each pipeline stage to a concrete model identifier.

    Defaults correspond to the model versions reported in the paper (Table 6).
    """

    roundtable_seat: list[str] = Field(
        default=[
            "claude-opus-4-8",
            "gemini-3.1-pro-preview",
            "gpt-5.5",
            "deepseek-v4-pro",
        ],
        description="Models powering the heterogeneous roundtable seats.",
    )
    roundtable_chair: str = Field(
        default="deepseek-v4-flash",
        description="Model used by the roundtable chair for convergence.",
    )
    idea_screening: str = Field(
        default="gpt-4o-mini",
        description="Cheap model for fast idea screening.",
    )
    researcher: str = Field(
        default="claude-opus-4-8",
        description="Primary model for the researcher agent.",
    )
    auditor_triage: str = Field(
        default="claude-haiku-4-5",
        description="Lightweight model for audit triage.",
    )
    auditor_deep: str = Field(
        default="claude-opus-4-8",
        description="Full model for deep audits.",
    )
    analyzer: str = Field(
        default="claude-opus-4-8",
        description="Model for the analyzer agent.",
    )
    writer: str = Field(
        default="claude-sonnet-4-5",
        description="Model for the writer agent.",
    )

    def get_model(self, stage: PipelineStage, seat_index: int = 0) -> str:
        """Return the model identifier for *stage*.

        For ``ROUNDTABLE_SEAT`` the *seat_index* selects among the configured
        seat models (cycling if the index exceeds the list length).
        """
        if stage == PipelineStage.ROUNDTABLE_SEAT:
            return self.roundtable_seat[seat_index % len(self.roundtable_seat)]
        return getattr(self, stage.value)


class WorkspaceConfig(BaseModel):
    """Workspace directory layout and policies."""

    workspace_root: str = Field(
        default="./workspace",
        description="Root directory for campaign artifacts.",
    )
    no_delete_policy: bool = Field(
        default=True,
        description=(
            "When True, raw outputs and analysis artifacts are never "
            "overwritten or deleted without an explicit backup."
        ),
    )


class ResourceLimits(BaseModel):
    """Compute and token budget constraints (Section H.3)."""

    max_wall_clock_hours: float = Field(
        default=120.0,
        description="Maximum wall-clock hours for the entire campaign.",
    )
    max_gpu_hours: float = Field(
        default=1000.0,
        description="Maximum GPU-hours budget.",
    )
    monthly_token_budget: int = Field(
        default=10_000_000,
        description="Monthly token budget across all LLM providers.",
    )
    per_cell_wall_time_minutes: int = Field(
        default=120,
        description="Wall-time cap per individual simulation cell.",
    )
    max_concurrent_simulations: int = Field(
        default=16,
        description="Maximum parallel simulation jobs.",
    )


class HumanCheckpointConfig(BaseModel):
    """Controls when human-in-the-loop approval is required."""

    require_approval_for: list[str] = Field(
        default=[
            "proposal_approval",
            "goal_change",
            "termination",
            "validation_sub_campaign",
        ],
        description="Events that pause for human approval.",
    )
    auto_approve_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "If set, auto-approve after this many seconds when running "
            "in unattended mode.  None means wait indefinitely."
        ),
    )


class LLMProviderConfig(BaseModel):
    """API key and endpoint configuration for a single LLM provider."""

    provider: str = Field(description="Provider name: anthropic | openai | gemini | deepseek")
    api_key: str = Field(default="", description="API key (can also be set via env var).")
    base_url: str | None = Field(default=None, description="Optional base URL override.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Provider-specific options.")


class RoundtableSeatConfig(BaseModel):
    """Configuration for a single roundtable participant."""

    name: str = Field(description="Display name for this seat (e.g. 'Domain Expert').")
    model: str = Field(
        default="",
        description="Model identifier. Empty = use default from LLMModelAssignment.",
    )
    prompt: str = Field(
        default="",
        description="Custom system prompt for this seat. Empty = use roundtable_seat_prompt.",
    )


# ---------------------------------------------------------------------------
# Top-level campaign configuration
# ---------------------------------------------------------------------------

class AuroraConfig(BaseModel):
    """Complete configuration for a single AURORA research campaign."""

    campaign_id: str = Field(description="Unique identifier for this campaign.")
    objective: str = Field(description="The open-ended research objective (Section H.1).")
    domain: str = Field(
        default="computational_materials_science",
        description="Scientific domain tag.",
    )
    domain_context: str = Field(
        default="",
        description=(
            "Domain-specific context injected into agent system prompts. "
            "For example, quality criteria, evaluation standards, or "
            "domain vocabulary. When empty, agents use generic prompts."
        ),
    )

    # Sub-configurations
    llm_assignment: LLMModelAssignment = Field(default_factory=LLMModelAssignment)
    llm_providers: list[LLMProviderConfig] = Field(
        default_factory=list,
        description="API credentials for each LLM provider.",
    )
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    human_checkpoints: HumanCheckpointConfig = Field(default_factory=HumanCheckpointConfig)

    # Pipeline parameters
    max_followup_cycles: int = Field(
        default=3,
        description="Maximum writer-triggered follow-up research cycles.",
    )
    max_roundtable_rounds: int = Field(
        default=5,
        description="Unused by the group-meeting roundtable (see roundtable_item_rounds); kept for compatibility.",
    )
    roundtable_item_rounds: int = Field(
        default=2,
        description="Maximum remark/response exchanges per agenda item (presentation) in the roundtable.",
    )
    roundtable_web_search: bool = Field(
        default=False,
        description=("Let roundtable seats search the web when preparing and when answering remarks on their own work "
                     "(OpenRouter web plugin, model-native engine; requires OpenRouter seat models)."),
    )
    num_roundtable_seats: int = Field(
        default=4,
        description=(
            "Number of heterogeneous seats in the roundtable. "
            "Ignored when roundtable_seats is non-empty."
        ),
    )
    roundtable_seats: list[RoundtableSeatConfig] = Field(
        default_factory=list,
        description=(
            "Per-seat configuration (name, model, prompt). "
            "When non-empty, overrides num_roundtable_seats and "
            "roundtable_seat_prompt. Each seat can have its own "
            "model and system prompt."
        ),
    )
    max_research_iterations: int = Field(
        default=50,
        description="Safety cap on research iterations within a single cycle.",
    )
    use_real_tools: bool = Field(
        default=False,
        description="Use real tool implementations when available.",
    )
    use_agentic_researcher: bool = Field(
        default=True,
        description=(
            "Use the agentic researcher (Claude tool_use loop) instead of the "
            "legacy prompt-and-parse pipeline. The agentic researcher calls "
            "tools autonomously and decides its own workflow."
        ),
    )
    max_agentic_turns: int = Field(
        default=30,
        description="Maximum tool-use round-trips per agentic iteration.",
    )
    use_duplex_mode: bool = Field(
        default=False,
        description=(
            "Use the Cerebellum-Brain duplex architecture. The Cerebellum "
            "(lightweight model) handles real-time user interaction while the "
            "Brain (full model) runs heavy computation as background tasks. "
            "Requires use_agentic_researcher=True."
        ),
    )
    duplex_cerebellum_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model for the Cerebellum (fast, lightweight interaction).",
    )
    initial_autonomy_mode: AutonomyMode = Field(
        default=AutonomyMode.SUPERVISED,
        description=(
            "Initial autonomy mode for the campaign. Can be changed at "
            "runtime. 'autonomous' auto-approves all checkpoints; "
            "'supervised' blocks only on high-risk events; "
            "'collaborative' blocks on all configured checkpoints."
        ),
    )
    skip_phases: list[str] = Field(
        default_factory=list,
        description=(
            "Campaign phases to skip. User-configurable, no hardcoded "
            "restrictions. Valid values: roundtable, screening, planning, "
            "research_loop, analysis, writing."
        ),
    )
    mock_mode: bool = Field(
        default=False,
        description=(
            "Run offline with mock models and mock tools: no Claude Code, "
            "no Docker, no API key. Overrides use_terminal_iteration, "
            "use_docker and use_real_tools. For demos and tests only."
        ),
    )
    use_terminal_iteration: bool = Field(
        default=True,
        description=(
            "Run the pipeline phases as Claude Code sessions in the "
            "experiment container (the same path the web console uses). "
            "False falls back to direct API calls."
        ),
    )
    allow_host_bash: bool = Field(
        default=False,
        description=(
            "Let Claude Code sessions run shell commands on the host when "
            "there is no container. Off by default: sessions may only read "
            "and edit files inside the campaign workspace."
        ),
    )
    terminal_driver: str = Field(
        default="auto",
        description=(
            "Agent driver for Terminal iteration: 'claude-code', 'openai', "
            "'anthropic', or 'auto'."
        ),
    )
    terminal_model: str = Field(
        default="",
        description="Model ID for Terminal iteration (empty = use researcher model).",
    )
    terminal_api_key: str = Field(
        default="",
        description="API key for Terminal iteration (empty = use env vars).",
    )
    terminal_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="API base URL for Terminal iteration.",
    )
    terminal_timeout_sec: float = Field(
        default=3600.0,
        description="Timeout in seconds for Terminal iteration.",
    )
    terminal_max_turns: int = Field(
        default=200,
        description="Max turns per phase for Terminal iteration.",
    )
    continue_until_timeout: bool = Field(
        default=False,
        description="Retry after verifier failure until timeout.",
    )

    # -- Docker execution environment --
    use_docker: bool = Field(
        default=True,
        description=(
            "Run all execution phases inside a Docker container. "
            "One experiment = one container, shared across all phases. "
            "Set to False only for local development/debugging."
        ),
    )
    docker_image: str = Field(
        default="python:3.11-slim",
        description=(
            "Docker image for the experiment container. Task adapters "
            "(e.g. LHTBTaskAdapter) override this with the task's image."
        ),
    )
    docker_cpus: float = Field(
        default=4.0,
        description="CPU limit for the Docker container.",
    )
    docker_memory_mb: int = Field(
        default=8192,
        description="Memory limit (MB) for the Docker container.",
    )
    docker_network: bool = Field(
        default=True,
        description="Allow internet access from the Docker container.",
    )
    docker_tests_dir: str = Field(
        default="",
        description="Host path to mount as /tests:ro inside the container.",
    )

    # -- Supervisor --
    use_supervisor: bool = Field(
        default=False,
        description=(
            "Enable the Supervisor (Claude Code instance) to monitor "
            "the main agent during research execution."
        ),
    )
    supervisor_model: str = Field(
        default="deepseek/deepseek-v4-flash",
        description="Model ID for the Supervisor instance.",
    )

    # -- Front Desk Agent --
    use_frontdesk: bool = Field(
        default=True,
        description=(
            "Enable the Front Desk Agent (Claude Code SDK session) as the "
            "human's single interaction entry point. Handles status queries, "
            "instruction relay, escalation response, and interrupt requests."
        ),
    )
    frontdesk_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model ID for the Front Desk Agent session.",
    )

    # -- Configurable agent prompts --
    screening_system_prompt: str = Field(
        default=(
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
        ),
        description="System prompt for the screening agent.",
    )
    planning_system_prompt: str = Field(
        default=(
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
        ),
        description="System prompt for the planning agent.",
    )
    analysis_system_prompt: str = Field(
        default=(
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
        ),
        description="System prompt for the analysis agent.",
    )
    writing_system_prompt: str = Field(
        default=(
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
        ),
        description="System prompt for the writing agent.",
    )
    roundtable_system_prompt: str = Field(
        default="",
        description="System prompt for the roundtable chair. Empty = built-in chair prompt.",
    )
    roundtable_seat_prompt: str = Field(
        default="",
        description=(
            "System prompt for roundtable participants (seats). Empty = built-in seat prompt. "
            "'{seat_id}' is replaced with the seat's ID."
        ),
    )
    research_system_prompt: str = Field(
        default=(
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
        ),
        description="System prompt for the research agent.",
    )

    @model_validator(mode="after")
    def _apply_mock_mode(self) -> "AuroraConfig":
        """In mock mode every stage uses a mock model and nothing external runs."""
        if not self.mock_mode:
            return self
        a = self.llm_assignment
        n_seats = max(len(a.roundtable_seat), self.num_roundtable_seats, 1)
        self.llm_assignment = a.model_copy(update={
            "roundtable_seat": [f"mock-seat-{i}" for i in range(n_seats)],
            "roundtable_chair": "mock-chair",
            "idea_screening": "mock-screener",
            "researcher": "mock-researcher",
            "auditor_triage": "mock-auditor-triage",
            "auditor_deep": "mock-auditor-deep",
            "analyzer": "mock-analyzer",
            "writer": "mock-writer",
        })
        self.roundtable_seats = [
            sc.model_copy(update={"model": f"mock-seat-{i}"})
            for i, sc in enumerate(self.roundtable_seats)
        ]
        self.use_terminal_iteration = False
        self.use_docker = False
        self.use_real_tools = False
        self.use_duplex_mode = False
        self.use_supervisor = False
        self.use_frontdesk = False
        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> AuroraConfig:
    """Load and validate a campaign configuration from a YAML file.

    Parameters
    ----------
    path:
        Path to the YAML configuration file.

    Returns
    -------
    AuroraConfig
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    pydantic.ValidationError
        If the YAML content fails schema validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    return AuroraConfig.model_validate(raw)
