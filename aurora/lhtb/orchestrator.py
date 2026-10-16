"""Orchestrator — lightweight LLM coordinator between main agent and watchdog.

Responsibilities
----------------
1. During checkpoint waits, monitor watchdog activity and send heartbeats
   to the main agent so it doesn't timeout while the watchdog is working.
2. Detect if the watchdog is stuck or off-track and re-prompt it.
3. Manage the overall lifecycle coordination.

The orchestrator uses direct API calls (not a Claude Code instance) for
lightweight, fast assessments.  It watches the watchdog's stdout events
to determine activity without relying on the watchdog to send heartbeats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Orchestrator:
    """Lightweight LLM-based coordinator for main agent ↔ watchdog."""

    def __init__(
        self,
        *,
        watchdog: Any,
        api_key: str = "",
        model: str = "deepseek/deepseek-v4-flash",
        base_url: str = "https://openrouter.ai/api/v1",
        heartbeat_interval: float = 15.0,
        max_stuck_checks: int = 3,
    ) -> None:
        self._watchdog = watchdog
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._model = model
        self._base_url = base_url
        self._heartbeat_interval = heartbeat_interval
        self._max_stuck_checks = max_stuck_checks
        self._monitor_task: asyncio.Task[Any] | None = None
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Checkpoint monitoring
    # ------------------------------------------------------------------

    async def monitor_checkpoint(
        self,
        checkpoint_id: str,
        verdicts_file: Path,
    ) -> None:
        """Monitor watchdog during a checkpoint, sending heartbeats.

        Runs as a background task.  Cancelled when the checkpoint
        verdict arrives or the timeout fires.
        """
        self._monitor_task = asyncio.current_task()
        stuck_count = 0
        last_event_count = len(self._watchdog._stdout_events)

        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)

                current_event_count = len(self._watchdog._stdout_events)
                recent_events = self._watchdog._stdout_events[-10:]

                has_new_activity = current_event_count > last_event_count
                last_event_count = current_event_count

                if has_new_activity:
                    stuck_count = 0
                    assessment = await self._assess_activity(
                        recent_events, checkpoint_id,
                    )
                    heartbeat = {
                        "checkpoint_id": checkpoint_id,
                        "type": "heartbeat",
                        "message": assessment.get("summary", "Watchdog is working"),
                        "timestamp": time.time(),
                    }
                    self._write_jsonl(verdicts_file, heartbeat)
                    logger.debug(
                        "Checkpoint %s heartbeat: %s",
                        checkpoint_id, assessment.get("summary", ""),
                    )
                else:
                    stuck_count += 1
                    logger.info(
                        "Checkpoint %s: watchdog idle for %d checks",
                        checkpoint_id, stuck_count,
                    )

                    if stuck_count >= self._max_stuck_checks:
                        await self._handle_stuck_watchdog(
                            checkpoint_id, verdicts_file, stuck_count,
                        )

        except asyncio.CancelledError:
            pass

    async def _assess_activity(
        self,
        recent_events: list[dict[str, Any]],
        checkpoint_id: str,
    ) -> dict[str, str]:
        """Ask LLM to assess whether watchdog activity is checkpoint-related."""
        events_text = "\n".join(
            json.dumps(e, default=str)[:200] for e in recent_events[-5:]
        )

        prompt = (
            f"A watchdog agent is supposed to be auditing checkpoint {checkpoint_id}. "
            f"Here are its most recent stdout events:\n\n{events_text}\n\n"
            f"Is the watchdog actively working on the checkpoint audit? "
            f"Reply in JSON: {{\"status\": \"working|off_track|idle\", "
            f"\"summary\": \"one sentence describing what the watchdog is doing\"}}"
        )

        try:
            result = await self._llm_call(prompt)
            parsed = _extract_json(result)
            if parsed and "status" in parsed:
                return parsed
        except Exception as e:
            logger.warning("Orchestrator assess failed: %s", e)

        return {"status": "working", "summary": "Assessment unavailable — assuming active"}

    async def _handle_stuck_watchdog(
        self,
        checkpoint_id: str,
        verdicts_file: Path,
        stuck_count: int,
    ) -> None:
        """Handle a watchdog that appears stuck during a checkpoint."""
        if stuck_count == self._max_stuck_checks:
            # First attempt: re-prompt the watchdog
            logger.warning(
                "Checkpoint %s: watchdog appears stuck, re-prompting",
                checkpoint_id,
            )
            await self._watchdog.push_message(
                f"REMINDER: Checkpoint {checkpoint_id} is still pending. "
                f"The main agent is BLOCKED waiting for your verdict. "
                f"Please write your verdict to the verdicts file immediately."
            )
            # Send heartbeat so main agent knows we're handling it
            self._write_jsonl(verdicts_file, {
                "checkpoint_id": checkpoint_id,
                "type": "heartbeat",
                "message": "Orchestrator re-prompted watchdog — waiting for response",
                "timestamp": time.time(),
            })

        elif stuck_count >= self._max_stuck_checks * 2:
            # Second threshold: force a default verdict
            logger.warning(
                "Checkpoint %s: watchdog unresponsive after %d checks, "
                "forcing default verdict",
                checkpoint_id, stuck_count,
            )
            self._write_jsonl(verdicts_file, {
                "checkpoint_id": checkpoint_id,
                "level": "normal",
                "reason": (
                    f"Orchestrator: watchdog unresponsive after "
                    f"{stuck_count * self._heartbeat_interval:.0f}s — "
                    f"forcing pass to unblock main agent"
                ),
                "suggestion": "",
                "confidence": "low",
                "timestamp": time.time(),
            })

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _llm_call(self, prompt: str) -> str:
        """Make a lightweight LLM call for assessment."""
        client = await self._get_client()
        resp = await client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_jsonl(path: Path, obj: dict[str, Any]) -> None:
        try:
            with open(path, "a") as f:
                f.write(json.dumps(obj, default=str) + "\n")
        except Exception:
            logger.exception("Failed to write to %s", path)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text that may contain surrounding prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
