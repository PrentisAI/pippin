# Command line

```text
aurora demo                    run the offline demo (no API key, no Docker)
aurora init [PATH]             write a commented starter config (default aurora.yaml)
aurora doctor [CONFIG]         check Claude Code, Docker, the API key and the config
aurora run CONFIG              run a campaign
aurora run -o "objective"      run a campaign with the default settings
aurora status CAMPAIGN_DIR     show where a campaign is
aurora resume CAMPAIGN_DIR     continue a campaign from its last checkpoint
aurora serve                   start the web console
aurora skills list|add|remove  manage the skill library
```

## `aurora run`

```bash
aurora run aurora.yaml
aurora run aurora.yaml -o "A different objective" --name my-run
aurora run -o "Is the effect in data/x.csv robust to outliers?"
aurora run aurora.yaml --mock          # same config, offline
```

| Option | Meaning |
|---|---|
| `-o, --objective` | objective (overrides the config's) |
| `--name` | campaign directory name (default: from the objective and the time) |
| `--mock` | mock mode |
| `-v` | debug logging |

Exit codes: `0` completed or stopped, `1` failed, `2` requirements missing,
`130` interrupted (Ctrl-C; resume later).

In an interactive terminal, human gates ask at the prompt. When stdin is not a
terminal (a batch job), set `human.autonomy: autonomous` or the gates wait
until `human.gate_timeout_minutes` passes.

## `aurora resume`

```bash
aurora resume workspace/my-run
```

Uses the configuration saved in the campaign directory (`config.yaml`, without
secrets) and continues from `checkpoint.json`. To change limits before resuming
(say, a larger budget), edit that `config.yaml`.

## `aurora serve`

```bash
aurora serve                               # http://localhost:8000
aurora serve --port 9000 --config base.yaml
```

`--config` is the base configuration for experiments started from the console
(models, sandbox image, budgets, autonomy); its objective is ignored. The
console has no login, so by default it listens on 127.0.0.1 only. On a remote
machine, use an SSH tunnel: `ssh -L 8000:localhost:8000 server`.

## `aurora skills`

```bash
aurora skills add our-plotting-style "Label axes with units; use SI units; ..." --description "Figure conventions"
aurora skills add dft-protocol @protocol.md
aurora skills list
aurora skills remove our-plotting-style
```

Skills added here are global: every new campaign can use them.
