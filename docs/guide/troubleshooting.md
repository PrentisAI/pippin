# Troubleshooting

Start with `aurora doctor [config]`: it checks everything a campaign needs.

**"Claude Code CLI not found"** — install it (`npm install -g
@anthropic-ai/claude-code`, Node.js 18+) and make sure `claude` is on the `PATH`
of the shell that runs Pippin. On clusters, batch jobs often have a different
`PATH` than your login shell; add the npm global bin directory in the job script.

**"Docker is required ... the Docker daemon is not reachable"** — start Docker,
or add your user to the `docker` group (Linux) and log in again. Check with
`docker info`. Or run without Docker (`sandbox.docker: false`).

**"No API key"** — put `OPENROUTER_API_KEY=...` in `.env` in the current
directory or in `~/.aurora/`, or export it.

**A config key is "not permitted" / "Extra inputs are not permitted"** — a typo
in the YAML; the message names the key. See the
[configuration reference](../reference/configuration.md).

**Every ideation seat failed** — usually a wrong model ID or no credit on the
key. Model IDs must exist on OpenRouter (`vendor/model`).

**The campaign waits forever** — a human gate is open and `human.autonomy` is
`supervised`. Answer it (terminal prompt, web console, or front desk chat), or
use `autonomous` for unattended runs. Gates also time out after
`human.gate_timeout_minutes`.

**"All N research jobs failed"** — the campaign stops before analysis so it
does not write up results that do not exist. Look at
`phases/research/research_loop.jsonl` and the log for the error; common causes
are a container image without the needed tools and no network to install them,
or a time limit (`agent.research_timeout_minutes`) that is too short.

**The agent says a command was denied** — that is the sandbox: on the host the
agents may only edit files in the campaign directory and run commands in the
container. See [Sandbox and security](sandbox.md).

**The browser extension's side panel says "Pippin isn't running"** (or stays blank) — no console answers at the
address in the extension's options, nor on ports 8000, 8200 or 8002 of this machine. Start one (`aurora serve`, or
open the desktop app) and click *try again*; for a console elsewhere, put its address in the extension's options.

**The web console shows "No web console build found"** — in a source checkout,
build the frontend: `cd frontend && npm ci && npm run build && cp -r dist ../aurora/web`.

**Resuming** — `aurora resume <campaign dir>` (CLI) or *Resume* (web console).
To change limits first, edit `<campaign dir>/config.yaml`.
