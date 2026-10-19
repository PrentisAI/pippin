"""Notifications: tell the researcher when a decision waits for them or a run ends.

Channels are configured under ``notifications`` (or in the web console's Settings): a generic
JSON webhook, a WeCom (企业微信) group robot, a Feishu/Lark group bot, a Slack incoming webhook,
or Server酱, which pushes to a personal WeChat.  Every message links to the experiment in the
web console, where the decision is made.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from aurora.config import NotificationsConfig, NotifyChannel
from aurora.core.events import Event

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
# the same message again within this many seconds is dropped (a burst of identical events)
_DEDUPE_SECONDS = 60.0


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def describe(event: Event, run: str) -> tuple[str, str, str] | None:
    """The notification an engine event calls for, as (kind, title, text); None for none."""
    d = event.data
    name = event.name
    if name == "human_gate_opened":
        return ("decision_needed", f"Decision needed: {run}",
                f"{d.get('title', '')}\n\n{_clip(str(d.get('detail', '')), 600)}")
    if name == "campaign_completed":
        return ("run_finished", f"Finished: {run}",
                f"{_clip(str(d.get('summary', '')), 400)}\n\nCost ${d.get('cost_usd', 0):.2f}, "
                f"{_duration(d.get('wall_time_seconds', 0))}.")
    if name in ("campaign_failed", "campaign_stopped"):
        if name == "campaign_stopped" and d.get("by_user"):
            return None      # the researcher stopped it: they know
        verb = "Failed" if name == "campaign_failed" else "Stopped"
        return ("run_failed", f"{verb}: {run}",
                f"{_clip(str(d.get('summary', '')), 600)}\n\nCost ${d.get('cost_usd', 0):.2f}, "
                f"{_duration(d.get('wall_time_seconds', 0))}.")
    if name == "budget_warning":
        return ("budget_warning", f"Budget {int(round(d.get('share', 0) * 100))}% used: {run}",
                str(d.get("detail", "")))
    if name == "agent_reply_to_human":
        return ("agent_reply", f"The research agent answered: {run}", _clip(str(d.get("text", "")), 800))
    if name == "skill_suggestion":
        return ("skill_suggestion", f"Skill proposed: {d.get('name', '')}",
                f"{run}\n\n{_clip(str(d.get('text', '')), 500)}")
    if name == "compute_job_ended":
        return ("compute_job", f"Cluster job {d.get('state', '').lower()}: {d.get('name', '')}",
                f"{run}\nJob {d.get('id')} (SLURM {d.get('slurm_id')}), ran {_duration(d.get('elapsed_seconds', 0))}"
                + (f", exit code {d['exit_code']}" if d.get("exit_code") is not None else "") + ".")
    return None


def _serverchan_url(key: str) -> str:
    # Server酱³ keys (sctp<uid>t...) have their own host; Turbo keys use sctapi.ftqq.com
    m = re.match(r"sctp(\d+)t", key)
    return f"https://{m.group(1)}.push.ft07.com/send/{key}.send" if m else f"https://sctapi.ftqq.com/{key}.send"


def _request(channel: NotifyChannel, kind: str, title: str, text: str, url: str,
             extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The URL and JSON body a channel takes."""
    link = f"\n\n{url}" if url else ""
    if channel.type == "wecom":
        md = f"**{title}**\n{text}" + (f"\n[Open in AURORA]({url})" if url else "")
        return channel.target, {"msgtype": "markdown", "markdown": {"content": _clip(md, 3800)}}
    if channel.type == "feishu":
        if not url:
            return channel.target, {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
        return channel.target, {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": _clip(title, 100)}},
            "elements": [{"tag": "markdown", "content": _clip(text, 2000)},
                         {"tag": "action", "actions": [{"tag": "button", "type": "primary", "url": url,
                                                        "text": {"tag": "plain_text", "content": "Open in AURORA"}}]}]}}
    if channel.type == "slack":
        return channel.target, {"text": f"*{title}*\n{text}" + (f"\n<{url}|Open in AURORA>" if url else "")}
    if channel.type == "serverchan":
        return _serverchan_url(channel.target.strip()), {"title": _clip(title, 32), "desp": text + link}
    return channel.target, {"event": kind, "title": title, "text": text, "url": url,
                            "time": time.time(), **extra}


def _failure(channel: NotifyChannel, response: httpx.Response) -> str:
    """Why a delivery failed, or '' when it went through (some services answer 200 with an error code)."""
    if response.status_code >= 300:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    if channel.type in ("wecom", "feishu", "serverchan"):
        try:
            body = response.json()
        except ValueError:
            return ""
        code = body.get("errcode", body.get("code", body.get("StatusCode", 0)))
        if code not in (0, None):
            return f"{channel.type} error {code}: {body.get('errmsg') or body.get('msg') or body.get('message') or ''}"
    return ""


async def deliver(channel: NotifyChannel, kind: str, title: str, text: str, url: str = "",
                  extra: dict[str, Any] | None = None) -> str:
    """Send one notification; returns '' on success, else why it failed."""
    target, body = _request(channel, kind, title, text, url, extra or {})
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(target, json=body)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"
    return _failure(channel, response)


class Notifier:
    """Sends an experiment's notifications to the configured channels.

    *config_fn* gives the current settings each time, so channels added in the console reach
    experiments that are already running.  *link* is the experiment's page in the console.
    """

    def __init__(self, config_fn: Callable[[], NotificationsConfig], *, run_name: str = "",
                 link: str = "", extra: dict[str, Any] | None = None) -> None:
        self.config_fn = config_fn
        self.run_name = run_name
        self.link = link
        self.extra = extra or {}
        self._recent: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task] = set()

    async def on_event(self, event: Event) -> None:
        note = describe(event, self.run_name or "experiment")
        if note is not None:
            self.send(*note)

    def send(self, kind: str, title: str, text: str) -> None:
        """Queue a notification of *kind* to every channel that wants it (never blocks)."""
        try:
            config = self.config_fn()
        except Exception:
            logger.debug("notification settings unreadable", exc_info=True)
            return
        channels = [c for c in config.channels if c.enabled and kind in c.events]
        if not channels:
            return
        now = time.time()
        key = (kind, title)
        if now - self._recent.get(key, 0.0) < _DEDUPE_SECONDS:
            return
        self._recent[key] = now
        task = asyncio.create_task(self._send_all(channels, kind, title, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_all(self, channels: list[NotifyChannel], kind: str, title: str, text: str) -> None:
        for channel in channels:
            error = await deliver(channel, kind, title, text, self.link, self.extra)
            if error:
                logger.warning("Notification to %s (%s) failed: %s", channel.name or channel.type,
                               channel.type, error)

    async def drain(self, timeout: float = 20.0) -> None:
        """Wait for notifications still being sent (at the end of a run)."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)
