"""Defaults for new experiments, changed in the web console (Settings, or by asking the front desk).

They are a layer over the server's base config (``aurora serve --config``), kept in
``AURORA_HOME/console_settings.yaml``; the base config file itself is not changed.  The order,
later winning: built-in defaults, the server's base config, these settings, the proposal card.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from aurora.paths import aurora_home

# What the console may change: (dotted key, group, label, kind[, choices]).
# kinds: model, models (a list of model IDs), number, optional-number, int, bool, choice, text,
# words (a list typed as words separated by spaces)
EDITABLE: list[tuple] = [
    ("models.agent", "Models", "Agents (default for all phases)", "model"),
    ("models.screening", "Models", "Screening", "model"),
    ("models.planning", "Models", "Planning", "model"),
    ("models.preflight", "Models", "Pre-flight", "model"),
    ("models.research", "Models", "Research", "model"),
    ("models.analysis", "Models", "Analysis", "model"),
    ("models.writing", "Models", "Writing", "model"),
    ("models.ideation_seats", "Models", "Ideation seats", "models"),
    ("models.ideation_chair", "Models", "Ideation chair", "model"),
    ("models.auditor", "Models", "Milestone auditor", "model"),
    ("models.supervisor", "Models", "Live auditor", "model"),
    ("models.frontdesk", "Models", "Front desk", "model"),
    ("models.skills", "Models", "Skill drafting", "model"),
    ("budget.max_cost_usd", "Budget", "Cost limit per experiment (US$; empty: none)", "optional-number"),
    ("budget.max_hours", "Budget", "Time limit per experiment (hours)", "number"),
    ("human.autonomy", "Decisions", "When a decision is needed", "choice", ["supervised", "autonomous"]),
    ("human.gate_timeout_minutes", "Decisions", "Wait for a decision (minutes)", "number"),
    ("human.on_timeout", "Decisions", "If nobody decides in time", "choice", ["continue", "stop"]),
    ("human.frontdesk", "Decisions", "Front desk on", "bool"),
    ("ideation.mode", "Ideation", "Mode", "choice", ["roundtable", "independent"]),
    ("ideation.web_search", "Ideation", "Seats search the literature", "bool"),
    ("literature.enabled", "Literature", "Search and check papers", "bool"),
    ("literature.add_found", "Literature", "Papers the agents find", "choice", ["cited", "all", "none"]),
    ("literature.queries_per_turn", "Literature", "Searches per ideation turn", "int"),
    ("literature.screening_search", "Literature", "Screening compares with prior work", "bool"),
    ("agent.max_parallel_jobs", "Research", "Jobs at the same time", "int"),
    ("agent.research_timeout_minutes", "Research", "Time limit of one job (minutes)", "number"),
    ("agent.max_followup_cycles", "Research", "Follow-up rounds analysis may ask for", "int"),
    ("agent.preflight_max_revisions", "Research", "Plan revisions after pre-flight problems", "int"),
    ("audit.live", "Research", "Live auditor watches the research agents", "bool"),
    ("sandbox.image", "Container", "Docker image", "text"),
    ("sandbox.cpus", "Container", "CPUs", "number"),
    ("sandbox.memory_mb", "Container", "Memory (MB)", "int"),
    ("sandbox.network", "Container", "Internet access", "bool"),
    ("sandbox.agent_in_container", "Container", "Research agents run inside the container (experimental)", "bool"),
    ("skills.distill", "Skills", "Learn skills from the agents' runs", "bool"),
    ("skills.from_feedback", "Skills", "Learn skills from your corrections", "bool"),
    ("skills.auto_accept", "Skills", "Add proposed skills without asking", "bool"),
    ("compute.slurm.enabled", "Cluster jobs", "Research agents may submit SLURM jobs", "bool"),
    ("compute.slurm.submit_via", "Cluster jobs", "Reach SLURM through (e.g. ssh login-node; empty: here)", "words"),
    ("compute.slurm.partition", "Cluster jobs", "Partition", "text"),
    ("compute.slurm.job_name_prefix", "Cluster jobs", "Job names start with", "text"),
    ("compute.slurm.max_gpus_per_job", "Cluster jobs", "Most GPUs per job", "int"),
    ("compute.slurm.max_jobs", "Cluster jobs", "Jobs at once per experiment", "int"),
    ("compute.slurm.max_time", "Cluster jobs", "Longest job (HH:MM:SS)", "text"),
    ("compute.slurm.max_gpu_hours", "Cluster jobs", "GPU hours per experiment (empty: no limit)", "optional-number"),
    ("compute.ssh.enabled", "GPU machine (SSH)", "Research agents may run jobs on a GPU machine over SSH", "bool"),
    ("compute.ssh.host", "GPU machine (SSH)", "Machine (a Host in ~/.ssh/config, or user@address)", "text"),
    ("compute.ssh.ssh_options", "GPU machine (SSH)", "More ssh options (e.g. -p 2222)", "words"),
    ("compute.ssh.remote_dir", "GPU machine (SSH)", "Folder for the workspaces there", "text"),
    ("compute.ssh.docker", "GPU machine (SSH)", "Run jobs in Docker there", "bool"),
    ("compute.ssh.setup", "GPU machine (SSH)", "Before every job (e.g. conda activate ml)", "text"),
    ("compute.ssh.gpus", "GPU machine (SSH)", "GPUs jobs may use (indices; empty: all)", "words"),
    ("compute.ssh.max_gpus_per_job", "GPU machine (SSH)", "Most GPUs per job", "int"),
    ("compute.ssh.max_jobs", "GPU machine (SSH)", "Jobs at once per experiment", "int"),
    ("compute.ssh.max_time", "GPU machine (SSH)", "Longest job (HH:MM:SS)", "text"),
    ("compute.ssh.max_gpu_hours", "GPU machine (SSH)", "GPU hours per experiment (empty: no limit)", "optional-number"),
    ("uploads.max_file_gb", "Uploads", "Largest file (GB)", "number"),
    ("uploads.max_project_gb", "Uploads", "Uploaded files per project (GB)", "number"),
]
KEYS = {e[0] for e in EDITABLE}


def overlay_path() -> Path:
    return aurora_home() / "console_settings.yaml"


def load_overlay() -> dict[str, Any]:
    path = overlay_path()
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_overlay(overlay: dict[str, Any]) -> None:
    path = overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(overlay, sort_keys=True, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def get_dotted(data: dict[str, Any], key: str) -> tuple[bool, Any]:
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def with_changes(overlay: dict[str, Any], changes: dict[str, Any], reset: list[str] | None = None) -> dict[str, Any]:
    """*overlay* with dotted-key *changes* set and the *reset* keys removed (back to the server's value)."""
    unknown = (set(changes) | set(reset or [])) - KEYS
    if unknown:
        raise ValueError(f"not a console setting: {', '.join(sorted(unknown))}")
    out = copy.deepcopy(overlay)
    for key, value in changes.items():
        *parents, leaf = key.split(".")
        node = out
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    for key in reset or []:
        *parents, leaf = key.split(".")
        node = out
        for p in parents:
            node = node.get(p) or {}
        node.pop(leaf, None)
    # drop emptied sections
    return {k: v for k, v in out.items() if v != {}}
