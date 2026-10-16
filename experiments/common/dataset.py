"""Read access to the open-ended subset of AutoResearchEval (Hugging Face: PrentisAI/AutoResearchEval)."""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from common.normalize import load_steps
from common.paths import require


class AutoResearchEval:
    """Tasks, rollouts and normalized trajectories of the ``open-ended`` subset."""

    def __init__(self, root: str | Path) -> None:
        root = Path(root)
        if (root / "open-ended").is_dir():
            root = root / "open-ended"
        self.root = require(root, "AutoResearchEval (open-ended subset)")
        require(self.root / "tasks.jsonl", "AutoResearchEval tasks.jsonl")

    @cached_property
    def tasks(self) -> dict[str, dict]:
        with open(self.root / "tasks.jsonl") as fh:
            return {t["task_id"]: t for t in map(json.loads, fh)}

    def task(self, task_id: str) -> dict:
        return self.tasks[task_id]

    def rollouts(self):
        with open(self.root / "rollouts.jsonl") as fh:
            yield from map(json.loads, fh)

    def trajectory_path(self, model: str, task_id: str) -> Path:
        return self.root / "trajectories" / model / f"{task_id}.jsonl"

    def steps(self, model: str, task_id: str) -> list[dict]:
        """Normalized steps (thinking / text / call / output) of one trajectory."""
        return load_steps(self.trajectory_path(model, task_id))

    def premise_and_tension(self, task_id: str) -> str:
        """The task description shown to the auditors: premise and open problem, no conclusion."""
        t = self.task(task_id)
        return f"{t['premise']}\n\n{t['tension']}"
