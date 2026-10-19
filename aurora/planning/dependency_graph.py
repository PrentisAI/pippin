"""Dependency graph for experiment job scheduling.

:class:`DependencyGraph` wraps an :class:`ExperimentPlan` and provides
scheduling primitives — ready-job enumeration, completion/failure tracking,
topological ordering, critical-path analysis, and blocked-state detection.

Cycle detection is performed eagerly in the constructor so that an invalid
plan is rejected before any job executes.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Literal

from aurora.models.experiment import (
    ExperimentJob,
    ExperimentPlan,
    JobResult,
)

logger = logging.getLogger(__name__)

# Internal status type mirrors ExperimentJob.status but is tracked in the
# graph rather than mutating the plan's model instances.
_JobStatus = Literal["pending", "ready", "running", "completed", "failed", "blocked"]


class DependencyGraph:
    """Directed acyclic graph (DAG) of experiment jobs with scheduling state.

    Parameters
    ----------
    plan:
        The experiment plan whose jobs and dependency edges define the graph.

    Raises
    ------
    ValueError
        If the dependency graph contains a cycle or references unknown jobs.
    """

    def __init__(self, plan: ExperimentPlan) -> None:
        self._plan = plan

        # Index jobs by ID
        self._jobs: dict[str, ExperimentJob] = {j.job_id: j for j in plan.jobs}

        # Adjacency: parent → children (forward edges) and child → parents
        self._children: dict[str, list[str]] = defaultdict(list)
        self._parents: dict[str, list[str]] = defaultdict(list)

        for edge in plan.dependency_edges:
            if edge.from_job_id not in self._jobs:
                raise ValueError(
                    f"Dependency edge references unknown source job: {edge.from_job_id}"
                )
            if edge.to_job_id not in self._jobs:
                raise ValueError(
                    f"Dependency edge references unknown target job: {edge.to_job_id}"
                )
            self._add_edge(edge.from_job_id, edge.to_job_id)
        # planning agents state dependencies on the jobs themselves
        for job in plan.jobs:
            for parent in job.dependencies:
                if parent not in self._jobs or parent == job.job_id:
                    logger.warning("Job %s depends on %r, which is not a job of the plan; ignored",
                                   job.job_id, parent)
                    continue
                self._add_edge(parent, job.job_id)

        # Internal status tracker (decoupled from the ExperimentJob models)
        self._status: dict[str, _JobStatus] = {
            j.job_id: "pending" for j in plan.jobs
        }

        # Results stored upon completion
        self._results: dict[str, JobResult] = {}
        self._errors: dict[str, list[str]] = {}

        # Validate: detect cycles eagerly
        self._detect_cycles()

        # Promote root jobs (no parents) to "ready"
        for job_id in self._jobs:
            if not self._parents[job_id]:
                self._status[job_id] = "ready"

        logger.info(
            "DependencyGraph initialised: %d jobs, %d edges, %d ready",
            len(self._jobs),
            sum(len(c) for c in self._children.values()),
            sum(1 for s in self._status.values() if s == "ready"),
        )

    def _add_edge(self, parent: str, child: str) -> None:
        if child not in self._children[parent]:
            self._children[parent].append(child)
            self._parents[child].append(parent)

    # ------------------------------------------------------------------
    # Scheduling queries
    # ------------------------------------------------------------------

    def ready_jobs(self) -> list[ExperimentJob]:
        """Return jobs whose dependencies are all completed.

        A job is *ready* when all of its parent jobs have status
        ``"completed"`` and it has not yet started or failed.
        """
        return [
            self._jobs[jid]
            for jid, status in self._status.items()
            if status == "ready"
        ]

    def is_complete(self) -> bool:
        """Return ``True`` when every job has reached ``completed`` status."""
        return all(s == "completed" for s in self._status.values())

    def is_blocked(self) -> bool:
        """Return ``True`` when no jobs are ready but the plan is not complete.

        This indicates that remaining jobs depend on failed predecessors
        and cannot make progress without intervention.
        """
        if self.is_complete():
            return False
        return len(self.ready_jobs()) == 0 and not any(
            s == "running" for s in self._status.values()
        )

    def get_status(self, job_id: str) -> _JobStatus:
        """Return the current status of *job_id*."""
        return self._status[job_id]

    def get_result(self, job_id: str) -> JobResult | None:
        """Return the stored :class:`JobResult` for a completed job, or ``None``."""
        return self._results.get(job_id)

    def get_errors(self, job_id: str) -> list[str]:
        """Return error messages for a failed job."""
        return self._errors.get(job_id, [])

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def mark_running(self, job_id: str) -> None:
        """Transition a *ready* job to *running*."""
        self._assert_job_exists(job_id)
        if self._status[job_id] != "ready":
            raise ValueError(
                f"Cannot mark job {job_id!r} as running — "
                f"current status is {self._status[job_id]!r} (expected 'ready')"
            )
        self._status[job_id] = "running"
        logger.debug("Job %s → running", job_id)

    def mark_complete(self, job_id: str, result: JobResult) -> None:
        """Mark a job as completed and propagate readiness to dependants.

        Parameters
        ----------
        job_id:
            The job to mark.
        result:
            The execution result to record.
        """
        self._assert_job_exists(job_id)
        self._status[job_id] = "completed"
        self._results[job_id] = result
        logger.info("Job %s → completed (success=%s)", job_id, result.success)

        # Promote children whose parents are now all completed
        for child_id in self._children[job_id]:
            if self._status[child_id] in ("pending", "blocked"):
                if all(
                    self._status[pid] == "completed"
                    for pid in self._parents[child_id]
                ):
                    self._status[child_id] = "ready"
                    logger.debug("Job %s promoted to ready", child_id)

    def mark_failed(self, job_id: str, errors: list[str]) -> None:
        """Mark a job as failed and block all transitive dependants.

        Parameters
        ----------
        job_id:
            The job that failed.
        errors:
            Human-readable error messages.
        """
        self._assert_job_exists(job_id)
        self._status[job_id] = "failed"
        self._errors[job_id] = errors
        logger.warning("Job %s → failed: %s", job_id, "; ".join(errors[:3]))

        # Transitively block all descendant jobs
        queue: deque[str] = deque(self._children[job_id])
        visited: set[str] = set()
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            if self._status[cid] not in ("completed", "failed"):
                self._status[cid] = "blocked"
                logger.debug("Job %s → blocked (ancestor %s failed)", cid, job_id)
            queue.extend(self._children[cid])

    # ------------------------------------------------------------------
    # Graph analysis
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[ExperimentJob]:
        """Return jobs in topological (dependency-respecting) order.

        Uses Kahn's algorithm (BFS with in-degree counting).

        Returns
        -------
        list[ExperimentJob]
            Jobs ordered so that every job appears after all its
            dependencies.
        """
        in_degree: dict[str, int] = {jid: 0 for jid in self._jobs}
        for jid in self._jobs:
            in_degree[jid] = len(self._parents[jid])

        queue: deque[str] = deque(
            jid for jid, deg in in_degree.items() if deg == 0
        )
        order: list[ExperimentJob] = []

        while queue:
            jid = queue.popleft()
            order.append(self._jobs[jid])
            for child_id in self._children[jid]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        # Should not happen (cycles caught in __init__), but guard anyway
        if len(order) != len(self._jobs):  # pragma: no cover
            raise RuntimeError("Topological sort failed — cycle detected after construction")

        return order

    def get_critical_path(self) -> list[ExperimentJob]:
        """Return the longest path through the DAG (by number of jobs).

        This is the *critical path* — the chain of sequential dependencies
        that determines the minimum wall-clock time of the plan.

        Returns
        -------
        list[ExperimentJob]
            Jobs along the critical path, in execution order.
        """
        topo_order = self.topological_sort()
        topo_ids = [j.job_id for j in topo_order]

        # Longest-path DP: dist[j] = length of longest path ending at j
        dist: dict[str, int] = {jid: 1 for jid in self._jobs}
        predecessor: dict[str, str | None] = {jid: None for jid in self._jobs}

        for jid in topo_ids:
            for child_id in self._children[jid]:
                if dist[jid] + 1 > dist[child_id]:
                    dist[child_id] = dist[jid] + 1
                    predecessor[child_id] = jid

        # Find the terminal node with the largest distance
        end_id = max(dist, key=lambda k: dist[k])

        # Trace back the path
        path_ids: list[str] = []
        current: str | None = end_id
        while current is not None:
            path_ids.append(current)
            current = predecessor[current]

        path_ids.reverse()
        return [self._jobs[jid] for jid in path_ids]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def completed_job_ids(self) -> list[str]:
        """Return IDs of all jobs with 'completed' status."""
        return [jid for jid, status in self._status.items() if status == "completed"]

    def restore_completed(self, job_ids: list[str]) -> None:
        """Restore previously completed jobs and propagate readiness.

        Used when resuming a campaign from a checkpoint. Marks each job
        in *job_ids* as completed (with a stub JobResult) and triggers
        the readiness cascade for dependent jobs.

        Parameters
        ----------
        job_ids:
            Job IDs that were completed in a prior run.
        """
        for job_id in job_ids:
            if job_id not in self._jobs:
                logger.warning("Checkpoint references unknown job %s — skipping", job_id)
                continue
            if self._status[job_id] == "completed":
                continue
            # Create a stub result for the restored job
            stub_result = JobResult(
                job_id=job_id,
                success=True,
                metrics={},
                wall_time_seconds=0.0,
            )
            self._status[job_id] = "completed"
            self._results[job_id] = stub_result
            logger.info("Restored completed job %s from checkpoint", job_id)

        # Propagate readiness for all jobs whose parents are now complete
        for job_id in self._jobs:
            if self._status[job_id] in ("pending", "blocked"):
                parents = self._parents[job_id]
                if parents and all(self._status[pid] == "completed" for pid in parents):
                    self._status[job_id] = "ready"
                    logger.debug("Job %s promoted to ready after checkpoint restore", job_id)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        """Return a count of jobs in each status category."""
        counts: dict[str, int] = defaultdict(int)
        for status in self._status.values():
            counts[status] += 1
        return dict(counts)

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"DependencyGraph(jobs={len(self._jobs)}, "
            f"completed={s.get('completed', 0)}, "
            f"ready={s.get('ready', 0)}, "
            f"failed={s.get('failed', 0)}, "
            f"blocked={s.get('blocked', 0)})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_job_exists(self, job_id: str) -> None:
        if job_id not in self._jobs:
            raise KeyError(f"Unknown job ID: {job_id!r}")

    def _detect_cycles(self) -> None:
        """Detect cycles using Kahn's algorithm; raise on failure.

        If the topological order cannot include all nodes, at least one
        cycle exists.
        """
        in_degree: dict[str, int] = {jid: len(self._parents[jid]) for jid in self._jobs}

        queue: deque[str] = deque(
            jid for jid, deg in in_degree.items() if deg == 0
        )
        visited_count = 0

        while queue:
            jid = queue.popleft()
            visited_count += 1
            for child_id in self._children[jid]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        if visited_count != len(self._jobs):
            # Identify the jobs involved in cycles for a helpful message
            cycle_jobs = [jid for jid, deg in in_degree.items() if deg > 0]
            raise ValueError(
                f"Dependency graph contains a cycle involving "
                f"{len(cycle_jobs)} job(s): {cycle_jobs[:5]}"
            )
