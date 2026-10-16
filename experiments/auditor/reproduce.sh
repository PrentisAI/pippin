#!/usr/bin/env bash
# Milestone auditor on final deliverables: Table 1 "Milestone" column, Figure 3b, Appendix C.
#
#   bash auditor/reproduce.sh              recompute the paper numbers from the released outputs (free, seconds)
#   bash auditor/reproduce.sh rerun        audit the 52 packages and judge them again
#                                          (~$12 auditor + ~$4 judge, ~10 min at concurrency 8;
#                                           needs OPENROUTER_API_KEY, Docker, Claude Code)
#
# Data: $AURORA_EXPERIMENTS_DATA or experiments/data (python scripts/download_data.py).
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-offline}" in
  offline)
    python -m auditor.summarize
    ;;
  rerun)
    image=aurora-auditor-sandbox:py311-sci
    docker image inspect "$image" >/dev/null 2>&1 || docker build -t "$image" auditor
    out=runs/auditor/test_sonnet
    python -m auditor.run_auditor --model anthropic/claude-sonnet-5.5 --image "$image" --concurrency 8 --out-dir "$out"
    python -m auditor.score --runs "$out" --judge openai/gpt-5.6-sol
    python -m auditor.summarize "$out/scores.json"
    ;;
  *) echo "usage: $0 [offline|rerun]" >&2; exit 2 ;;
esac
