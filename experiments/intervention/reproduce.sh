#!/usr/bin/env bash
# Intervention experiment: "Behavioral correction via auditor intervention" (Section 5) and Appendix C.
#
#   bash intervention/reproduce.sh         recompute the paper numbers from the released samples and judgements
#                                          (free, seconds)
#   bash intervention/reproduce.sh rerun   rebuild both rounds: select the failures, rebuild the agents' contexts,
#                                          generate wrong advice, sample the agents' next turns, judge them
#                                          (~$4 first round + ~$12 second round, ~20 min;
#                                           needs OPENROUTER_API_KEY and AutoResearchEval)
#
# Data: $AURORA_EXPERIMENTS_DATA or experiments/data (python scripts/download_data.py).
set -euo pipefail
cd "$(dirname "$0")/.."
DATA="${AURORA_EXPERIMENTS_DATA:-data}"
run() { python -m intervention.run_intervention "$@"; }

case "${1:-offline}" in
  offline)
    run summary --out-dir "$DATA/intervention/runs/main"
    run summary --out-dir "$DATA/intervention/runs/focus3"
    ;;
  rerun)
    # first round: two failures per agent model, all four conditions, two samples each
    main=runs/intervention/main
    run select --per-model 2 --seed 0 --out-dir "$main"
    run prep --out-dir "$main"
    run probe --out-dir "$main"
    run run --n 2 --out-dir "$main"
    run judge --out-dir "$main"
    run summary --out-dir "$main"
    # second round: every detected failure of three agent models; items shared with the first round are reused
    focus=runs/intervention/focus3
    run select --models glm-5.2 gpt-5-mini opus-4.8 --all --out-dir "$focus"
    run seed --from "$main" --out-dir "$focus"
    run prep --out-dir "$focus"
    run run --n 2 --conditions none specific wrong --out-dir "$focus"
    run judge --out-dir "$focus"
    run summary --out-dir "$focus"
    ;;
  *) echo "usage: $0 [offline|rerun]" >&2; exit 2 ;;
esac
