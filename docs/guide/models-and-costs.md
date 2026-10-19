# Models and costs

## One key, any model

All roles use OpenRouter model IDs (`vendor/model`), so one
`OPENROUTER_API_KEY` covers every model:

| Role | Config key | Default |
|---|---|---|
| Phase agents (screening, planning, research, analysis, writing) | `models.agent` | `anthropic/claude-sonnet-5.5` |
| One phase's agent, overriding `models.agent` | `models.screening`, `models.planning`, `models.research`, `models.analysis`, `models.writing` | empty (use `models.agent`) |
| Ideation seats | `models.ideation_seats` | Claude Sonnet 5.5, GPT-5.6, Gemini 3.1 Pro |
| Ideation chair | `models.ideation_chair` | `moonshotai/kimi-k3` |
| Milestone auditor | `models.auditor` | `anthropic/claude-sonnet-5.5` |
| Live auditor | `models.supervisor` | `anthropic/claude-sonnet-5.5` |
| Front desk | `models.frontdesk` | `anthropic/claude-haiku-5.5` |

For example, a strong model for research and cheaper ones elsewhere:

```yaml
models:
  agent: anthropic/claude-sonnet-5.5
  research: anthropic/claude-opus-5.5
  writing: anthropic/claude-haiku-5.5
```

In the web console the same settings are on the proposal card under
*Advanced settings*, with the server's base config as defaults. Every model ID
is checked against OpenRouter's list when a campaign starts; a typo stops it at
once with the closest valid IDs.

The defaults are the models used in the paper. The agent, auditor and
supervisor roles run inside Claude Code, which works best with Claude models but
accepts any model OpenRouter serves with tool use. For ideation, heterogeneity
matters more than any single model: keep seats from different families and the
chair from yet another one.

To use another OpenRouter-compatible endpoint, set `api_base_url` and
`api_key`. To call a provider directly for the ideation seats or the chair (for
example `claude-...` model IDs with an Anthropic key), install the extra
(`pip install "aurora-research[anthropic]"`) and add it under `providers`.

## What a campaign costs

Cost depends mostly on how long the research agent works. Rough guide with the
default models:

| Campaign | Typical cost |
|---|---|
| `examples/quickstart` | US$1–3 |
| a focused task (skip ideation and screening, 2–4 jobs) | US$3–15 |
| an open question with the full pipeline | US$15–50 |
| a multi-day case study | hundreds of US$ |

Multi-agent ideation costs cents to a few dollars (`independent` is about four
times cheaper than `roundtable`). Each milestone audit is a short session. The
live auditor reads the whole research transcript, so for long research it is a
noticeable share; use a cheaper `models.supervisor` if needed.

## Limiting spend

```yaml
budget:
  max_cost_usd: 20      # stop cleanly when reached
  max_hours: 6
agent:
  research_max_turns: 150
  research_timeout_minutes: 45
  max_research_iterations: 5
  max_followup_cycles: 1
```

The campaign prints and records its cost (`cost_usd` in the result and in the
web console). The figure is what the providers report; check your OpenRouter
dashboard for the authoritative amount.
