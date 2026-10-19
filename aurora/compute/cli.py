"""``aurora job`` as a subcommand of the ``aurora`` CLI (see :mod:`aurora.compute.job_client`)."""

from __future__ import annotations

import argparse
from typing import Any

from aurora.compute import job_client


def cmd_job(args: argparse.Namespace) -> int:
    return job_client.main(args.job_args)


def add_parser(sub: Any) -> None:
    p = sub.add_parser("job", add_help=False,
                       help="cluster jobs of a running experiment (used by its research agents)")
    p.add_argument("job_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_job)
