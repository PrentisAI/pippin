# Reproducing the paper

The repository's `experiments/` directory contains the component-wise
evaluation of the paper — the live auditor, the milestone auditor, auditor
interventions and multi-agent ideation — together with the released outputs of
the paper's runs. Every reported number can be recomputed **offline and for
free**; every experiment can also be **re-run** with models.

```bash
pip install -e .                       # from the repository root
cd experiments
pip install -r requirements.txt
python scripts/download_data.py --skip-are   # released outputs (8.5 MB); enough to recompute
bash supervisor/reproduce.sh                 # likewise auditor/, intervention/, ideation/
```

| Paper result | Experiment | Re-run cost |
|---|---|---|
| Live auditor flags 93/95 annotated failures, 59 before their onset | `supervisor/` | ~US$45, 30 min |
| Milestone auditor finds 100/106 visible failures; 88% of findings valid | `auditor/` | ~US$16, 10 min |
| Specific advice raises corrections from 3/28 to 16/28 and 2/70 to 41/70; wrong advice followed 39/44 | `intervention/` | ~US$17, 20 min |
| Three models give more distinct directions than one (chair: same study 19/20, 12/20 vs 4/20); meeting statistics and cost | `ideation/` | ~US$78, 3 h |

The evaluations run on recorded agent trajectories from
[AutoResearchEval](https://huggingface.co/datasets/PrentisAI/AutoResearchEval),
which `download_data.py` fetches for re-runs.

The released outputs were produced with the pre-release implementation; a
re-run with the released package gives comparable, not identical, numbers. See
`experiments/README.md` and each experiment's README for details.
