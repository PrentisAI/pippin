"""Command-line interface: ``aurora <command>``.

    aurora demo                 run the offline demo (no API key, no Docker)
    aurora init [PATH]          write a commented starter config
    aurora doctor [CONFIG]      check Claude Code, Docker and the API key
    aurora run CONFIG           run a campaign
    aurora run -o "objective"   run a campaign with the default settings
    aurora status CAMPAIGN_DIR  show where a campaign is
    aurora resume CAMPAIGN_DIR  continue a stopped campaign from its checkpoint
    aurora serve                start the web console
    aurora skills ...           list, add or remove skills in the skill library
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from aurora import __version__

STARTER_CONFIG = """\
# Pippin campaign configuration. Only `objective` is required; everything else
# shows its default. Reference: docs/configuration.md

objective: >
  Replace this with your research question, in plain language. Say what you want
  to find out, what data or code exists, and what a good result looks like.

# inputs:                      # files or folders the agents should use
#   - ./data
# skip_phases: [writing]       # any of ideation, screening, planning, research, analysis, writing

models:                        # OpenRouter model IDs; one OPENROUTER_API_KEY covers all
  agent: anthropic/claude-sonnet-5.5
  ideation_seats: [anthropic/claude-sonnet-5.5, openai/gpt-5.6-sol, google/gemini-3.1-pro-preview]
  ideation_chair: moonshotai/kimi-k3
  auditor: anthropic/claude-sonnet-5.5
  supervisor: anthropic/claude-sonnet-5.5
  frontdesk: anthropic/claude-haiku-5.5

ideation:
  mode: roundtable             # or: independent (cheaper, no discussion)

sandbox:
  docker: true
  image: python:3.11-slim      # use an image with the libraries your task needs

human:
  autonomy: supervised         # supervised: escalations wait for you; autonomous: they don't

budget:
  max_hours: 24
  # max_cost_usd: 50

workspace:
  root: ./workspace
"""


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _run_engine(config, *, resume: bool = False) -> int:
    import asyncio

    from aurora.core.engine import ResearchEngine
    from aurora.core.requirements import RuntimeRequirementError

    print(flush=True)
    print(f"Pippin {__version__} — campaign '{config.campaign_id}'" + ("  [mock mode]" if config.mock_mode else ""))
    print(f"  objective : {' '.join(config.objective.split())[:150]}")
    print(f"  workspace : {Path(config.workspace.root).resolve() / config.campaign_id}")
    print()
    engine = ResearchEngine(config)
    engine.human_gate.interactive_console = sys.stdin.isatty()
    try:
        result = asyncio.run(engine.run_campaign(resume=resume))
    except RuntimeRequirementError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Continue later with: aurora resume "
              f"{Path(config.workspace.root).resolve() / config.campaign_id}", file=sys.stderr)
        return 130

    print()
    print("=" * 64)
    print(f"Campaign {result.status.upper()}: {result.summary}")
    if result.proposal:
        print(f"  direction : {result.proposal.title}")
    print(f"  jobs      : {sum(1 for r in result.iterations if r.job_result and r.job_result.success)}"
          f"/{len(result.iterations)} succeeded")
    if result.manuscript_path:
        print(f"  report    : {result.manuscript_path}")
    print(f"  workspace : {result.workspace}")
    print(f"  time      : {result.total_wall_time_seconds / 60:.1f} min   API cost: ${result.cost_usd:.2f}")
    print("=" * 64)
    return 1 if result.status == "failed" else 0


# -- commands -------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    from aurora.config import AuroraConfig, load_config

    _setup_logging(args.verbose)
    overrides = {"mock_mode": True if args.mock else None, "campaign_id": args.name,
                 "objective": args.objective}
    if args.config:
        config = load_config(args.config, **overrides)
    elif args.objective:
        config = AuroraConfig.model_validate({k: v for k, v in overrides.items() if v is not None})
    else:
        print("Give a config file (aurora run config.yaml) or an objective (aurora run -o '...').",
              file=sys.stderr)
        return 2
    return _run_engine(config)


def cmd_demo(args: argparse.Namespace) -> int:
    from aurora.config import AuroraConfig

    _setup_logging(args.verbose)
    config = AuroraConfig(
        objective=("Investigate surface segregation of Zn in Cu(111) and its effect on CO adsorption, "
                   "as a step towards Cu-Zn methanol synthesis catalysts."),
        campaign_id=args.name,
        mock_mode=True,
        workspace={"root": args.workspace},
    )
    print("Offline demo: scripted mock models and simulated tools replay a small materials study.\n")
    return _run_engine(config)


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite.", file=sys.stderr)
        return 1
    path.write_text(STARTER_CONFIG)
    print(f"Wrote {path}. Edit the objective, then: aurora doctor {path} && aurora run {path}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import shutil
    import subprocess

    from aurora.config import AuroraConfig, load_config
    from aurora.core.requirements import RuntimeRequirementError, check_runtime_requirements
    from aurora.env import load_dotenv
    from aurora.paths import aurora_home

    env_file = load_dotenv()

    def line(good: bool, what: str, detail: str = "") -> None:
        print(f"  [{'ok' if good else '!!'}] {what}" + (f" — {detail}" if detail else ""))

    print(f"Pippin {__version__}, Python {sys.version.split()[0]}")
    claude = shutil.which("claude")
    version = ""
    if claude:
        try:
            version = subprocess.run([claude, "--version"], capture_output=True, text=True,
                                     timeout=20).stdout.strip()
        except Exception:
            version = "?"
    line(bool(claude), "Claude Code CLI", version or "not found: npm install -g @anthropic-ai/claude-code")
    docker = shutil.which("docker")
    line(bool(docker), "Docker", docker or "not found (needed unless sandbox.docker is false)")
    line(env_file is not None, ".env file", str(env_file) if env_file else "none found (optional)")
    print(f"  [..] data directory — {aurora_home()}")
    try:
        config = load_config(args.config) if args.config else AuroraConfig(objective="doctor")
        check_runtime_requirements(config)
        line(True, "requirements of " + (args.config or "the default config"))
    except RuntimeRequirementError as exc:
        print("\n" + str(exc))
        return 1
    except Exception as exc:
        line(False, f"config {args.config}", str(exc))
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    campaign = Path(args.campaign_dir)
    checkpoint = campaign / "checkpoint.json"
    if not checkpoint.exists():
        print(f"No campaign found at {campaign} (no checkpoint.json).", file=sys.stderr)
        return 1
    cp = json.loads(checkpoint.read_text())
    print(f"Campaign   : {cp.get('campaign_id')}")
    print(f"Next phase : {cp.get('phase')}   round: {cp.get('research_cycle', 0)}")
    if cp.get("completed_jobs"):
        print(f"Jobs done  : {', '.join(cp['completed_jobs'])}")
    reports = sorted((campaign / "phases" / "writing").glob("**/draft_*.md"))
    if reports:
        print(f"Report     : {reports[-1]}")
    gates = campaign / "human_gates.jsonl"
    if gates.exists():
        print(f"Human gates: {sum(1 for _ in gates.open())} (see {gates})")
    audit_dir = campaign / "audit_records"
    for a in sorted(audit_dir.glob("*.json"))[-3:] if audit_dir.exists() else []:
        try:
            verdict = json.loads(a.read_text()).get("verdict", "?")
        except json.JSONDecodeError:
            verdict = "?"
        print(f"Audit      : {a.stem}: {verdict}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from aurora.config import load_config

    _setup_logging(args.verbose)
    campaign = Path(args.campaign_dir).resolve()
    saved = campaign / "config.yaml"
    if not saved.exists():
        print(f"{saved} not found; is {campaign} a campaign directory?", file=sys.stderr)
        return 1
    config = load_config(saved, campaign_id=campaign.name)
    config.workspace.root = str(campaign.parent)
    return _run_engine(config, resume=True)


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    if args.config:
        import yaml

        from aurora.config import AuroraConfig
        raw = yaml.safe_load(Path(args.config).read_text()) or {}
        # fail now on typos rather than when the first experiment starts
        AuroraConfig.model_validate({**raw, "objective": "(set per experiment)"})
        os.environ["AURORA_SERVER_CONFIG"] = str(Path(args.config).resolve())

    url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}"
    print(f"Pippin web console: {url}")
    os.environ.setdefault("AURORA_CONSOLE_URL", url)    # links in notifications
    if args.host not in ("127.0.0.1", "localhost"):
        print("Warning: the console has no login. Anyone who can reach this address can start "
              "campaigns with your API key. Prefer --host 127.0.0.1 with an SSH tunnel.", file=sys.stderr)
    uvicorn.run("aurora.api.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    from aurora.quickstart import main as quickstart
    return quickstart(assume=args.yes, port=args.port, serve=not args.no_serve)


def cmd_users(args: argparse.Namespace) -> int:
    """Accounts of the web console. The first admin turns sign-in on."""
    import getpass

    from aurora.api.persistence import Store
    from aurora.auth.passwords import check_new_password
    from aurora.auth.store import AuthStore

    auth = AuthStore()

    def ask_password() -> str:
        while True:
            pw = getpass.getpass("Password: ")
            problem = check_new_password(pw)
            if problem:
                print(problem)
                continue
            if getpass.getpass("Again: ") == pw:
                return pw
            print("The two do not match.")

    if args.users_cmd == "add":
        first = not auth.has_users()
        role = "admin" if args.admin else "guest" if args.guest else "member"
        if first and role != "admin":
            print("The first account must be an admin: add it with --admin.", file=sys.stderr)
            return 1
        password = args.password or ask_password()
        problem = check_new_password(password)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        try:
            row = auth.add_user(args.email, args.name or "", password, role)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        auth.log(None, f"added {row['name']} as {role} from the command line", row["user_id"])
        print(f"Added {row['email']} ({role}).")
        if first:
            store = Store()
            n = auth.adopt_unowned([r["campaign_id"] for r in store.list_campaigns()], row["user_id"])
            print("Sign-in is now required in the web console." + (f" {n} existing projects now belong to {row['email']}." if n else ""))
        return 0
    if args.users_cmd == "list":
        for u in auth.list_users():
            print(f"{u['email']:<40} {u['team_role']:<7} {u['name']}")
        return 0
    row = auth.user_by_email(args.email)
    if row is None:
        print(f"No account {args.email}", file=sys.stderr)
        return 1
    if args.users_cmd == "passwd":
        auth.update_user(row["user_id"], password=ask_password())
        print("Password changed.")
    elif args.users_cmd == "role":
        if row["team_role"] == "admin" and args.role != "admin" and len(auth.admins()) == 1:
            print("The team needs at least one admin.", file=sys.stderr)
            return 1
        auth.update_user(row["user_id"], team_role=args.role)
        print(f"{row['email']} is now {args.role}.")
    elif args.users_cmd == "remove":
        if row["team_role"] == "admin" and len(auth.admins()) == 1:
            print("The team needs at least one admin.", file=sys.stderr)
            return 1
        auth.remove_user(row["user_id"])
        print(f"Removed {row['email']}; what they made stays theirs.")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    from aurora.paths import aurora_home
    from aurora.skills.store import DistilledSkill, SkillStore, missing_sections, skill_template

    store = SkillStore(aurora_home() / "skills.db")
    if args.skills_cmd == "list":
        skills = store.list_all()
        if not skills:
            print("The skill library is empty. Write one: aurora skills template > my-skill.md, edit it, then "
                  "aurora skills add my-skill @my-skill.md --description '...'")
        origins = {"human": "from your corrections", "self": "learned from runs", "manual": "written by hand"}
        for sk in skills:
            print(f"- {sk.name} v{sk.version} [{sk.scope}] {sk.kind or 'rule'}, {origins.get(sk.origin, sk.origin)}"
                  f"\n    {sk.description or ''}")
    elif args.skills_cmd == "show":
        sk = store.get_by_name(args.name)
        if sk is None:
            print(f"No skill named '{args.name}'.", file=sys.stderr)
            return 1
        print(f"# {sk.name} (v{sk.version}, {sk.kind or 'rule'}, {sk.origin})\n{sk.description}\n\n{sk.rule}")
    elif args.skills_cmd == "template":
        print(skill_template())
    elif args.skills_cmd == "add":
        text = Path(args.rule[1:]).read_text() if args.rule.startswith("@") else args.rule
        existing = store.get_by_name(args.name)
        if existing:
            store.revise(existing.skill_id, text, reason="replaced with aurora skills add",
                         description=args.description or None)
            print(f"Revised skill '{args.name}' (now v{existing.version + 1}).")
        else:
            store.create(DistilledSkill(name=args.name, rule=text, description=args.description,
                                        scope="global", origin="manual"))
            print(f"Added skill '{args.name}'. Every new campaign can use it.")
        missing = missing_sections(text)
        if missing:
            print(f"Note: it lacks the standard sections {', '.join(missing)} (see aurora skills template).")
    elif args.skills_cmd == "remove":
        sk = store.get_by_name(args.name)
        if sk is None:
            print(f"No skill named '{args.name}'.", file=sys.stderr)
            return 1
        store.delete(sk.skill_id)
        print(f"Removed skill '{args.name}'.")
    return 0


# -- parser -----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora",
        description="Pippin — long-horizon, open-ended research with AI agents and a human in the loop.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("quickstart", help="the first minutes: check this machine, set the key, see the demo, open the console")
    p.add_argument("-y", "--yes", action="store_true", help="answer yes to every question")
    p.add_argument("--port", type=int, default=8000, help="port of the web console (default 8000)")
    p.add_argument("--no-serve", action="store_true", help="do not start the web console at the end")
    p.set_defaults(func=cmd_quickstart)

    p = sub.add_parser("run", help="run a campaign")
    p.add_argument("config", nargs="?", help="YAML configuration file")
    p.add_argument("-o", "--objective", help="research objective (overrides the config's)")
    p.add_argument("--name", help="campaign name (directory under the workspace root)")
    p.add_argument("--mock", action="store_true", help="offline mock mode")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("demo", help="run the offline demo (no API key, no Docker)")
    p.add_argument("--workspace", default="./workspace", help="workspace root (default ./workspace)")
    p.add_argument("--name", default="", help="campaign name")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("init", help="write a commented starter config")
    p.add_argument("path", nargs="?", default="aurora.yaml")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check that everything a campaign needs is installed")
    p.add_argument("config", nargs="?", help="check the requirements of this config")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("status", help="show where a campaign is")
    p.add_argument("campaign_dir", help="the campaign directory, e.g. workspace/<campaign_id>")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("resume", help="continue a campaign from its last checkpoint")
    p.add_argument("campaign_dir", help="the campaign directory, e.g. workspace/<campaign_id>")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("serve", help="start the web console")
    p.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--config", help="base configuration for experiments started from the console "
                                    "(models, sandbox, budget, ...; its objective is ignored)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("skills", help="manage the skill library shared by all campaigns")
    sk = p.add_subparsers(dest="skills_cmd", metavar="ACTION", required=True)
    sk.add_parser("list", help="list skills")
    sk.add_parser("show", help="print a skill").add_argument("name")
    sk.add_parser("template", help="print an empty skill in the standard sections")
    a = sk.add_parser("add", help="add a skill written by you")
    a.add_argument("name", help="kebab-case name")
    a.add_argument("rule", help="the skill text, or @file.md to read it from a file")
    a.add_argument("--description", default="", help="one-line description")
    r = sk.add_parser("remove", help="remove a skill")
    r.add_argument("name")
    p.set_defaults(func=cmd_skills)

    p = sub.add_parser("users", help="accounts of the web console (the first admin turns sign-in on)")
    us = p.add_subparsers(dest="users_cmd", metavar="ACTION", required=True)
    a = us.add_parser("add", help="add an account")
    a.add_argument("email")
    a.add_argument("--name", default="")
    a.add_argument("--admin", action="store_true", help="a team admin (the first account must be one)")
    a.add_argument("--guest", action="store_true", help="a guest: sees only the projects they are invited to")
    a.add_argument("--password", default="", help="(asked for when left out)")
    us.add_parser("list", help="list accounts")
    us.add_parser("passwd", help="set a new password").add_argument("email")
    r = us.add_parser("role", help="make someone admin, member or guest")
    r.add_argument("email")
    r.add_argument("role", choices=["admin", "member", "guest"])
    us.add_parser("remove", help="remove an account (what they made stays)").add_argument("email")
    p.set_defaults(func=cmd_users)

    from aurora.compute.cli import add_parser as add_job_parser
    add_job_parser(sub)
    papers = sub.add_parser("papers", add_help=False,
                            help="search the literature and check references (used by a running experiment's agents)")
    papers.add_argument("papers_args", nargs="*")
    results = sub.add_parser("results", add_help=False,
                             help="record results and their provenance (used by a running experiment's agents)")
    results.add_argument("results_args", nargs="*")
    return parser


def main(argv: list[str] | None = None) -> None:
    from aurora.env import load_dotenv

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["job"]:      # the agents' command: its own parser, options passed through as they are
        from aurora.compute.job_client import main as job_main
        sys.exit(job_main(argv[1:]))
    if argv[:1] == ["papers"]:   # the agents' literature command (aurora.literature.agent_service)
        from aurora.literature.papers_client import main as papers_main
        sys.exit(papers_main(argv[1:]))
    if argv[:1] == ["results"]:  # the agents' result ledger (aurora.results.agent_service)
        from aurora.results.results_client import main as results_main
        sys.exit(results_main(argv[1:]))
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))
