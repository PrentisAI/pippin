#!/usr/bin/env bash
# Multi-agent ideation: "Collective intelligence in ideation" (Section 5) and Appendix C
# ("Ideation scoring", "Multi-agent ideation evaluation").
#
#   bash ideation/reproduce.sh             recompute the paper numbers from the released runs and judgements
#                                          (free, seconds)
#   bash ideation/reproduce.sh rerun       run the 21 held-out tasks again (pool + group meeting) and judge them
#                                          (~$73 for the runs, ~$5 for the two ranking judges, ~3 h at concurrency 6),
#                                          and re-judge the agents' own proposals (~$3.3)
#                                          (needs OPENROUTER_API_KEY and AutoResearchEval)
#
# Data: $AURORA_EXPERIMENTS_DATA or experiments/data (python scripts/download_data.py).
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-offline}" in
  offline)
    echo "== group meeting vs independent selection (paper: 20 tasks, W4409170628 excluded) =="
    python -m ideation.analyze_meeting
    echo; echo "== blind ranking judges (all 21 tasks) =="
    python -m ideation.analyze_judges
    echo; echo "== the AutoResearchEval agents' own proposals =="
    python -m ideation.summarize_agent_ideas
    ;;
  rerun)
    out=runs/ideation/meeting_test
    python -m ideation.run_meeting --split test --conditions pool heterogeneous --concurrency 6 --out-dir "$out"
    python -m ideation.judge meeting --runs-dir "$out" --judge x-ai/grok-4.7 --out "$out/_judge_grok.json"
    python -m ideation.judge meeting --runs-dir "$out" --judge qwen/qwen3.8-max-0902 --out "$out/_judge_qwen.json"
    python -m ideation.analyze_meeting "$out" --annotations none
    python -m ideation.analyze_judges "$out/_judge_grok.json" "$out/_judge_qwen.json"
    agents=runs/ideation/agent_ideas
    python -m ideation.build_agent_inputs --out "$agents/ideation_inputs.json"
    python -m ideation.judge agents --inputs "$agents/ideation_inputs.json" --judge openai/gpt-5.6-sol \
        --budget 6 --out "$agents/ideation_scores_gpt.json"
    python -m ideation.judge agents --inputs "$agents/ideation_inputs.json" --judge google/gemini-3.1-pro-preview \
        --tasks 15 --budget 2 --out "$agents/ideation_scores_gemini15.json"
    python -m ideation.summarize_agent_ideas "$agents/ideation_scores_gpt.json" "$agents/ideation_scores_gemini15.json" \
        --inputs "$agents/ideation_inputs.json"
    ;;
  *) echo "usage: $0 [offline|rerun]" >&2; exit 2 ;;
esac
