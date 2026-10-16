# Installation

## 1. Install the package

AURORA needs Python 3.11 or newer.

```bash
pip install aurora-research          # or, from a clone: pip install -e .
aurora demo                          # offline run with scripted mock models
```

`aurora demo` runs the whole pipeline in a few seconds without any API key,
Docker or Claude Code. It replays a small scripted materials study, so the
content is canned — it shows what a campaign produces and where.

## 2. What real campaigns need

| Requirement | Why | Check |
|---|---|---|
| **Claude Code CLI** | every phase agent, the milestone auditor and the live auditor are Claude Code sessions | `claude --version` |
| **Docker** | each campaign gets its own container in which the agents run code | `docker info` |
| **An OpenRouter API key** | one key covers all models (Claude, GPT, Gemini, Kimi, ...) | — |

Install Claude Code with `npm install -g @anthropic-ai/claude-code` (Node.js 18
or newer). You do not need an Anthropic account: AURORA points Claude Code at
OpenRouter, so it can drive any model OpenRouter serves.

Install Docker from <https://docs.docker.com/engine/install/> and make sure your
user can run `docker info` without `sudo` (on Linux: add yourself to the
`docker` group). You can run without Docker (`sandbox.docker: false`), but then
the agents can only read and write files — see [Sandbox and security](../guide/sandbox.md).

## 3. Add your API key

Create a `.env` file with your OpenRouter key, in the directory you run AURORA
from or in `~/.aurora/`:

```bash
mkdir -p ~/.aurora && echo "OPENROUTER_API_KEY=sk-or-..." > ~/.aurora/.env
```

An exported environment variable works too and takes precedence.

## 4. Check

```bash
aurora doctor
```

```text
AURORA 0.1.0, Python 3.12.4
  [ok] Claude Code CLI — 2.1.300 (Claude Code)
  [ok] Docker — /usr/bin/docker
  [ok] .env file — /home/you/.aurora/.env
  [..] data directory — /home/you/.aurora
  [ok] requirements of the default config
```

Every campaign runs the same checks when it starts and stops right away, with
instructions, if something is missing.

## Where AURORA keeps data

| What | Where |
|---|---|
| Campaigns started with `aurora run` | `./workspace/<campaign>/` (set `workspace.root`) |
| Web console projects and experiments | `~/.aurora/projects/` |
| Skill library | `~/.aurora/skills.db` |
| Web console database and event logs | `~/.aurora/` |

Set `AURORA_HOME` to move `~/.aurora` elsewhere.

## Installing from source

```bash
git clone https://github.com/titanresearchlabs/aurora && cd aurora
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm ci && npm run build && cd ..   # the web console
cp -r frontend/dist aurora/web
pytest
```
