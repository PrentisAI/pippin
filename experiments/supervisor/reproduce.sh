#!/usr/bin/env bash
# Live auditor (supervisor) replay: Table 1 "Live" column, Figure 3, Appendix C.
#
#   bash supervisor/reproduce.sh           recompute the paper numbers from the released outputs (free, seconds)
#   bash supervisor/reproduce.sh rerun     replay the 46 trajectories and judge them again
#                                          (~$41 live auditor + ~$4 judge, ~30 min at concurrency 8;
#                                           needs OPENROUTER_API_KEY, Docker, Claude Code, AutoResearchEval)
#
# Data: $AURORA_EXPERIMENTS_DATA or experiments/data (python scripts/download_data.py).
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-offline}" in
  offline)
    python -m supervisor.summarize
    ;;
  rerun)
    out=runs/supervisor/test_sonnet_pi
    python -m supervisor.replay --model anthropic/claude-sonnet-5.5 --prompt-variant pi --chunk 5 \
        --concurrency 8 --out-dir "$out"
    python -m supervisor.score --runs "$out" --judge openai/gpt-5.6-sol
    python -m supervisor.summarize "$out/scores.json"
    ;;
  *) echo "usage: $0 [offline|rerun]" >&2; exit 2 ;;
esac
