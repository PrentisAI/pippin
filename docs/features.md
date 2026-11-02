# Everything Pippin does

This page lists every feature of Pippin in one place, grouped by what you are doing. Each section says what the
feature does, how you reach it, and the settings that matter; the linked pages go into detail.

| Area | In short |
|---|---|
| [Getting started](#getting-started) | quickstart, offline demo, welcome, examples, tour, help |
| [Projects and experiments](#projects-and-experiments) | a project chat that drafts a proposal; one experiment = one campaign |
| [The research cycle](#the-research-cycle) | ideation → screening → planning → pre-flight → research → analysis → writing, with follow-up rounds |
| [Oversight](#oversight-auditors-and-you) | milestone auditor, live auditor, human gates, front desks you talk to while it works |
| [Results and reports](#results-and-reports) | every number traced to its script; downloads, reproduce, Overleaf, compare, fork, estimates |
| [Compute](#compute) | one container per run, SLURM or an SSH GPU machine, agents that pause while jobs run |
| [Skills](#skills) | know-how carried between runs, learned from runs and from your corrections |
| [Literature and the Library](#literature-and-the-library) | real papers only, every reference checked |
| [Brainstorm](#brainstorm) | Skeptic, Methods, Librarian and Mentor; ideas as arguments; drafts and your own versions |
| [Zoom out, zoom in](#zoom-out-zoom-in) | a map of a field as a chain; a deep read of a paper in twelve questions |
| [Paper reader](#paper-reader) | read a PDF with the agents; notes in step with Zotero |
| [The feed](#the-feed) | new papers and posts from whom you follow, ranked against your projects |
| [Zotero](#zotero) | two-way sync, annotations both ways, and a Zotero 7 plugin |
| [The shared notebook](#the-shared-notebook) | everything you save, kept by claim; what bears on what you do surfaces |
| [Where you read and write](#where-you-read-and-write) | browser extension, Overleaf, macOS orb, Obsidian, voice |
| [What Pippin may write](#what-aurora-may-write) | every write into your tools off by default, logged, undoable |
| [Accounts and sharing](#accounts-and-sharing) | sign-in, roles, sharing, own keys, confidential projects, team rules |
| [Notifications](#notifications) | WeCom, Feishu, Slack, WeChat, webhooks; answer decisions from the phone |
| [Around the console](#around-the-console) | files, search, analytics, settings, settings by conversation |
| [Command line](#command-line) | `aurora run`, `resume`, `serve`, `users`, `skills`, and the agents' `job` and `papers` |
| [Reference and research](#reference-and-research) | configuration, campaign files, events, reproducing the paper |

---

## Getting started

| Feature | What it does |
|---|---|
| **`aurora quickstart`** | Checks Python, Claude Code and Docker (offers to pull the container image), asks for your OpenRouter key (checked with OpenRouter, saved to `~/.aurora/.env` readable only by you), runs the offline demo and opens the console. `-y` answers yes to everything; `--no-serve` stops before the console. |
| **`aurora demo`** | The whole pipeline in seconds with scripted models and simulated tools: no key, no Docker, no Claude Code, no cost. Same files and events as a real run. |
| **`aurora doctor`** | Checks Claude Code, Docker, the API key and a config. Every campaign runs the same checks at start and stops with instructions if something is missing. |
| **`aurora init`** | Writes a commented starter `aurora.yaml`. |
| **Welcome** | The first time someone opens the console (with accounts, every new member): *Welcome*, *Connect* (what this machine has and lacks, each with how to fix it; the model key), *Your way of working* (field, supervised or autonomous, budget), *Start* (example, demo or own research). Every step can be skipped; **Help → Open the welcome again**. |
| **Examples** | Finished runs to look at, read-only: *Citi Bike usage patterns* (a real run, with a screening decision and the live auditor stopping the agent) and the scripted demo. **Duplicate** makes a project of your own with the same question. Each person can hide them; an admin can withdraw one. |
| **The tour** | Three chapters of spotlights: *A finished run*, *Around Pippin* (every button of the left bar), *Your own research*. Arrow keys move, Esc ends. |
| **Help (?)** | Ask how to do something (answered from the documentation, with links); a *Getting started* checklist that ticks itself; the tours, the examples and every guide, read in the console. |
| **First-time tips** | The first proposal card, decision, running run, report, Compare, Fork, GPU jobs, Brainstorm and Library each get a short tip; *Got it* hides it for good. |
| **Setup card** | On the start page: what a run needs (key, Claude Code, Docker) and what helps (image pulled, GPU jobs, notifications, OpenAlex key), each with its fix. Without accounts the key can be pasted there and the image pulled with one click. |
| **Watch a demo run** | Adds a scripted run to your demo project: the ideation meeting, every phase, the auditors and a report, in seconds, for free. |
| **Templates** | *Analyse a dataset*, *Compare methods*, *Reproduce a paper*, *Explore an open question*, *Run a simulation study*: a brief with [brackets] to fill in and the phases it will likely use. |

See [Installation](getting-started/installation.md), [Your first campaign](getting-started/first-campaign.md) and
[Web console](guide/web-console.md#the-first-time).

## Projects and experiments

- **A project** groups related experiments and has its own chat. Type what you want to investigate on the start
  page to create one.
- **The project front desk** asks what is unclear, then shows a **proposal card**: the instruction the agents
  will get, a summary, and the phases to run. On the card you can:
  - tick or untick phases, choose *Round table* or *Independent* ideation, set the budget (US$20 by default;
    empty means no limit);
  - under *Advanced settings*: supervised or autonomous decisions, the model of every role (each phase, ideation
    seats and chair, both auditors, the front desk), the container's CPUs and memory, jobs at the same time;
  - pick project files as the run's inputs (*+ files*), see the **estimate** of cost and time, see which earlier
    runs it is *based on* and which seed papers it starts from, and set which papers the agents find go into the
    literature;
  - ask the front desk to change the instruction, then **confirm** to start.
- **Building on earlier runs**: when a new experiment continues earlier work, the front desk lists those runs as
  *based on*; their reports, findings and mental models are copied to the new run's `prior_runs/` and its agents
  are told to build on them.
- **An experiment page** shows the phase pipeline, the live stream (the ideation meeting as it is written, agent
  reasoning and tool calls, job boundaries, audit verdicts, live-auditor verdicts, human gates, front-desk
  messages, skill suggestions, literature searches and flagged references, GPU jobs), the files (the campaign
  directory and, while it runs, the live container's `/app`, editable in place), the cost, the container limits,
  the report, and **Fork…**; a project with several runs has a **Compare** tab.
- **Stop** (at the next safe point), **Resume** (from the checkpoint), **Re-run** (same settings), **Rename** and
  **Delete** (optionally with the files on disk, showing how much space that frees) from each item's ⋮ menu.
- **Templates and settings by conversation**: "budget 50 dollars, Opus for research, 32 CPUs" goes into the card;
  "from now on use Opus for research" is shown first and saved as a default only after you agree.

## The research cycle

```text
ideation → screening → planning → pre-flight → research → analysis → writing
                                                   ↑___________|   analysis may ask for more
```

Any phase can be skipped (`skip_phases`, or the ticks on the card). Skipping ideation makes the objective the
direction; skipping planning gives a single job. Every phase agent starts a fresh session and is told where the
earlier outputs and transcripts are. See [How a campaign runs](concepts/pipeline.md).

### Ideation

- **Heterogeneous seats and a chair from another family** (defaults: Claude Sonnet, GPT, Gemini as seats; Kimi as
  chair). Seats can be named and given their own prompt; the chair's prompt can be replaced.
- **`independent`** (cheaper): each seat prepares one idea; the chair sees them anonymised and shuffled, picks one
  with a reason, and says why each other idea was set aside; the author writes the proposal.
- **`roundtable`** (a group meeting): presentations, an agenda item per idea with remarks (questions, concerns,
  suggestions, evidence, build-ons) and responses, follow-ups, open discussion, and the chair's decision from its
  notes. The transcript, notes and decision are saved; the meeting streams live in the console.
- **Literature during the meeting**: before presenting and before answering remarks, a seat chooses search
  queries that Pippin runs on the scholarly indexes; every reference it cites is checked and a not-found paper is
  marked unsupported for everyone. Optional web search through OpenRouter.
- **Starting from your idea** (`ideation.develop_objective`): the seats develop the objective into its strongest
  version and say what is wrong with it, instead of proposing other directions.
- **Live round table in a brainstorm** (`ideation.live`, *Brainstorm with me*): you can speak at the meeting and
  rewrite the idea while it runs; the chair's decision comes back to you as drafts (below, under Brainstorm).
- **Seed papers**: a run can start from a paper you give the front desk; ideation develops ideas from it and
  every phase reads it.
- **Resuming** an interrupted meeting continues after the last finished step.
- A failing seat is left out; if all fail, the run stops naming the models.

See [Multi-agent ideation](concepts/ideation.md).

### Screening

Scores novelty, feasibility and significance; searches for the closest prior work (and the project's own papers)
and names up to three closest papers with how the proposal differs. Below 6/10 the proposal goes back to ideation
(twice, then you decide). The stream shows a card with the scores and the closest work, and, while the run waits,
**Start the experiment** and **Change it**.

### Planning and pre-flight

- **Planning** turns the direction into a dependency graph of jobs, each with what it must produce.
- **Pre-flight** checks every job before anything expensive runs: prerequisites (inputs, software, parameters
  that agree with each other and the proposal) and a **smoke run** on the smallest representative input, with
  time and memory measured to estimate the full run against the container's limits. Problems send the plan back to
  planning (up to `agent.preflight_max_revisions`, default 2); problems that remain open a decision for you. The
  findings travel with each job to its research agent.

### Research

- One Claude Code session per job, in the run's container; jobs run in dependency order.
- **Parallel jobs** (`agent.max_parallel_jobs`): jobs whose inputs are ready run at once, each with its own agent,
  told which jobs run beside it.
- **Acceptance tests** (`sandbox.tests_dir`): after each job, hidden tests run in the container; a failing job is
  resumed with the test output, up to three times.
- **GPU jobs** on a cluster or a GPU machine, with the agent paused at no cost while they run (see
  [Compute](#compute)).
- If every job fails, the run stops before analysis instead of writing up results that do not exist.

### Analysis, the mental model, follow-up rounds

- Analysis checks results against the objective and either accepts them or names a phase to return to (research,
  planning or ideation) for a new **round**, up to `agent.max_followup_cycles`.
- Each analysis records the **mental model**: findings with evidence and status (supported, tentative, refuted),
  open questions, and hypotheses with status, confidence, mechanisms, ambiguities and rationale. Refuted
  hypotheses stay in the record. Later phases (new ideation rounds, planning, research, the live auditor, writing)
  receive it.

### Writing

A Markdown report with figures, a result ID after every number, and a reference list and `references.bib` built
only from verified papers. Earlier drafts are kept.

### Stopping, resuming, budgets, mock mode

- Stop at any time (Ctrl-C, *Stop*, a budget, a human *stop*, a crash) and resume: finished phases and jobs are
  not repeated; an interrupted agent session continues in the **same Claude Code session** with what it had read
  and written; files stay in the mounted workspace.
- `budget.max_cost_usd` and `budget.max_hours` are checked before every phase and job; a warning at 80 %.
- `mock_mode` replaces every model and tool with a simulation: same pipeline, files and events.

## Oversight: auditors and you

### Milestone auditor

Reviews the actual output files of every phase before the next one builds on it (and may inspect the container
read-only), for goal alignment, completeness, correctness and traceability: **pass**, **reject** (fixes go back
into the phase's own session, which revises and is audited again, up to `audit.milestone_max_retries`), or
**escalate** to you. Reports in `audit_records/`. See [Auditors](concepts/auditors.md).

### Live auditor

Reads the research agent's transcript every few seconds and answers **continue**, **advise** (a suggestion the
agent receives while working), **interrupt** (stops the current action and sends a correction), **escalate**
(pauses the agent, keeping its session and stopping its clock, and opens a decision) or **withdraw** (no
decision needed after all). It cannot edit files or change the plan. Verdicts show live in the stream.

### Human gates

A run stops for you only when a decision is yours: an auditor escalates, a phase keeps failing its audit,
screening keeps rejecting, a live round table has decided, pre-flight problems remain, or (optionally) before the
run finishes. Answers:

| Answer | Effect |
|---|---|
| **Continue** | go on; a message is passed on as a note |
| **Instruct** | go on following your message |
| **Propose** (screening gate, live round table) | say in your own words which direction to pursue: one of the proposals, one with changes, or your own; the stream shows how it was understood; optionally run it through ideation and/or screening first |
| **Stop** | end the run (resumable) |

Answer in the approval bar, in the chat, at the terminal prompt, or from a notification. **Supervised** gates
wait (with a timeout and a default); **autonomous** records and continues. Switch the mode of a running
experiment at any time. See [Working with the human](concepts/human-in-the-loop.md).

### Front desks: talking to a run while it works

- **The run's front desk** answers from the live status and the workspace files, relays your instructions to the
  research agent *while it works* (streamed into its running session; the agent acknowledges and replies), and can
  interrupt the current action. It explains open decisions and records your answer. After the run ends it still
  answers questions from the files. *Front desk* in the header switches it off (messages then go straight to the
  agent; no model calls).
- **The project front desk** turns an idea into a proposal, can look into every run of the project (status,
  findings, mental model, report, files), compare runs, and propose runs that build on earlier ones.
- In a running experiment's chat, the front desk can also switch the decision mode, change the budget and the
  container's CPUs and memory.
- Conversations survive restarts and resumes.

## Results and reports

| Feature | What it does |
|---|---|
| **Recorded results** | Agents record every number as they compute it (`aurora results record`): value, unit, uncertainty, the script's path, hash and a **copy as it was then**, the command, inputs, outputs, the job and the time. IDs `R1`, `R2`…; recording again supersedes (the old one stays). |
| **Checked citations of results** | When the report is written, its `{R3}` IDs are checked: unknown IDs and superseded results are reported. |
| **The report view** | Every number a chip showing its provenance (open the script now or as it was, copy the command); every reference a chip opening the paper; *What was done*, *Key results*, *Conclusions*, *Figures*. |
| **Downloads** | *Report (zip)*: `report.md`, `report.tex` (made by Pippin, with `\citep` and a results appendix), figures, `references.bib`, results as CSV and JSON, provenance, `environment.txt`, `reproduce.sh`. *Everything (zip)*: the whole run directory (up to 2 GB). |
| **`reproduce.sh`** | Replays the commands that produced the results and the GPU jobs, in the run's image, after installing the saved `pip freeze`. |
| **Put into Overleaf** | Sends the report, figures, bib and results into a folder of an Overleaf project through its git integration; one commit in your name; the token is stored encrypted. |
| **Compare** | Runs side by side: setup, outcome (cost, time, direction, screening scores, GPU hours), results matched by name, conclusions; differences marked; *Only what differs*. |
| **Fork** | From after any phase or any research job (a saved checkpoint and workspace snapshot), with new guidance, another direction (an idea the meeting did not choose, or your own), another research model, budget or name. The original is untouched. |
| **Estimate** | Before a run: cost and time ranges with how they were made and a table by phase, from your earlier runs (three or more alike) or typical phase costs scaled by the chosen models' prices. During the run the header shows it, amber when exceeded; the stream and a notification say so at the upper end and at twice it. |

See [Results, reports and runs](guide/results.md).

## Compute

| Feature | What it does |
|---|---|
| **One container per run** | Every command runs in the run's own Docker container (`sandbox.image`); `/app` is the run's `workspace/` on the host. On the host the agents may only read and write the campaign directory. Agent sessions get a minimal environment (no other secrets). |
| **Limits you can change live** | CPUs and memory per run (card, *Container* in the header, or the front desk); the running container is updated in place and the agent told. |
| **Network** | `sandbox.network` on or off; with it off, use a prepared image. |
| **Agents inside the container** (experimental) | The research agent runs in the container itself; the API key stays in a proxy container, the run's container only gets a token the proxy accepts. |
| **Without Docker** | Agents read and write files only; `allow_host_bash` for disposable machines. |
| **SLURM** | `aurora job submit/status/await/wait/logs/cancel`: jobs run on a node in the run's own image (pyxis/enroot) with the workspace mounted; limits on GPUs per job, jobs at once, time and GPU hours; partitions the agent may choose. |
| **A GPU machine over SSH** | The workspace is copied there with `rsync`, free GPUs are found with `nvidia-smi` (others' work is left alone), the job waits while none are free, runs in Docker with only its GPUs (or directly), and its files come back at the end. The image is pulled or sent once. |
| **Paused while it waits** | `aurora job await` ends the agent's turn: no model calls, its time limit stands still; it is resumed with how each job ended, and for check-ins at growing intervals; your message wakes it at once. |
| **Jobs panel and GPU usage** | Every job with state, node or GPUs, resources, time, exit code, command, output and *cancel*; GPU use and memory per GPU, refreshed every 30 seconds, with a warning when GPUs sit idle. |

See [Sandbox and security](guide/sandbox.md) and [GPU jobs](guide/cluster-jobs.md).

## Skills

- Claude Code skills (a folder with `SKILL.md`), written into every agent session and listed in its prompt.
- Structured sections: purpose, inputs, tools, procedure, expected outputs, validation, failure modes,
  interpretation, when to ask a human; a kind, an origin and a version history.
- **From your corrections**: a reusable correction or convention you give an agent becomes a draft skill (a task
  decision does not).
- **From the agents' runs**: after every job and every passed phase, up to three procedures or diagnosed failures
  are proposed, or revisions of existing skills.
- **Written by you**: in the *Skills* panel (from a template), with `aurora skills add`, or as files in
  `skills_dirs`.
- **You decide**: each proposal is a card (a revision side by side with the current text); *Edit first*, choose
  where it applies (this run, this project, selected projects, all projects), *Add* or *Dismiss*. A team rule can
  require an admin's approval before a skill leaves its project; skills from confidential projects never leave.

See [Skills](concepts/skills.md).

## Literature and the Library

- **Four indexes merged**: OpenAlex, Crossref, arXiv, Semantic Scholar; a short key per paper (`Behrens12`).
- **Every reference checked** (by DOI or arXiv ID, else title, first author and year, comparing every index's own
  record): *verified*, *not found*, *wrong identifier*, *not checked yet*. A paper no index has is never presented
  as real.
- **Citation chips everywhere** agents write: `Behrens 2012 ✓` opens the paper (abstract, where it was used, PDF);
  `Smith 2024 ?` says why, the closest real paper and **Use the real one instead**.
- **The agents' own commands** (also in the container): `aurora papers search / show / pdf / check / list / cite
  / bib`. `check` accepts a citation in any style; `pdf` uses your Zotero copy or the open-access PDF.
- **A project's Literature page**: papers (checked against what, used where, added by whom); *Not found* (use the
  real one, remove everywhere, I have this paper, check again); every search with its results (add one, a
  selection or all); search and add (or paste a DOI, arXiv ID or link); upload PDFs (recognised and checked);
  import BibTeX or RIS; newer arXiv versions (**Cite vN**); export `.bib`.
- **Which found papers go in by themselves**: `cited` (default), `all` or `none`, per console, per project and
  per run.
- **The Library**: your own papers and the literature of every project you can see; **Search** the indexes; the
  **feed** (below).

See [Literature](concepts/literature.md).

## Brainstorm

| Feature | What it does |
|---|---|
| **Four agents** | **Skeptic** (the weakest assumption), **Methods** (how to test it), **Librarian** (the literature and the project's runs), **Mentor** (helps you find your own question by asking). Name one (`@Skeptic`), or use *What could be wrong?*, *Find evidence*, *How to test it?* on an idea. You see each agent's steps while it works. |
| **How active** | *Only when asked*, *Moderately* (a cheap check decides whether one agent answers), *Very* (up to two answer). On their own they bring up recent papers bearing on an idea, ideas that could be one, questions left waiting; at *Very* they also look for evidence and question unchallenged ideas (at most 40 calls a day). Dismissing a suggestion teaches them. |
| **Deep search** | The Librarian searches from many angles and writes an overview, including what the literature does not answer yet. |
| **Attachments** | **+** attaches a Library paper (abstract and your highlights) or a project file. |
| **The Mentor** (after ARS's Socratic mentor) | Asks one or two questions at a time; asks you to predict before it shows; brings a counterpoint from a real paper; offers the opposite when you sound certain; raises one adjacent angle; brings the Skeptic in as devil's advocate; writes your question down as an **RQ Summary** in your words with FINER notes when you can say it; proposes candidate questions only when you ask (marked as the AI's); ends when you say so, then offers to map the field around your question. |
| **Your plan** | The **Plan** tab: research question, method, evidence, limitations, contribution, each point with the words of yours it came from; editable. |
| **Drafts in rounds** | Ask any agent to write or rewrite your idea: two or three drafts you can edit in place, **Take this**, or **Another round from this** (your version first). |
| **A stimulus, then your versions** | When you are stuck the Mentor gives one piece of knowledge or an analogy from a real paper and asks you to write two or three versions yourself; pick one to go on. |
| **Versions of an idea** | Whatever you take or edit becomes the idea's next version, marked as yours, an AI draft you edited (with how much is your own words), or the AI's draft; the history is on the idea and in the Plan. |
| **One step leads to the next** | Each step ends with one sentence offering the natural next one (a stimulus, versions, going deeper, the Skeptic, the map, the round table). |
| **Ideas as arguments** | Each idea: status (*Waiting on 2 questions*, *Ready to test*, *Early*), a sentence on where it stands (kept up to date), a tally, and *why we think so* / *what could be wrong* / *next steps*; links between ideas; a main button for the next thing to do. |
| **Spaces and privacy** | A space per project and one of your own. A new brainstorm is private (owners and admins cannot open it); share the whole conversation or chosen ideas. In a shared brainstorm agents use only shared material and ask you before using a private idea. |
| **Papers tab** | The project's literature with everything of its Literature page, and the agents' searches. |
| **From an idea to an experiment** | **Turn into a proposal**: the idea, evidence for and against, questions, next steps go into the project chat; start from the idea directly, with the round table (agents only, developing your idea), or **Brainstorm with me**. An idea in your own space can move to a project. |
| **Brainstorm with me** | The run's round table meets live in a shared brainstorm: turns appear as written, presentations become ideas and remarks become questions; you can speak and rewrite your idea between turns; the chair's decision comes back as drafts (the chosen direction, the others, your idea) to edit, take, or send for another round. |
| **Evaluation metrics** | Hidden from the user, kept for research: diversity of a space's ideas, how much of them is the researcher's, and how deeply the researcher elaborated (five levels). |

See [Brainstorm](concepts/brainstorm.md).

## Zoom out, zoom in

**Zoom out: a map of a research direction**, started from the conversation (the Librarian proposes it):

1. **Finding the papers** (after ARS): scope, sub-questions and criteria fixed first (you confirm; skipped when the
   map starts from a question you settled with the Mentor); web and index searches, every paper checked; two
   models of different families screen each paper, a third adjudicates, uncertain ones are read in full; targeted
   searches for thin sub-questions or skews; PRISMA-style counts for your word.
2. **The chain**: why (the capabilities needed) → definition (with boundary decisions and components) → design
   space (three frameworks from three models, scored by papers fit, held-out fit, independence of dimensions and a
   judged explanatory score) → placement of every paper with its own words → progress per capability → trends →
   unspoken assumptions → problems attempted but not solved (each diagnosed, with why now, approaches, a first
   experiment, the likely failure and the closest work) → what to read next.
3. **Loops back up**: a misfit paper proposes new dimensions; dispute a placement; challenge the definition and
   get two or three ways to redraw it with what each would change; new papers placed automatically. Every change is
   a version with its reason.
4. Every paragraph is labelled *Paper says*, *Literature*, *Inference* or *Guess*; the map stays in the **Map**
   tab and the agents use it in every answer; your ideas are placed on it.

**Zoom in: a deep read** of one paper in twelve questions (problem; what came before; **how the authors may have
come to the idea**, reconstructed without seeing the method and checked step by step against the introduction;
intuition; the method on one real example; the mathematics; experiments; take-aways; weakest assumption;
one-week reproduction; counterexamples; a follow-up idea with the closest existing work). **Every AI answer
stays covered until you answer the question yourself or choose to see it**; for question 3 you then tick which
steps you had made. Quotes light up the passage in the PDF; ask more about any question; send the follow-up idea
to the brainstorm.

See [Zoom out and zoom in](concepts/zoom-out-zoom-in.md).

## Paper reader

- The PDF with its notes beside it. Select words to highlight, note, or ask the Librarian, the Skeptic or Methods;
  the answer comes in the note's conversation.
- **The agents read along** for your idea, each marking up to two passages as dashed suggestions you **Keep** or
  **Dismiss**.
- Author colours (you yellow, Librarian blue, Skeptic orange, Methods purple); filter by author.
- Notes private by default; **Share with the project** to discuss them.
- **Zotero annotations both ways**, including agents' kept notes with their colour and replies in the comment.
- The **Deep read** tab, and the **Your saves** tab: the sentences of this paper that something you saved argues
  with, with a margin button (*N of your saves argue with this page*).
- The PDF comes from the project, your Library, another project, or the open copy fetched once.

## The feed

- New papers, posts, blogs and discussions from the people and sources you follow, ranked against every project
  you can see, each with **Why for <project>** and how well it fits.
- **People**: by name (pick the right one among namesakes), profile link, ORCID or handle; Pippin finds their
  other places (Bluesky, Mastodon, X, blog) and you choose what to follow from each.
- **Topics**: arXiv categories and keywords, and topics worked out from your projects' objectives.
- **Other sources**: HF Daily Papers, discussions around your projects' papers (Bluesky, Hacker News,
  OpenReview, with **Show the points**), any blog's RSS or Atom; X with your own API key.
- Buttons: **Save to project**, **Bring to brainstorm**, **Ask the front desk**, open it, **Not interested** (and
  similar items rank lower).
- **The daily digest** at your chosen time to a notification channel, with what came into your notebook.

See [The feed](guide/feed.md).

## Zotero

- **Two-way sync** through Zotero's Web API (or read-only through Zotero 7 on the same computer): collections
  mapped to projects; items checked and brought in with PDFs, highlights and notes (which the agents see); the
  project's papers sent back into the collection, tagged `aurora` (when allowed); optional idea notes; Better
  BibTeX keys; every 5 minutes.
- **Notes on PDFs both ways** with the paper reader.
- **The Zotero 7 plugin**: every paper you add is read in the background into a card (methods, findings,
  limitations, each marked stated or inferred) with what your notebook makes of it; the sentences your saves argue
  with (opening the PDF at the page, and orange margin marks when allowed, removable); in the reader, **What my
  saves say** and **Save to Pippin** for a selection; **Save the card as a note** when allowed.

See [Zotero](guide/zotero.md).

## The shared notebook

Everything you save is kept by **claim**: the same claim from several sources (in any language) is one **idea
unit** with several sources, each with its own words found again in a snapshot; opposite claims are linked as
**conflicts**.

| Feature | What it does |
|---|---|
| **Saving** | Links, pages (as you see them, behind a login too), selections, files (PDF or text), notes, papers (recognised and read from the PDF). One to five claims are taken out; the same claim is merged across languages and settings; the result comes back in a sentence (*Merged into "…", now 3 sources; bears on Q1*). |
| **Corrections** | **not the same** splits a source off (and later alignments are told, so the mistake is not repeated); **same claim, merge** joins two units; **edit** a claim in your own words. |
| **Research questions** | Q1, Q2… from the Mentor and your own; each unit scored against them. |
| **Context** | What an entry point says you have open (page, paper and page, selection), kept a few hours in memory only; or set by hand. |
| **Three layers** | **Surface** (at most 3, new and directly relevant; conflicts first), **Absorbed** (at most 5, its point already gathered; no need to read), **Sunk** (unrelated or let sink; never deleted). Each unit says why it is at that depth and how it stands (challenges, adds, supports, background, tool). |
| **Your marks** | Let it sink or raise it, *This changed my view*, with times. |
| **Ask me** | A question from what surfaced (usually a conflict) instead of a summary; your answer comes back with those units later and joins the Mentor's research plan for that question. |
| **What it adds up to** | For a research question: what its claims amount to, starting from the map of its field when there is one. |
| **Today** | What came in over the last day, by question: +1 source, new claims, new conflicts; one or two papers **worth reading closely** with **Deep read** and **To read**; let a claim sink, or see **how it differs** from another. |
| **In a brainstorm** | **Bring to a brainstorm** as evidence; in your private brainstorms the Librarian and the Skeptic search your notebook themselves. |
| **This week** | A weekly report: what merged, new units and conflicts, what changed your view, your answers, what to archive. |
| **Interruptions** | Only an experiment **waiting for your decision**, a **retraction** of a paper you rely on (memory, Library, a manuscript you checked; OpenAlex and Crossref daily), or a new paper that **overlaps** a question you settled with its plan or a manuscript you checked (at most two overlaps a day, quiet hours respected). Banner, orb dot, notifications; **Not worth interrupting me for** moves that kind to the digest. |
| **Your own notes** | An Obsidian vault (or any Markdown folder) read by the console or sent by the plugin; claims marked as yours; a paper disagreeing with your note shown first; edited notes re-read, deleted ones removed. |
| **Copy as Markdown** | Any unit, with its sources' words and conflicts, for any note app. |
| **Forget** | Take a saved thing out (**forget** in the list of what you saved; units left without a source go too). |
| **Language** | Explanations in the language of what you read, Chinese, or English. |
| **Personal** | Only you see your notebook. |

See [The shared notebook](concepts/memory.md).

## Where you read and write

### Browser extension (Chrome, Edge, Arc; Safari through a build script)

- **Save anything**: right click or ⌥⇧S; a notification says what saving did.
- **On a paper's page** (arXiv, OpenReview, ACL Anthology, Semantic Scholar, DOI): a card with the research
  questions it bears on and how, the saved claims it touches, **Save to notebook**, **Save to Zotero** (or the
  BibTeX and DOI), **Sentences your saves argue with**. Unrelated papers leave only a small tab.
- **Context**: the page and your selection (sites can be excluded).
- **Following**: people, topics and threads on Zhihu, X and Xiaohongshu read every 15 minutes in a background tab
  of your signed-in browser; Reddit and blog feeds read by the console; new posts into the notebook and the digest.
- **Ask on any page**: the side panel's conversation reads the page you have open (as you see it, signed-in pages
  too; a paper's page from its PDF) and the words you selected; **Ask Pippin** appears beside a selection (or right
  click → *Ask Pippin about …*). An answer can be **kept as a note on the page** (your question, Pippin's answer as its
  reply; the page goes into your Library), and **Open in Pippin** opens the page in the reader at the same words.
- **Side panel**: your notebook panel beside the page; for a PDF open in the browser, the sentences your saves argue
  with above it, each opening its page.
- **Interruptions** as browser notifications (a decision opens its experiment).

### Overleaf (through the extension)

- **A quiet hint** when you stop at a claim without a citation and papers you have may back it (by meaning only, no
  model call); click it for the passages.
- **Cite this** (⌥⇧C): candidates from your own memory and Library only, each with the passage that backs the
  sentence, or a plain "nothing supports this"; **Insert `\cite{}`** when allowed, and the missing entry added to
  the project's `.bib` when that is allowed too (the extension opens the `.bib`, appends, and returns to your file);
  otherwise copy.
- **Check references** before you submit: every reference exists, none is retracted, each `\cite` supports its
  sentence (differing numbers named, with the paper's words), key work not cited, keys cited but undefined and
  entries unused. A report, never an edit; also in the Library (**Check references**) for a pasted `.tex` and `.bib`, or for the
  manuscript as a **PDF** (numbered or author-year references). The references are watched for retractions from
  then on, and new papers that overlap the manuscript's contribution interrupt you.
- **Context**: the sentence at your cursor.

### The macOS orb (Tauri)

- A ball at the screen's edge: drop a link, a selection or a file to save it (it swallows it and says what
  happened); click or ⌥Space for the panel; drag it and it settles against the nearest side.
- Knows the app and window in front (Accessibility) and says in a bubble when something you saved bears on it. In
  another PDF reader (Preview, Acrobat, Skim…) it tells the paper from the file name and says when your saves argue
  with it, with the pages.
- **⌥⇧N**: type a thought about what is in front; kept as your note (and on the Zotero item when allowed).
- Starts the console in the background if it is not running.
- An orange dot and a macOS notification for interruptions, including an experiment waiting for your decision.
- **Voice** (below).

### Obsidian

- The console reads the vault (only the folders you name, never those you exclude), or the plugin sends notes
  as you write.
- The plugin: notebook panel in the sidebar with your note and selection as context; **Literature note from a
  paper** (DOI, arXiv id or title); **Append an idea unit**; **Save the selection**; **Send this note now**.
  Creating and appending follow the write levels (off gives the Markdown to copy).

See [Obsidian](guide/obsidian.md).

### Voice (macOS)

- Hold **⌥⇧Space**, speak, let go. Recognition on your Mac (whisper.cpp) or by the console (`AURORA_WHISPER`);
  recordings are not kept.
- **Pointing words**: *this sentence/paragraph* (selection, else the text around you), *this figure* (a capture
  of the window in front, only then), *this paper*, *the one before*, *the second one* (of the digest you heard).
- Ask (about what is in front, or your notebook), compare two things, save, keep a thought, add to the reading
  list, let something sink or raise it, ask for a citation, start a deep read, hear the digest, **undo**.
- **Short aloud, complete on the panel** (*You said*, with sources and their words); answers only from sources.
- Writes confirmed in words (*say undo to take it back*); *ask* level asks and your next words decide; off says
  where to turn it on; a manuscript is never changed by voice.
- System voice (`say`), or a cloud voice for long things; pressing the key again stops it.
- **The digest to listen to**, numbered, so you can act on an item afterwards.

See [Browser extension and desktop app](guide/extension-and-desktop.md) and [Voice](guide/voice.md).

### Connecting entry points

Without accounts nothing beyond the console's address; with accounts a **device token** per entry point (Notebook
page → *How things come in on their own* → **Make a token**), revocable, with when it was last used.

## What Pippin may write

- Reading your pages, Zotero, notes and manuscripts needs no permission; **every kind of write is off** until you
  change it under the account button → **What Pippin may write**.
- Levels: **Off** (you get what it would write, to copy), **When I do it**, **Also on its own**.
- Kinds: save papers and PDFs to Zotero; write notes on Zotero items; send PDF notes as Zotero annotations; mark
  items to read; insert citations in Overleaf; add `.bib` entries; create notes in Obsidian; append to a note.
- **Every write is logged** with what undoing it takes; Zotero writes are undone from the log, others say how
  (⌘Z in Overleaf, the note in Obsidian). Changing a manuscript's text is never offered.

## Accounts and sharing

| Feature | What it does |
|---|---|
| **Single-user by default** | No sign-in, the server's key. |
| **Accounts** | `aurora users add … --admin` turns sign-in on; invite by a one-time link (14 days); roles admin, member, guest (by email domain); groups. Admins manage people and rules but get no rights in others' projects. |
| **Sharing a project** | Owner, Editor, Commenter, Viewer, or custom per right (view, comment, edit, start runs, answer decisions, share, manage); the rest of the team: none, see, or see and comment; who answers a run's decisions. A private project answers "not found". |
| **Read-only report links** | For people without an account, expiring after 7, 30 or 90 days. |
| **Confidential projects** | No outside links, hidden from the team, skills stay inside, only providers that keep no data. |
| **Private conversations** | Each person's front-desk conversation and brainstorms are their own; the activity log records who did what, never what anyone wrote. |
| **Own API keys** | Each person's OpenRouter key, stored encrypted, never shown back; the team key as a fallback if the team allows. A run is charged to whoever started it. |
| **Team rules** | Report links, guests starting runs, new projects confidential, admin approval for skills leaving a project, seeing that other projects exist, members using the team key, allowed model providers. |

See [Accounts and sharing](guide/accounts.md).

## Notifications

- Channels: WeCom group robot, Server酱 (personal WeChat), Feishu/Lark, Slack, any webhook; several at once; a
  test button; per-person.
- Events: a decision waits, a run finished, failed or stopped on its own, 80 % of the budget, the estimate
  exceeded, the agent replied, a skill proposed, a GPU job ended, something in your notebook you would regret
  missing, and the daily digest.
- **Approve and continue** / **Stop the experiment** links that confirm on a page (so link previews cannot act),
  signed, expiring after a day, respecting who may decide.
- **Quiet hours** with a summary when they end, and kinds that still come through.

See [Notifications](guide/notifications.md).

## Around the console

| Feature | What it does |
|---|---|
| **Files** | Upload files and whole folders with a message, to the project (shared by its runs), as a run's inputs (copy-on-write clones), or into a running experiment; preview, folders, delete, unpack archives; limits per file and project. |
| **Search** | Projects, runs, reports, chats and skills, with the matching passage. |
| **Analytics** | Cost and outcomes over 7–365 days: total cost, runs, completion share, mean duration, cost per day and per project, the most expensive runs. |
| **Settings** | Defaults for new experiments (models of every role, budget, time, decision mode, front desk, ideation, research limits, live auditor, container, skill learning), the environment (key set or not, Docker, Claude Code, CPUs, memory, disk), notifications, cluster jobs and the SSH GPU machine. |
| **Remote use** | Without accounts it listens on 127.0.0.1 (use an SSH tunnel); with accounts it can sit behind HTTPS. |

## Command line

```text
aurora quickstart | demo | init | doctor
aurora run CONFIG [-o objective] [--name] [--mock]
aurora status | resume CAMPAIGN_DIR
aurora serve [--port] [--config base.yaml]
aurora skills list|add|remove
aurora users add|list|passwd|role|remove
aurora results record|list|show     (for the agents)
aurora job submit|status|await|wait|logs|cancel   (for the agents)
aurora papers search|show|pdf|check|list|cite|bib (for the agents)
```

In a terminal, decisions are asked at the prompt. See [Command line](guide/cli.md).

## Reference and research

- [Configuration](reference/configuration.md): every setting, generated from the schema.
- [Campaign files](reference/workspace.md) and [Events](reference/events.md): what a run writes and emits.
- [Models and costs](guide/models-and-costs.md): one OpenRouter key for every role; per-role models; typical costs.
- [Reproducing the paper](reproducing.md): every number of the paper's evaluation recomputed offline for free,
  or re-run with models.
- [Development](development.md): tests run offline in mock mode.

## Not there yet

- The notebook is personal; sharing idea units with a team comes later.
- Embeddings come from an API (OpenRouter `text-embedding-3-large`); a local model may follow.
- Mobile entry points (headphones, lock screen) come after the desktop.
- Notion is not connected; Logseq works as a Markdown folder.
- Writing into Zotero needs a key with write access.
