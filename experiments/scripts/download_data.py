"""Download the data of the paper-reproduction experiments into --data-dir (default experiments/data).

1. Our release tarball (labels, deliverable packages, outputs of the paper's model runs and judges),
   from a GitHub Release asset, checked against its SHA-256 and unpacked into <data-dir>/.
   This is all that is needed to recompute the paper's numbers.
2. AutoResearchEval (open-ended subset; tasks and agent trajectories, CC-BY-4.0) from Hugging Face
   (PrentisAI/AutoResearchEval, pinned revision) into <data-dir>/AutoResearchEval/.
   Needed only to re-run models or judges.  Requires `pip install huggingface_hub`.

Usage (from experiments/):
  python scripts/download_data.py                 # both
  python scripts/download_data.py --skip-are      # only the release tarball
  python scripts/download_data.py --only-are      # only AutoResearchEval
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

EXPERIMENTS_DIR = Path(__file__).resolve().parents[1]
RELEASE_URL = "https://github.com/titanresearchlabs/aurora/releases/download/v0.1.0/aurora-experiments-data.tar.gz"
RELEASE_SHA256 = "839bd55c383dc813c9844e04781c247a2b4dbc2eba3a841d57536808c5c34772"
ARE_REPO = "PrentisAI/AutoResearchEval"
ARE_REVISION = "412fb4908a3a7b98dd50cfa3e5c6cd40750d2d4f"
ARE_PATTERNS = ["README.md", "open-ended/*"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    if url.startswith(("http://", "https://")):
        print(f"downloading {url}")
        with urllib.request.urlopen(url) as r, open(dest, "wb") as fh:  # noqa: S310 - fixed https URL
            shutil.copyfileobj(r, fh)
    else:  # a local file, e.g. a tarball built from the staging directory
        shutil.copy(url, dest)


def safe_members(tar: tarfile.TarFile):
    """Members with the top-level directory stripped; refuse links and paths that escape the target."""
    for m in tar.getmembers():
        p = PurePosixPath(m.name)
        if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk() or m.isdev():
            raise SystemExit(f"refusing unsafe archive member {m.name!r}")
        if len(p.parts) <= 1:  # the top-level directory itself
            continue
        m.name = str(PurePosixPath(*p.parts[1:]))
        yield m


def install_release(data_dir: Path, url: str, expected: str | None, force: bool) -> None:
    if (data_dir / "MANIFEST.md").exists() and not force:
        print(f"release data already in {data_dir} (MANIFEST.md present); use --force to reinstall")
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        archive = Path(tmp) / "aurora-experiments-data.tar.gz"
        fetch(url, archive)
        digest = sha256(archive)
        if expected and digest != expected:
            raise SystemExit(f"SHA-256 mismatch for {url}:\n  expected {expected}\n  got      {digest}")
        print(f"sha256 ok: {digest}" if expected else f"sha256 (not checked): {digest}")
        with tarfile.open(archive) as tar:
            members = list(safe_members(tar))
            if hasattr(tarfile, "data_filter"):
                tar.extractall(data_dir, members=members, filter="data")
            else:
                tar.extractall(data_dir, members=members)  # noqa: S202 - members checked above
    print(f"unpacked the release data into {data_dir}")


def install_are(data_dir: Path, revision: str) -> None:
    target = data_dir / "AutoResearchEval"
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("AutoResearchEval is downloaded with huggingface_hub: `pip install huggingface_hub`, "
                         f"or fetch https://huggingface.co/datasets/{ARE_REPO} yourself into {target}")
    print(f"downloading {ARE_REPO}@{revision[:10]} (open-ended subset, ~0.5 GB) into {target}")
    snapshot_download(repo_id=ARE_REPO, repo_type="dataset", revision=revision,
                      allow_patterns=ARE_PATTERNS, local_dir=str(target))
    print(f"AutoResearchEval ready in {target}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path,
                    default=Path(os.environ.get("AURORA_EXPERIMENTS_DATA") or EXPERIMENTS_DIR / "data"))
    ap.add_argument("--release-url", default=os.environ.get("AURORA_EXPERIMENTS_DATA_URL", RELEASE_URL),
                    help="URL or local path of aurora-experiments-data.tar.gz")
    ap.add_argument("--sha256", default=RELEASE_SHA256, help="expected SHA-256 of the tarball ('' to skip the check)")
    ap.add_argument("--are-revision", default=ARE_REVISION, help="AutoResearchEval revision on Hugging Face")
    ap.add_argument("--skip-are", action="store_true", help="do not download AutoResearchEval")
    ap.add_argument("--only-are", action="store_true", help="download only AutoResearchEval")
    ap.add_argument("--force", action="store_true", help="reinstall the release data")
    a = ap.parse_args()
    if not a.only_are:
        install_release(a.data_dir, a.release_url, a.sha256 or None, a.force)
    if not a.skip_are:
        install_are(a.data_dir, a.are_revision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
