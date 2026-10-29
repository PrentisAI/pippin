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
from datetime import datetime
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
    if name == "estimate_exceeded":
        return ("estimate_exceeded", f"Over the estimate: {run}", str(d.get("detail", "")))
    if name == "agent_reply_to_human":
        return ("agent_reply", f"The research agent answered: {run}", _clip(str(d.get("text", "")), 800))
    if name == "skill_suggestion":
        return ("skill_suggestion", f"Skill proposed: {d.get('name', '')}",
                f"{run}\n\n{_clip(str(d.get('text', '')), 500)}")
    if name == "compute_job_ended":
        return ("compute_job", f"Cluster job {d.get('state', '').lower()}: {d.get('name', '')}",
                f"{run}\nJob {d.get('id')} ({_where(d)}), ran {_duration(d.get('elapsed_seconds', 0))}"
                + (f", exit code {d['exit_code']}" if d.get("exit_code") is not None else "") + ".")
    return None


def _where(d: dict[str, Any]) -> str:
    if d.get("backend") == "ssh":
        return f"on {d.get('node') or 'the GPU machine'}" + (f", GPU {d['gpu_ids']}" if d.get("gpu_ids") else "")
    return f"SLURM {d.get('slurm_id')}"


def _serverchan_url(key: str) -> str:
    # Server酱³ keys (sctp<uid>t...) have their own host; Turbo keys use sctapi.ftqq.com
    m = re.match(r"sctp(\d+)t", key)
    return f"https://{m.group(1)}.push.ft07.com/send/{key}.send" if m else f"https://sctapi.ftqq.com/{key}.send"


def _request(channel: NotifyChannel, kind: str, title: str, text: str, url: str,
             extra: dict[str, Any], actions: list[tuple[str, str]] | None = None) -> tuple[str, dict[str, Any]]:
    """The URL and JSON body a channel takes.  ``actions``: [(label, url)] answered from the message."""
    actions = actions or []
    links = ([("Open in Pippin", url)] if url else []) + actions
    if channel.type == "wecom":
        md = f"**{title}**\n{text}" + ("\n" + " · ".join(f"[{label}]({u})" for label, u in links) if links else "")
        return channel.target, {"msgtype": "markdown", "markdown": {"content": _clip(md, 3800)}}
    if channel.type == "feishu":
        if not links:
            return channel.target, {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
        buttons = [{"tag": "button", "type": "primary" if i == 0 else "default", "url": u,
                    "text": {"tag": "plain_text", "content": label}} for i, (label, u) in enumerate(links)]
        return channel.target, {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": _clip(title, 100)}},
            "elements": [{"tag": "markdown", "content": _clip(text, 2000)}, {"tag": "action", "actions": buttons}]}}
    if channel.type == "slack":
        return channel.target, {"text": f"*{title}*\n{text}" + ("\n" + " · ".join(f"<{u}|{label}>" for label, u in links)
                                                                    if links else "")}
    if channel.type == "serverchan":
        desp = text + ("\n\n" + "\n\n".join(f"[{label}]({u})" for label, u in links) if links else "")
        return _serverchan_url(channel.target.strip()), {"title": _clip(title, 32), "desp": desp}
    return channel.target, {"event": kind, "title": title, "text": text, "url": url,
                            "actions": [{"label": label, "url": u} for label, u in actions],
                            "time": time.time(), **extra}


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = (int(x) for x in hhmm.strip().split(":"))
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def in_quiet_hours(config: NotificationsConfig, now: datetime | None = None) -> bool:
    """Whether *now* (the server's local time) is within the quiet hours (which may span midnight)."""
    start, end = _minutes(config.quiet_start), _minutes(config.quiet_end)
    if start is None or end is None or start == end:
        return False
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    return start <= t < end if start < end else (t >= start or t < end)


def held_digest(held: list[dict[str, Any]]) -> tuple[str, str]:
    """One message for what was held during quiet hours."""
    lines = []
    for h in held[:30]:
        lines.append(f"- {h['title']}" + (f": {_clip(h['text'].splitlines()[0] if h['text'] else '', 160)}" if h.get("text") else "")
                     + (f" ({h['url']})" if h.get("url") else ""))
    more = f"\n…and {len(held) - 30} more" if len(held) > 30 else ""
    return f"While your notifications were quiet: {len(held)}", "\n".join(lines) + more


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
                  extra: dict[str, Any] | None = None, actions: list[tuple[str, str]] | None = None) -> str:
    """Send one notification; returns '' on success, else why it failed."""
    target, body = _request(channel, kind, title, text, url, extra or {}, actions)
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
                 link: str = "", extra: dict[str, Any] | None = None,
                 actions: Callable[[Event], list[tuple[str, str]]] | None = None,
                 hold: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.config_fn = config_fn
        self.run_name = run_name
        self.link = link
        self.extra = extra or {}
        # [(label, url)] answering a decision from the message (the console's signed links)
        self.actions = actions
        # keeps a message held in quiet hours (the console sends them together afterwards); without it
        # they are kept here and sent when the quiet hours end, if the run is still going
        self.hold = hold
        self._held: list[dict[str, Any]] = []
        self._flusher: asyncio.Task | None = None
        self._recent: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task] = set()

    async def on_event(self, event: Event) -> None:
        note = describe(event, self.run_name or "experiment")
        if note is not None:
            actions: list[tuple[str, str]] = []
            if event.name == "human_gate_opened" and self.actions is not None:
                try:
                    if self.config_fn().action_links:
                        actions = self.actions(event)
                except Exception:
                    logger.debug("decision links could not be made", exc_info=True)
            self.send(*note, actions=actions)

    def send(self, kind: str, title: str, text: str, actions: list[tuple[str, str]] | None = None) -> None:
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
        if in_quiet_hours(config) and kind not in config.quiet_except:
            item = {"kind": kind, "title": title, "text": text, "url": self.link, "at": now}
            if self.hold is not None:
                self.hold(item)
            else:
                self._held.append(item)
                if self._flusher is None or self._flusher.done():
                    self._flusher = asyncio.create_task(self._flush_after_quiet())
            return
        task = asyncio.create_task(self._send_all(channels, kind, title, text, actions or []))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush_after_quiet(self) -> None:
        while self._held:
            await asyncio.sleep(60)
            config = self.config_fn()
            if in_quiet_hours(config):
                continue
            held, self._held = self._held, []
            kinds = {h["kind"] for h in held}
            title, text = held_digest(held)
            await self._send_all([c for c in config.channels if c.enabled and kinds & set(c.events)],
                                 "held", title, text, [])

    async def _send_all(self, channels: list[NotifyChannel], kind: str, title: str, text: str,
                        actions: list[tuple[str, str]] | None = None) -> None:
        for channel in channels:
            error = await deliver(channel, kind, title, text, self.link, self.extra, actions)
            if error:
                logger.warning("Notification to %s (%s) failed: %s", channel.name or channel.type,
                               channel.type, error)

    async def drain(self, timeout: float = 20.0) -> None:
        """Wait for notifications still being sent (at the end of a run)."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)
