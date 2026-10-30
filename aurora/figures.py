"""Figures the agents draw by themselves when trying something out says more than words: a small page (HTML with
its own script, nothing from the network) that the console shows in a sandboxed frame, its source a click away.

An agent decides when one is worth it (a quantity that changes with a setting, a trade-off, a toy simulation of an
idea, numbers easier to compare by moving them) and writes a brief; :func:`draw` turns the brief into the page.
Pippin's notes in the reader make their own (see :mod:`aurora.literature.companion`); the brainstorm's agents keep
theirs here.

Table (in ``AURORA_HOME/aurora.db``): ``figures``.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS figures (
    fig_id     TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,             -- who it was drawn for
    space      TEXT NOT NULL DEFAULT '',  -- a project, or user:<id>
    chat_id    TEXT NOT NULL DEFAULT '',
    about      TEXT NOT NULL DEFAULT '',
    html       TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_DRAW = """\
Write a small interactive figure for a researcher, shown in a conversation inside a sandboxed frame about 600 \
pixels wide. It should let them try something out, so they see it rather than read about it.

What it is about: {about}

What to show, and what they can change:
{brief}

The numbers to use (and where they come from):
{numbers}

Rules:
- One complete HTML document: inline <style> and <script>, nothing loaded from the network (no CDN, no fonts, no \
images from elsewhere). Draw with SVG or a canvas.
- Use the numbers given. Anything you have to assume (a curve between two points, a made-up example signal, a toy \
model) is labelled as assumed, in the figure.
- One or two controls (a slider, a few buttons) that change what is drawn at once. A title of a few words, one line \
under it saying what to try.
- Plain, quiet styling: system sans-serif font, 13px, dark grey text on white, one accent colour (#2f6fde), thin \
lines. At most 360 pixels tall. Round every number shown.
- No explanation outside the figure; no comments in the code.

Answer with the HTML document only."""


async def draw(access: Any, *, about: str, brief: str, numbers: str = "", client: Any = None) -> str:
    """The figure's page for a brief ('' when none could be made)."""
    from aurora.literature.companion import clean_figure, model_client
    if not access.api_key or not brief:
        return ""
    client = client or model_client(access, 300)
    prompt = _DRAW.format(about=about or "(untitled)", brief=brief,
                          numbers=numbers or "None given: a toy example, labelled as such.")
    resp = await client.chat.completions.create(model=access.model, temperature=0.3, max_tokens=16000,
                                                messages=[{"role": "user", "content": prompt}])
    return clean_figure(resp.choices[0].message.content or "")


# what the page sends the console so the frame takes its height (the console listens to that frame only)
TELL = ("<script>(function(){function t(){parent.postMessage({figureHeight:document.documentElement.scrollHeight},'*')}"
        "new ResizeObserver(t).observe(document.documentElement);addEventListener('load',t);t()})()</script>")


class FigureStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    def add(self, owner: str, html: str, *, space: str = "", chat_id: str = "", about: str = "") -> dict[str, Any]:
        fig_id = "f_" + uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute("INSERT INTO figures VALUES (?,?,?,?,?,?,?)",
                               (fig_id, owner, space, chat_id, about[:300], html, time.time()))
            self._conn.commit()
        return self.get(fig_id)  # type: ignore[return-value]

    def get(self, fig_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM figures WHERE fig_id=?", (fig_id,)).fetchone()
        return dict(row) if row else None


class _Lazy:
    """Opened on first use, so ``AURORA_HOME`` is read then (tests switch it per test)."""

    def __init__(self) -> None:
        self._store: FigureStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = FigureStore()
        return getattr(self._store, name)


figures = _Lazy()
