"""Singleton state manager for the AURORA API.

Holds the in-memory campaign state, provides access to artifacts and
engine status, and bridges real-time updates to WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from aurora.api.schemas import (
    ActivityEvent,
    CampaignProgress,
    CampaignSummary,
    DashboardStats,
    PipelinePhaseStatus,
)
from aurora.config import AutonomyMode, AuroraConfig, load_config
from aurora.core.engine import CampaignPhase, Checkpoint, ResearchEngine
from aurora.core.events import Event, EventBus
from aurora.human.chat_handler import AgentChatHandler
from aurora.human.web_checkpoint import WebCheckpointHandler
from aurora.models.artifacts import ResearchArtifacts

if TYPE_CHECKING:
    from aurora.api.app import ConnectionManager

logger = logging.getLogger(__name__)

# Canonical phase ordering for the pipeline progress view
_PHASE_ORDER: list[str] = [
    CampaignPhase.ROUNDTABLE.value,
    CampaignPhase.SCREENING.value,
    CampaignPhase.PLANNING.value,
    CampaignPhase.RESEARCH_LOOP.value,
    CampaignPhase.ANALYSIS.value,
    CampaignPhase.WRITING.value,
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CampaignState:
    """Tracks the runtime state of a single campaign."""

    def __init__(
        self,
        config: AuroraConfig,
        workspace_dir: Path,
    ) -> None:
        self.config = config
        self.workspace_dir = workspace_dir
        self.engine: ResearchEngine | None = None
        self.phase: CampaignPhase = CampaignPhase.INITIALIZING
        self.research_cycle: int = 0
        self.created_at: datetime = _now()
        self.updated_at: datetime = _now()
        self.running: bool = False
        self.paused: bool = False
        self.autonomy_mode: AutonomyMode = config.initial_autonomy_mode
        self._task: asyncio.Task[Any] | None = None
        self.checkpoint_handler: WebCheckpointHandler | None = None
        self.chat_handler: AgentChatHandler | None = None
        self.proactive_interventions: list[dict[str, Any]] = []

    @property
    def campaign_id(self) -> str:
        return self.config.campaign_id


class StateManager:
    """Singleton managing all campaigns and the WebSocket broadcast bridge.

    Designed for a single-process API server that holds state in memory.
    Campaign data is loaded from workspace directories on disk and kept
    in sync via the ``EventBus``.
    """

    def __init__(self) -> None:
        self._campaigns: dict[str, CampaignState] = {}
        self._events: list[ActivityEvent] = []
        self._max_events: int = 2000
        self._ws_manager: ConnectionManager | None = None
        self._default_workspace_root: Path = Path("./workspace")

    # ------------------------------------------------------------------
    # WebSocket bridge
    # ------------------------------------------------------------------

    def set_ws_manager(self, ws_manager: ConnectionManager) -> None:
        self._ws_manager = ws_manager

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected WebSocket clients."""
        if self._ws_manager is not None:
            await self._ws_manager.broadcast(message)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_event(self, event: ActivityEvent) -> None:
        """Append an event to the in-memory event log."""
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    def get_events(
        self,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActivityEvent]:
        """Return recent events with optional campaign filtering."""
        filtered = self._events
        if campaign_id:
            filtered = [
                e for e in filtered
                if e.data.get("campaign_id") == campaign_id
                or e.source == campaign_id
            ]
        total = len(filtered)
        start = max(0, total - offset - limit)
        end = total - offset
        return list(reversed(filtered[start:end]))

    # ------------------------------------------------------------------
    # Campaign lifecycle
    # ------------------------------------------------------------------

    def scan_workspace(self, root: Path | None = None) -> list[CampaignSummary]:
        """Scan a workspace root directory for existing campaigns."""
        root = root or self._default_workspace_root
        summaries: list[CampaignSummary] = []

        if not root.exists():
            return summaries

        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            config_path = child / "campaign_config.yaml"
            artifacts_path = child / "artifacts.json"
            checkpoint_path = child / "checkpoint.json"

            campaign_id = child.name
            objective = ""
            domain = "computational_materials_science"
            phase = "initializing"
            created_at = None
            updated_at = None

            # Try to load config
            if config_path.exists():
                try:
                    cfg = load_config(config_path)
                    campaign_id = cfg.campaign_id
                    objective = cfg.objective
                    domain = cfg.domain
                except Exception:
                    logger.debug("Could not load config from %s", config_path)

            # Try to read checkpoint for phase
            if checkpoint_path.exists():
                try:
                    cp = Checkpoint.model_validate_json(
                        checkpoint_path.read_text(encoding="utf-8")
                    )
                    phase = cp.phase
                except Exception:
                    pass

            # Try to read artifacts for timestamps
            if artifacts_path.exists():
                try:
                    raw = json.loads(artifacts_path.read_text(encoding="utf-8"))
                    created_at = raw.get("created_at")
                    updated_at = raw.get("updated_at")
                    if not objective:
                        objective = raw.get("initial_objective", "")
                except Exception:
                    pass

            # Also check if this campaign is loaded in memory
            autonomy_mode = "supervised"
            if campaign_id in self._campaigns:
                state = self._campaigns[campaign_id]
                phase = state.phase.value
                autonomy_mode = state.autonomy_mode.value
                created_at = created_at or state.created_at.isoformat()
                updated_at = state.updated_at.isoformat()

            summaries.append(CampaignSummary(
                campaign_id=campaign_id,
                objective=objective,
                domain=domain,
                phase=phase,
                autonomy_mode=autonomy_mode,
                created_at=created_at,
                updated_at=updated_at,
            ))

        # Also include any in-memory campaigns not found on disk
        for cid, state in self._campaigns.items():
            if not any(s.campaign_id == cid for s in summaries):
                summaries.append(CampaignSummary(
                    campaign_id=cid,
                    objective=state.config.objective,
                    domain=state.config.domain,
                    phase=state.phase.value,
                    autonomy_mode=state.autonomy_mode.value,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                ))

        return summaries

    def get_campaign_state(self, campaign_id: str) -> CampaignState | None:
        """Return the in-memory state for a campaign, or None."""
        return self._campaigns.get(campaign_id)

    def register_campaign(
        self,
        config: AuroraConfig,
        workspace_dir: Path | None = None,
    ) -> CampaignState:
        """Register a campaign config and create its state holder."""
        ws_dir = workspace_dir or (
            self._default_workspace_root / config.campaign_id
        )
        state = CampaignState(config=config, workspace_dir=ws_dir)
        self._campaigns[config.campaign_id] = state
        return state

    def load_artifacts(self, campaign_id: str) -> ResearchArtifacts | None:
        """Load artifacts from the campaign workspace on disk."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None

        artifacts_path = state.workspace_dir / "artifacts.json"
        if not artifacts_path.exists():
            return None

        try:
            return ResearchArtifacts.model_validate_json(
                artifacts_path.read_text(encoding="utf-8")
            )
        except Exception:
            logger.exception("Failed to load artifacts for %s", campaign_id)
            return None

    def load_checkpoint(self, campaign_id: str) -> Checkpoint | None:
        """Load the campaign checkpoint from disk."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None

        cp_path = state.workspace_dir / "checkpoint.json"
        if not cp_path.exists():
            return None

        try:
            return Checkpoint.model_validate_json(
                cp_path.read_text(encoding="utf-8")
            )
        except Exception:
            logger.debug("Could not load checkpoint for %s", campaign_id)
            return None

    def get_progress(self, campaign_id: str) -> CampaignProgress | None:
        """Build a pipeline progress summary for a campaign."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None

        current_phase = state.phase.value
        checkpoint = self.load_checkpoint(campaign_id)

        phases: list[PipelinePhaseStatus] = []
        found_current = False
        for p in _PHASE_ORDER:
            if p == current_phase:
                found_current = True
                phases.append(PipelinePhaseStatus(phase=p, status="active"))
            elif not found_current:
                phases.append(PipelinePhaseStatus(phase=p, status="done"))
            else:
                phases.append(PipelinePhaseStatus(phase=p, status="pending"))

        completed_jobs = checkpoint.completed_jobs if checkpoint else []

        return CampaignProgress(
            campaign_id=campaign_id,
            current_phase=current_phase,
            research_cycle=state.research_cycle,
            phases=phases,
            iteration_count=len(completed_jobs),
            completed_jobs=len(completed_jobs),
            total_jobs=0,
        )

    def get_dashboard_stats(self, campaign_id: str) -> DashboardStats:
        """Compute aggregate dashboard statistics from artifacts."""
        artifacts = self.load_artifacts(campaign_id)
        if artifacts is None:
            return DashboardStats()

        all_experiments = artifacts.experiments + artifacts.failed_experiments
        completed = [e for e in artifacts.experiments if e.status == "completed"]
        failed = artifacts.failed_experiments

        total = len(all_experiments)
        success_rate = (len(completed) / total * 100) if total > 0 else 0.0

        tentative = sum(1 for c in artifacts.claims if c.status == "tentative")
        accepted = sum(1 for c in artifacts.claims if c.status == "accepted")
        rejected = sum(1 for c in artifacts.claims if c.status == "rejected")

        # Count ungrounded claims
        ungrounded = 0
        for claim in artifacts.claims:
            if claim.status in ("tentative", "accepted"):
                evidence = artifacts.provenance_links.get(claim.claim_id, [])
                if not evidence:
                    ungrounded += 1

        validated_obs = sum(1 for o in artifacts.observations if o.validated)

        state = self._campaigns.get(campaign_id)
        research_cycles = state.research_cycle if state else 0

        return DashboardStats(
            total_experiments=total,
            completed_experiments=len(completed),
            failed_experiments=len(failed),
            success_rate=success_rate,
            total_observations=len(artifacts.observations),
            validated_observations=validated_obs,
            total_claims=len(artifacts.claims),
            tentative_claims=tentative,
            accepted_claims=accepted,
            rejected_claims=rejected,
            ungrounded_claims=ungrounded,
            open_questions=len(artifacts.open_questions),
            iteration_count=len(artifacts.experiments),
            research_cycles=research_cycles,
            audit_count=len(artifacts.auditor_findings),
            human_interventions=len(artifacts.human_interventions),
        )

    async def start_campaign(self, campaign_id: str) -> bool:
        """Start a campaign's research engine as a background task.

        Returns True if the campaign was started, False if already running
        or not found.
        """
        state = self._campaigns.get(campaign_id)
        if state is None or state.running:
            return False

        engine = ResearchEngine(state.config)
        state.engine = engine
        state.running = True
        state.paused = False
        state.phase = CampaignPhase.ROUNDTABLE

        # Set up the web checkpoint handler for the engine
        checkpoint_handler = self.ensure_checkpoint_handler(campaign_id)
        if checkpoint_handler is not None:
            engine.set_checkpoint_handler(checkpoint_handler)

        # Set up the chat handler for real-time agent interaction
        chat_handler = self.ensure_chat_handler(campaign_id)
        if chat_handler is not None:
            engine.set_chat_handler(chat_handler)

        # Subscribe to engine events to forward to WebSocket + event log
        async def _forward_event(event: Event) -> None:
            activity = ActivityEvent(
                name=event.name,
                data={**event.data, "campaign_id": campaign_id},
                source=event.source,
                timestamp=event.timestamp,
            )
            self.record_event(activity)
            await self.broadcast({
                "type": "event",
                "campaign_id": campaign_id,
                "event": activity.model_dump(mode="json"),
            })

            # Update phase tracking
            if event.name == "phase_started":
                phase_str = event.data.get("phase", "")
                try:
                    state.phase = CampaignPhase(phase_str)
                except ValueError:
                    pass
                state.updated_at = _now()

        engine.event_bus.subscribe_all(_forward_event)

        async def _run_campaign() -> None:
            try:
                await engine.run_campaign()
                state.phase = CampaignPhase.COMPLETED
            except asyncio.CancelledError:
                state.phase = CampaignPhase.FAILED
                logger.info("Campaign %s was stopped", campaign_id)
            except Exception:
                state.phase = CampaignPhase.FAILED
                logger.exception("Campaign %s failed", campaign_id)
            finally:
                state.running = False
                state.updated_at = _now()
                await self.broadcast({
                    "type": "campaign_status",
                    "campaign_id": campaign_id,
                    "phase": state.phase.value,
                    "running": False,
                })

        state._task = asyncio.create_task(_run_campaign())
        return True

    async def stop_campaign(self, campaign_id: str) -> bool:
        """Cancel a running campaign's background task.

        Returns True if the campaign was stopped, False otherwise.
        """
        state = self._campaigns.get(campaign_id)
        if state is None or not state.running or state._task is None:
            return False

        state._task.cancel()
        state.running = False
        state.updated_at = _now()
        return True

    # ------------------------------------------------------------------
    # Checkpoint handler management
    # ------------------------------------------------------------------

    def get_checkpoint_handler(
        self, campaign_id: str
    ) -> WebCheckpointHandler | None:
        """Return the WebCheckpointHandler for a campaign, or None."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None
        return state.checkpoint_handler

    def ensure_checkpoint_handler(
        self, campaign_id: str
    ) -> WebCheckpointHandler | None:
        """Return the checkpoint handler, creating one if needed."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None
        if state.checkpoint_handler is None:
            timeout = state.config.human_checkpoints.auto_approve_timeout_seconds
            state.checkpoint_handler = WebCheckpointHandler(
                broadcast_fn=self.broadcast,
                auto_approve_timeout=float(timeout) if timeout else None,
                autonomy_mode=state.autonomy_mode,
            )
        return state.checkpoint_handler

    # ------------------------------------------------------------------
    # Autonomy mode
    # ------------------------------------------------------------------

    async def set_autonomy_mode(
        self, campaign_id: str, mode: AutonomyMode
    ) -> int:
        """Change the autonomy mode for a campaign.

        Returns the number of pending checkpoints that were auto-resolved,
        or -1 if the campaign was not found.
        """
        state = self._campaigns.get(campaign_id)
        if state is None:
            return -1

        old_mode = state.autonomy_mode
        state.autonomy_mode = mode
        state.updated_at = _now()

        released = 0
        if state.checkpoint_handler is not None:
            released = state.checkpoint_handler.set_autonomy_mode(mode)

        logger.info(
            "Campaign %s autonomy mode: %s -> %s (%d checkpoints released)",
            campaign_id, old_mode.value, mode.value, released,
        )

        await self.broadcast({
            "type": "autonomy_mode_changed",
            "campaign_id": campaign_id,
            "mode": mode.value,
            "previous_mode": old_mode.value,
            "checkpoints_released": released,
        })

        return released

    # ------------------------------------------------------------------
    # Chat handler management
    # ------------------------------------------------------------------

    def get_chat_handler(
        self, campaign_id: str
    ) -> AgentChatHandler | None:
        """Return the AgentChatHandler for a campaign, or None."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None
        return state.chat_handler

    def ensure_chat_handler(
        self, campaign_id: str
    ) -> AgentChatHandler | None:
        """Return the chat handler, creating one if needed."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return None
        if state.chat_handler is None:
            state.chat_handler = AgentChatHandler(
                campaign_id=campaign_id,
                broadcast_fn=self.broadcast,
            )
        return state.chat_handler

    # ------------------------------------------------------------------
    # Pause / resume
    # ------------------------------------------------------------------

    def pause_campaign(self, campaign_id: str) -> bool:
        """Pause a running campaign.

        The engine will stop after its current iteration completes.
        Returns True if the campaign was paused, False otherwise.
        """
        state = self._campaigns.get(campaign_id)
        if state is None:
            return False
        if state.paused:
            return False

        state.paused = True
        state.updated_at = _now()

        # Signal the engine's pause event if it exists
        if state.engine is not None and hasattr(state.engine, '_paused'):
            state.engine._paused.clear()

        logger.info("Campaign %s paused", campaign_id)
        return True

    def resume_campaign(self, campaign_id: str) -> bool:
        """Resume a paused campaign.

        Returns True if the campaign was resumed, False otherwise.
        """
        state = self._campaigns.get(campaign_id)
        if state is None:
            return False
        if not state.paused:
            return False

        state.paused = False
        state.updated_at = _now()

        # Signal the engine's pause event if it exists
        if state.engine is not None and hasattr(state.engine, '_paused'):
            state.engine._paused.set()

        logger.info("Campaign %s resumed", campaign_id)
        return True

    # ------------------------------------------------------------------
    # Proactive interventions
    # ------------------------------------------------------------------

    def record_proactive_intervention(
        self,
        campaign_id: str,
        category: str,
        description: str,
        directive: str,
    ) -> dict[str, Any]:
        """Record a proactive intervention from the frontend.

        Returns the intervention record dict.
        """
        state = self._campaigns.get(campaign_id)
        if state is None:
            return {}

        record: dict[str, Any] = {
            "intervention_id": f"intv_{uuid.uuid4().hex[:8]}",
            "category": category,
            "description": description,
            "directive": directive,
            "timestamp": _now(),
        }
        state.proactive_interventions.append(record)
        state.updated_at = _now()

        logger.info(
            "Proactive intervention recorded for %s: %s",
            campaign_id,
            description[:80],
        )
        return record

    def get_proactive_interventions(
        self, campaign_id: str
    ) -> list[dict[str, Any]]:
        """Return all proactive interventions for a campaign."""
        state = self._campaigns.get(campaign_id)
        if state is None:
            return []
        return list(state.proactive_interventions)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_state_manager: StateManager | None = None


def get_state_manager() -> StateManager:
    """Return the singleton StateManager, creating it if needed."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
