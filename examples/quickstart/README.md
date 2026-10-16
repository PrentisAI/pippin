# Quickstart

A real (non-mock) campaign that finishes in a few minutes and costs a few
dollars. It shows the full loop on a small, checkable problem: the data in
`data/measurements.csv` was generated with a known ground truth (see the end of
this file — read it only after the run).

```bash
cd examples/quickstart
aurora doctor aurora.yaml        # checks Claude Code, Docker and your API key
aurora run aurora.yaml
```

What to look at afterwards (`workspace/<campaign>/`):

| Path | What it is |
|---|---|
| `phases/writing/draft_1.md` | the final report |
| `workspace/` | everything the research agent produced in the container (code, figures) |
| `phases/planning/round_0/plan.json` | the plan the planning agent wrote |
| `audit_records/` | the milestone auditor's verdict on every hand-off |
| `phases/supervisor/` | what the live auditor said while the agent worked |
| `human_gates.jsonl` | decisions you were asked for (if any) |

<details>
<summary>Ground truth (spoilers)</summary>

`yield = 2·temperature + sin(3·pressure) + noise(σ = 0.3)`.
`catalyst_loading` has no effect. `flow_rate` is correlated with temperature
(r ≈ 0.8) but has no effect of its own. A good report finds the linear
temperature effect (≈ 2), the non-linear pressure effect, no effect of
catalyst_loading, and explains that flow_rate only looks predictive because of
its correlation with temperature.
</details>
