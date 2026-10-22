"""``aurora quickstart``: the first minutes in the terminal.

Four steps, each skippable: check this machine, set the model key, watch the offline demo, open
the web console (which then shows the welcome wizard).  Nothing is changed without asking.
"""

from __future__ import annotations

import getpass
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_IMAGE = "python:3.11-slim"


def _ask(question: str, default: bool, assume: bool) -> bool:
    if assume or not sys.stdin.isatty():
        return default if not assume else True
    answer = input(f"  {question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def _ok(text: str) -> None:
    print(f"  ✓ {text}")


def _todo(text: str) -> None:
    print(f"  · {text}")


def _bad(text: str) -> None:
    print(f"  ! {text}")


def _run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def check_machine(assume: bool) -> bool:
    """Python, Claude Code, Docker and the image; returns whether a real run can start."""
    print("[1/4] This machine")
    ready = True
    _ok(f"Python {sys.version.split()[0]}")
    claude = shutil.which("claude")
    if claude:
        code, version = _run([claude, "--version"])
        _ok(f"Claude Code {version.split()[0] if code == 0 and version else ''}".rstrip())
    else:
        ready = False
        _bad("Claude Code not found: the agents run in it. Install it with "
             "npm install -g @anthropic-ai/claude-code (Node.js 18+).")
    if not shutil.which("docker"):
        ready = False
        _bad("Docker not found: each run gets its own container. https://docs.docker.com/engine/install/")
    elif _run(["docker", "info"], 30)[0] != 0:
        ready = False
        _bad("Docker is installed but this user cannot reach it: on Linux, add yourself to the docker group.")
    else:
        _ok("Docker answers")
        if _run(["docker", "image", "inspect", DEFAULT_IMAGE])[0] == 0:
            _ok(f"container image {DEFAULT_IMAGE}")
        elif _ask(f"The container image {DEFAULT_IMAGE} is not pulled yet. Pull it in the background?", True, assume):
            subprocess.Popen(["docker", "pull", DEFAULT_IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _todo(f"pulling {DEFAULT_IMAGE} in the background (a few minutes the first time)")
        else:
            _todo(f"the first run will pull {DEFAULT_IMAGE}")
    return ready


def set_key(assume: bool) -> bool:
    """The OpenRouter key: found, or asked for, checked and saved."""
    import os

    from aurora.env import KEY_PATTERN, check_openrouter_key, save_openrouter_key
    print("[2/4] Model key")
    if os.environ.get("OPENROUTER_API_KEY"):
        _ok("an OpenRouter key is set")
        return True
    if not sys.stdin.isatty():
        _todo("no key: set OPENROUTER_API_KEY, or paste it in the web console")
        return False
    print("  One key for every model (Claude, GPT, Gemini, DeepSeek, ...): openrouter.ai → Keys.")
    for _ in range(3):
        key = getpass.getpass("  OpenRouter key (sk-or-…, not shown as you type; Enter to skip): ").strip()
        if not key:
            _todo("skipped: the web console asks for it too")
            return False
        if not re.fullmatch(KEY_PATTERN, key):
            _bad("that does not look like an OpenRouter key (sk-or-…)")
            continue
        accepted = check_openrouter_key(key)
        if accepted is False:
            _bad("OpenRouter does not accept this key")
            continue
        path = save_openrouter_key(key)
        _ok(("OpenRouter accepted it" if accepted else "could not reach OpenRouter; kept it anyway")
            + f" · saved in {path}, readable only by you")
        return True
    return False


def watch_demo(assume: bool) -> None:
    print("[3/4] See a run")
    if not _ask("Watch the offline demo first (seconds, no key, no cost)?", True, assume):
        _todo("skipped: aurora demo runs it any time")
        return
    import contextlib
    import io
    import logging

    from aurora.cli import _run_engine
    from aurora.config import AuroraConfig
    root = Path(tempfile.mkdtemp(prefix="aurora-demo-"))
    config = AuroraConfig(
        objective=("Investigate surface segregation of Zn in Cu(111) and its effect on CO adsorption, "
                   "as a step towards Cu-Zn methanol synthesis catalysts."),
        campaign_id="demo", mock_mode=True, workspace={"root": str(root)})
    logging.disable(logging.WARNING)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = _run_engine(config)
    finally:
        logging.disable(logging.NOTSET)
    reports = sorted((root / "demo" / "phases" / "writing").glob("draft_*.md"))
    if code == 0 and reports:
        _ok("ideation · screening · planning · pre-flight · research · analysis · writing")
        _todo(f"its report: {reports[-1]}")
    else:
        _bad("the demo did not finish; aurora demo -v shows why")


def open_console(assume: bool, port: int) -> None:
    print("[4/4] The web console")
    if not _ask(f"Start it now on http://localhost:{port} and open it in your browser?", True, assume):
        _todo("later: aurora serve")
        return
    import threading
    import webbrowser
    threading.Timer(2.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    from argparse import Namespace

    from aurora.cli import cmd_serve
    cmd_serve(Namespace(host="127.0.0.1", port=port, config=None))


def main(assume: bool = False, port: int = 8000, serve: bool = True) -> int:
    from aurora import __version__
    print(f"AURORA {__version__} — a few steps and you are running.\n")
    ready = check_machine(assume)
    print()
    have_key = set_key(assume)
    print()
    watch_demo(assume)
    print()
    missing = ([] if ready else ["what is marked ! above"]) + ([] if have_key else ["a model key"])
    if missing:
        print(f"  Real runs still need {' and '.join(missing)}.")
    print("  Next: aurora init   a commented config for your own question\n"
          "        aurora run    run it from the terminal\n")
    if serve:
        open_console(assume, port)
    return 0
