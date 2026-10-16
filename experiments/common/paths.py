"""Where the experiments read their inputs and write their outputs.

* ``--data-dir`` (env ``AURORA_EXPERIMENTS_DATA``, default ``experiments/data``): the unpacked
  release data (``scripts/download_data.py`` puts it there).  Layout::

      <data-dir>/MANIFEST.md
      <data-dir>/supervisor/  auditor/  intervention/  ideation/     (our release tarball)
      <data-dir>/AutoResearchEval/open-ended/...                       (Hugging Face dataset)

* ``--are-dir`` (env ``AUTORESEARCHEVAL_DIR``, default ``<data-dir>/AutoResearchEval``): the
  AutoResearchEval dataset, needed only by scripts that read trajectories or tasks.
* ``--out-dir``: where a script writes new results.  Defaults to ``experiments/runs/<experiment>/...``,
  never into the released data.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

EXPERIMENTS_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = EXPERIMENTS_DIR / "runs"
DATA_ENV = "AURORA_EXPERIMENTS_DATA"
ARE_ENV = "AUTORESEARCHEVAL_DIR"


def default_data_dir() -> Path:
    return Path(os.environ.get(DATA_ENV) or EXPERIMENTS_DIR / "data")


def add_data_args(ap: argparse.ArgumentParser, *, out: str | None = None, are: bool = False) -> None:
    """Add ``--data-dir`` (and ``--out-dir`` when *out* names its default, ``--are-dir`` when *are*)."""
    ap.add_argument("--data-dir", type=Path, default=default_data_dir(),
                    help=f"unpacked release data (default: ${DATA_ENV} or experiments/data)")
    if out is not None:
        ap.add_argument("--out-dir", type=Path, default=RUNS_DIR / out,
                        help=f"where to write results (default: experiments/runs/{out})")
    if are:
        ap.add_argument("--are-dir", type=Path, default=None,
                        help=f"AutoResearchEval dataset (default: ${ARE_ENV} or <data-dir>/AutoResearchEval)")


def are_root(args: argparse.Namespace) -> Path:
    """The AutoResearchEval dataset directory selected by *args*."""
    explicit = getattr(args, "are_dir", None)
    if explicit:
        return Path(explicit)
    if os.environ.get(ARE_ENV):
        return Path(os.environ[ARE_ENV])
    return Path(args.data_dir) / "AutoResearchEval"


def require(path: Path, what: str) -> Path:
    """Fail with a pointer to the download script when an input is missing."""
    if not Path(path).exists():
        raise SystemExit(f"{what} not found at {path}.\n"
                         f"Run `python scripts/download_data.py` (from experiments/) or pass --data-dir.")
    return Path(path)
