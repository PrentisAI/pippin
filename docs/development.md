# Development

```bash
git clone https://github.com/prentisai/aurora && cd aurora
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,docs]"
pytest                                   # offline, about half a minute
ruff check aurora tests
python scripts/gen_config_reference.py   # after changing aurora/config.py
mkdocs serve -a localhost:8001           # docs at http://localhost:8001
cd frontend && npm ci && npm run dev     # console at :3000, proxies /api to `aurora serve` on :8000
```

The tests run entirely in mock mode and need no API key, Docker or Claude
Code.

## Code map

| Module | What it does |
|---|---|
| `aurora/config.py` | the configuration schema (`AuroraConfig`) and `load_config` |
| `aurora/core/engine.py` | `ResearchEngine`: the phase loop, milestone audits, budgets, checkpoints, wiring of the live auditor and the front desk |
| `aurora/core/terminal_iteration.py` | the research phase: one Claude Code session per job, acceptance tests |
| `aurora/core/agent_session.py` | a Claude Code session for one phase |
| `aurora/llm/agent_loops.py` | `ClaudeCodeLoop` (stream-json subprocess, message injection, interrupt) and the sandbox permission flags |
| `aurora/core/supervisor.py` | the live auditor |
| `aurora/core/human_gate.py` | human gates |
| `aurora/human/frontdesk.py` | the front desk agent |
| `aurora/agents/roundtable/` | multi-agent ideation (seats, chair, meeting) |
| `aurora/core/docker_backend.py`, `execution_backend.py` | the container per campaign / the host fallback |
| `aurora/core/requirements.py` | start-up checks |
| `aurora/state/` | campaign directory layout, artifact store |
| `aurora/skills/` | skill library (`store.py`), writing skills for Claude Code (`materializer.py`); `materials/` are the simulated skills of mock mode |
| `aurora/api/` | the web console's API |
| `aurora/llm/router.py`, providers | direct LLM calls (ideation, front desk, mock mode) |
| `aurora/core/lifecycle.py`, `planning/`, `screening/`, `agents/analyzer.py`, `agents/writer.py`, `tools/`, `hooks/` | the scripted pipeline of mock mode |
| `frontend/` | the web console (React + Vite) |

## Conventions

- Real runs go through Claude Code sessions; direct API calls are used only
  where no tools are needed (ideation, the front desk, skill distillation).
- Fail loudly: a missing output or a failed call should be visible to the user
  (an error, an event, or a human gate), never silently replaced by a default.
- Every configuration field has a description; it ends up in the reference.

See `CONTRIBUTING.md` for how to propose changes.
