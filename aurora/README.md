# AURORA

An agent harness for long-horizon, open-ended computational materials science research.
Reproduced from the [NeurIPS 2026 AI4Mat Workshop paper](../ai4mat_neurips_workshop_AURORA.pdf).

AURORA orchestrates a full **ideation-to-paper pipeline**: multi-agent roundtable deliberation → idea screening → experiment planning → autonomous researcher loop → analysis → manuscript writing — with built-in JIT auditing, epistemic state management, and human oversight.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    ResearchEngine                         │
│  Roundtable → Screening → Planning → Preflight            │
│  → Researcher Loop → Analysis → Writing → (Follow-up)     │
└───┬────────────┬──────────┬──────────┬──────────┬────────┘
    │            │          │          │          │
┌───▼───┐  ┌────▼────┐ ┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│Agents │  │  State  │ │Skills │ │ Tools │ │ Hooks │
│       │  │         │ │       │ │       │ │       │
│Resear.│  │Artifacts│ │Struct │ │MockDFT│ │Audit  │
│Auditor│  │Mental   │ │Converg│ │MockASE│ │Check- │
│Analyz.│  │ Model   │ │Segreg │ │MockMLP│ │ point │
│Writer │  │Context  │ │Diagno.│ │Analys.│ │       │
│Round- │  │Reconstr.│ │Statis.│ │Visual.│ │       │
│ table │  │         │ │Visual.│ │Litera.│ │       │
└───────┘  └─────────┘ └───────┘ └───────┘ └───────┘
                  │
           ┌──────▼──────┐
           │  LLM Router │
           │Claude │ GPT  │
           │Gemini│DeepSk │
           └──────────────┘
```

### Three Core Mechanisms (from the paper)

1. **Execution Robustness** — Pre-flight verification, expert-distilled skills, JIT auditing
2. **Epistemic Adaptability** — Persistent research artifacts, scientific mental model rebuilt from evidence (not incrementally updated)
3. **Collective Intelligence** — Multi-agent roundtable with heterogeneous models, human oversight checkpoints

## Project Structure

```
aurora/
├── config.py              # Pipeline stage enums, YAML config loading
├── cli.py                 # CLI: run / status / resume / audit
├── models/                # Pydantic v2 data models (artifacts, mental model, audit, etc.)
├── llm/                   # Multi-provider LLM abstraction (Claude, OpenAI, Gemini, DeepSeek, Mock)
├── agents/                # Researcher, Auditor, Analyzer, Writer, Roundtable
├── state/                 # Workspace, ArtifactStore, MentalModelManager, ContextReconstructor
├── planning/              # Experiment planner, dependency DAG, preflight verification
├── screening/             # N×F×S idea scoring, novelty checking
├── skills/                # Domain skills (structure construction, segregation analysis, etc.)
├── tools/                 # Mock DFT/MLIP/ASE, real analysis (NumPy/pandas), visualization (Matplotlib)
├── hooks/                 # 8 JIT audit trigger types, human checkpoint
├── human/                 # Human checkpoint interface, intervention logging
└── core/                  # ResearchEngine, lifecycle, EventBus, ComponentRegistry
```

**74 Python files, ~14,500 lines of code.**

## Multi-Model LLM Support

Matching the paper's Table 6 role assignments:

| Pipeline Stage | Default Model | Provider |
|---|---|---|
| Roundtable Seats | heterogeneous (Claude, GPT, Gemini, DeepSeek) | mixed |
| Roundtable Chair | deepseek-chat (flash) | DeepSeek |
| Researcher | claude-opus | Anthropic |
| Auditor Triage | claude-haiku | Anthropic |
| Auditor Deep | claude-opus | Anthropic |
| Analyzer | claude-opus | Anthropic |
| Writer | claude-sonnet | Anthropic |

All providers implement the same `LLMProvider` ABC and are accessed through the `LLMRouter`.

## Quick Start

### Install

```bash
cd /path/to/AutoResearch-Harness
pip install -e .
```

### Run the Demo (no API keys needed)

```bash
python examples/simple_demo/run_demo.py
```

This runs the full pipeline with mock LLM providers in ~5 seconds:
- Roundtable deliberation (2 seats)
- Idea screening (N×F×S scoring)
- Experiment planning (3 jobs)
- Preflight verification
- Researcher loop (3 iterations)
- Analysis
- Manuscript writing

Output goes to `workspace/demo_simple_001/`.

### Run via CLI

```bash
python -m aurora run --config examples/simple_demo/config.yaml
python -m aurora status --workspace workspace/demo_simple_001
python -m aurora resume --workspace workspace/demo_simple_001
python -m aurora audit --workspace workspace/demo_simple_001
```

### Run a real campaign

The CLI and the web console run the same pipeline from the same config:
every phase (screening, planning, research, analysis, writing, audits) is a
Claude Code session working in one Docker container per campaign.

Requirements (checked when a campaign starts; a missing one stops it with
instructions):

- Claude Code CLI: `npm install -g @anthropic-ai/claude-code` (Node.js 18+)
- Docker, usable by your user (`docker info` works)
- `OPENROUTER_API_KEY=...` in `.env` at the project root (loaded automatically
  by both entry points)

```bash
python -m aurora run --config path/to/config.yaml   # CLI
uvicorn aurora.api.app:app --port 8000              # web console
```

Claude Code sessions may only read and edit files inside the campaign
workspace, and may only run shell commands as `docker exec` into the campaign
container. With `use_docker: false` they cannot run shell commands at all
unless `allow_host_bash: true` is set.

Set `mock_mode: true` to run offline (mock models and tools, no Claude Code,
no Docker, no API key), as the demo config does.

## Research Lifecycle (8 Stages per Iteration)

1. **Context Construction** — Priority-budgeted assembly (goal > contradictions > failures > evidence > claims)
2. **Scientific Reasoning** — LLM generates next action from reconstructed context
3. **Planning Check** — Validates proposed action against current plan
4. **Action Execution** — Invokes tools/skills to run computation
5. **Observation Ingestion** — Records structured results as research artifacts
6. **Scientific Interpretation** — Updates claims, confidence levels
7. **Validation/Auditing** — JIT audit hooks check consistency across 5 categories
8. **Continuation/Replanning/Escalation** — Decides next step based on audit outcome

## JIT Auditing System

**5 categories:** goal, plan, execution, evidence, epistemic

**7 outcomes:** continue, validate, replan, reframe, reject, recover, escalate

**8 triggers:** milestone completion, goal change, repeated failure (≥3), contradictory evidence, claim promotion, compute budget (>80%), termination request, periodic (every N iterations)

## Domain: Cu-Zn Surface Segregation

The paper's evaluation domain is adsorbate-induced surface segregation in Cu-Zn alloys for methanol synthesis catalysis. Mock tools generate physically plausible data including:

- Slab construction with dopant placement
- DFT-like energy calculations with Cu-Zn segregation energetics
- MLIP screening with configurable systematic bias
- Statistical validation (0.5 eV significance gate per paper)

## Dependencies

Python ≥ 3.11 | pydantic ≥ 2.0 | pyyaml | anthropic | openai | google-generativeai | numpy | pandas | scikit-learn | matplotlib
