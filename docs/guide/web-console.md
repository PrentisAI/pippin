# Web console

```bash
aurora serve              # then open http://localhost:8000
```

![Web console](../assets/web-console.png)

## Projects and experiments

A **project** groups related experiments and has its own chat. Type what you
want to investigate on the start page to create one. The project's front desk
asks what is unclear and then shows a **proposal card**: the instruction the
agents will get, a summary, and the phases to run. Change the phases on the
card, choose the ideation mode (*Round table* or *Independent*) when ideation
runs, set the budget (US$20 by default; empty means no limit), or ask the front
desk to change the instruction, then **confirm** to start the experiment.
*Advanced settings* on the card set whether decisions wait for you
(supervised or autonomous), the model of every role (the agents of each
phase, the ideation seats and chair, both auditors and the front desk), and the
container's CPUs and memory.

An **experiment** is one campaign. Its page shows

- the **phase pipeline** and the current phase;
- the **live stream**: the ideation meeting as it happens (each seat's
  presentation and remarks appear while they are written), agent reasoning and tool calls, job boundaries,
  milestone audit verdicts, live-auditor verdicts, human gates, front-desk
  messages and skill suggestions;
- the **files** of the experiment: its campaign directory (plans, audit
  reports, transcripts, the report) and, while it runs, the container's `/app`
  (`app (live container)`). You can open and edit files; edits to container
  files go straight into the running container;
- the **cost** so far;
- the container's **CPU and memory limits** (*Container*): click to change them,
  at once while the experiment runs, or for its resume or re-run.

## Talking to an experiment

Type in the experiment chat. While the experiment runs, its front desk answers
questions and relays instructions to the research agent; afterwards it answers
questions about the experiment from its files. *Front desk* in the header
switches it off (no model calls; while the experiment runs, messages then go
straight to the agent). The project chat can look into all runs of the project
and propose experiments that build on them (see
[Working with the human](../concepts/human-in-the-loop.md#front-desks-in-the-web-console)).

## Decisions

When the experiment needs a decision, an approval bar appears with the
reason. Choose **Continue**, write an instruction and **Send instruction**, or
**Stop**. A message with **Continue** is passed on as a note. When screening keeps
rejecting the proposals, you can also write which direction to pursue (one of
the proposals, with changes, or your own idea) and choose **Choose the
direction**: the stream shows how your message was understood. Tick *ideation*
and/or *screening* next to the button to run your direction through them first;
otherwise planning starts from it at once. The *Supervised / Autonomous* switch changes whether such gates wait
for you.

## Stop, resume, re-run

- **Stop** ends the experiment at the next safe point; its files stay.
- **Resume** continues a stopped or failed experiment from its checkpoint.
- **Re-run** starts a new experiment with the same settings.

## Renaming and deleting

Each project and run in the sidebar has a **⋮** menu (it appears on hover):

- **Rename** edits the name in place (Enter saves, Escape cancels). Only the
  displayed name changes; the ID and the directory stay, and a resume or re-run
  keeps the new name.
- **Delete** asks for confirmation first. It removes the project (with all its
  runs) or the run from AURORA; tick *Also delete its files on disk* (the dialog
  shows how much space that frees) to remove the report, workspace and data as
  well, otherwise they stay where they are. A running experiment must be stopped
  first.

The run menu also has **Stop** and **Re-run**.

## Files

Upload files and whole folders (with their structure) in the browser:

- **With a message** — **+** in the chat box (*Upload files*, *Upload a
  folder*; in the project chat also *From the project's files*), or drop files
  and folders on the chat. Each appears as a card in the message box (images as
  thumbnails) while it uploads; they are sent with your message, so the front
  desk knows about them. In the project chat they go to the project's files, in
  an experiment's chat to its `inputs/`.
- **To the project** — *Files* in the project's ⋮ menu (or *Manage project
  files* under **+**). Project files are shared by all
  runs of the project. The *Files* window lists them (folders first, with sizes),
  previews text files, makes folders and deletes; it shows how much the
  project's files take of its limit.
- **To an experiment** — on its proposal card, *+ files* picks project files
  and folders as its inputs: when it starts they appear in its `inputs/`
  (`/app/inputs` for the agents). The copies are copy-on-write clones where the
  file system allows it (no extra space; the agents' changes do not reach the
  originals). The front desk sees the project's files, can read the start of
  text files (a CSV's header, say) and puts the ones an experiment needs on its
  card.
- **To a running experiment** — **+** in its chat, the upload buttons in its
  *Files* panel, or drop files there: they go to its `inputs/`, where the agents
  see them at once.

Archives (`.zip`, `.tar.gz`, ...) are kept as they are; tick *unpack archives*
in the *Files* window to unpack them into a folder of their name (entries that
would land outside it are skipped). A file may be up to 20 GB and a project's
files 200 GB (`uploads.max_file_gb`, `uploads.max_project_gb`, also in
*Settings*); uploads also stop when the disk is nearly full.

## Search, Analytics and Settings

The bar on the left also opens:

- **Search** — projects, runs (names and instructions), reports, the project and
  experiment chats, and skills, with the matching passage; a click opens it.
- **Analytics** — what the experiments cost and how they went over the last 7,
  30, 90 or 365 days: total cost, runs, the share completed, mean duration, cost
  per day, cost per project, and the most expensive runs.
- **Settings** — the defaults for new experiments: the model of every role,
  budget and time limit, decision mode and timeout, the front desk, ideation,
  research limits (jobs at the same time, time per job, follow-up rounds,
  pre-flight revisions), the live auditor, the container, and skill learning.
  It also shows the environment: whether the API key is set (never the key),
  Docker and Claude Code, the machine's CPUs, memory and free disk.
  *Notifications* sets where messages go when a decision waits for you or an
  experiment ends ([Notifications](notifications.md)); *Cluster jobs* lets the
  research agents submit SLURM jobs ([Cluster jobs](cluster-jobs.md)), whose
  states appear under **Jobs** on the experiment's right bar.

## Changing settings in conversation

You can also say it in the chat:

- In the project chat, settings for one experiment ("budget 50 dollars, Opus for
  research, 32 CPUs") go into the proposal card, where you can still change
  them. Defaults for all new experiments ("from now on use Opus for research")
  are shown to you first and saved only after you agree.
- In a running experiment's chat, the front desk can switch the decision mode,
  change the budget and the container's CPUs and memory. The budget can also be
  changed by clicking *Cost* in the experiment's header; it applies at once.

## Configuring experiments started from the console

Everything not set on the proposal card comes from *Settings*, and what Settings
leaves alone from the server's base config. Settings are kept in
`AURORA_HOME/console_settings.yaml`; the base config file is not changed:

```bash
aurora serve --config base.yaml
```

```yaml
# base.yaml — no objective needed
models:
  agent: anthropic/claude-sonnet-5.5
sandbox:
  image: ghcr.io/my-lab/analysis-env:latest
budget:
  max_cost_usd: 30
human:
  autonomy: supervised
```

## Remote use

The console has no authentication. Run it on `127.0.0.1` (the default) and
reach it through an SSH tunnel; do not expose it on a public interface, since
anyone who can reach it can start campaigns with your API key.
