# Results, reports and runs

A run ends with a report. This page is about what you can do with it: trace
every number back to the code that computed it, download it, rerun it, put it
into Overleaf, set runs side by side, start a new run from any point of an old
one, and know before a run starts what it will likely cost.

## Results you can trace

Every number the agents report is **recorded** when it is computed, with what
produced it. The agents (research, analysis, writing) have a command for it:

```
aurora results record "weekend demand elasticity" -0.42 --uncertainty 0.05 \
    --script analysis/elasticity.py --command "python analysis/elasticity.py --weekend" \
    --inputs data/trips.parquet --outputs figures/elasticity.png \
    --note "log-log OLS of trips on price, station fixed effects"
aurora results list            # what was recorded, with IDs
aurora results show R3         # one result and its provenance
```

Each result gets an ID (`R1`, `R2`, ...). The agents are told to cite every
number with its ID right after it (`-0.42 {R3}`); the report writer gets the
table of results and does the same. Recording again under the same name
*supersedes* the old result (when an agent recomputes something), and the old
one stays in the record.

With each result Pippin keeps:

| | |
|---|---|
| the value | with its unit and uncertainty |
| the script | its path, a SHA-256 of its content, and a **copy as it was then** (`provenance/R3/`) |
| the command | as the agent typed it |
| inputs, outputs | the files it read and wrote |
| who and when | the phase or research job, the time |

The ledger is `results.jsonl` in the run's directory; see
[Campaign files](../reference/workspace.md). When the report is written, Pippin
checks its result IDs: which recorded results it cites, IDs that name no
result, and results it cites that were superseded (the
`report_results_checked` event).

## The report

When a run has a report, its header has **Report**. The report opens over the
experiment:

- every cited number is a chip; a click shows the result's provenance: the
  value, the script (open it as it is now, or **as it was then**), the command
  (copy it), the inputs and outputs, the job that computed it, and when;
  a superseded result says so and names the newer one;
- every reference (`[Lee2021]`) is a chip that opens the paper's card from the
  [Library](../concepts/literature.md);
- next to it: *What was done* (the setup and the chosen direction), *Key
  results* (the current results), *Conclusions* (the claims the analysis made)
  and *Figures*.

Earlier drafts of the report stay in `phases/writing/`.

## Downloads

From the report:

| Download | What is in it |
|---|---|
| **Report (zip)** | `report.md`, `report.tex` (LaTeX), `figures/`, `references.bib`, `results.csv`, `results.json`, `provenance/` (the scripts as they were when each result was computed), `environment.txt`, `reproduce.sh`, a README |
| **Everything (zip)** | the run's whole directory (workspace, phases, audits, transcripts; up to 2 GB) with the report export in `export/` |
| **reproduce.sh** | the script alone (below) |

`report.tex` is made by Pippin (no pandoc): sections, lists, tables, figures,
citations as `\citep{...}` against `references.bib`, and each result ID as a
link to an appendix table of every result with its script and command.

## Reproducing a run

`reproduce.sh` replays, in the run's container image, the commands that
produced the recorded results and the commands of its GPU jobs, in the order
they first ran. Unpack the report zip next to the run's `workspace/` (the
*Everything* zip has both) and run `bash reproduce.sh`. Before the run's
workspace was taken out of the container, Pippin saved `pip freeze` as
`.aurora/environment.txt`; the script installs from it first. It needs Docker,
and, for GPU jobs, the NVIDIA container toolkit. Data the agents downloaded
during the run is downloaded again by the same commands.

## Putting a report into Overleaf

**Put into Overleaf...** in the report sends `report.tex`, the figures,
`references.bib` and the results into a folder of an Overleaf project
(`aurora/<run name>/` unless you name another), through Overleaf's git
integration. It needs:

- the project's address (`https://www.overleaf.com/project/...`) or its git URL
  (*Menu → Git* in Overleaf), remembered per project;
- a **git token** (Overleaf *Account settings → Git integration*). It is kept
  with your other keys, encrypted, and never shown again.

The push is one commit, made in your name. Pushing the same report again
changes nothing. `\input{aurora/<run>/report}` (or copying parts) brings it into
your paper.

## Comparing runs

A project with more than one experiment has a **Compare** tab. Pick the runs
(at first the four latest that finished with results); they are set side by
side:

- **Setup**: phases, models of every role, budget, ideation mode, where it was
  forked from;
- **Outcome**: status, cost, time, the direction taken and screening's scores,
  GPU jobs and GPU hours;
- **Results**, matched by name across runs, each value a chip with its
  provenance;
- **Conclusions** of each run.

Cells that differ between runs are marked; **Only what differs** hides the rest.

## Forking a run

Pippin keeps a **fork point** after every phase and after every research job:
what the run knew (its checkpoint, artifacts, mental model and cost) and a
snapshot of its workspace (files over 50 MB, or past 1 GB in all, are listed but
not kept; a fork recomputes them).

**Fork...** in an experiment's header starts a new run from one of them:

1. Choose the point (*after planning*, *after job 3 (3 of 5)*, ...).
2. Say what to do differently from there on. After ideation, you can also take
   **another direction**: one of the ideas the meeting had but did not choose,
   or your own.
3. Optionally another research model, a budget and a name.

The new run starts with the files, results and transcripts made until the
point, and with the cost spent until then (shown as inherited). It goes on with
your guidance in every following phase. The original run is not changed;
Compare shows which run a fork came from.

## Estimate before a run

The proposal card shows what the run will likely cost and take before you
confirm it: *Estimate: $3.1–8.6 · 1.2–3.4 h*, with how it was made and a table
by phase. It changes as you change phases, models or the ideation mode.

- With **three or more earlier runs** of the same phases and the same research
  model (in projects you can see), it is the middle half of what they cost and
  took.
- Otherwise it is built from what each phase typically costs with a Claude
  Sonnet-class model, scaled by the chosen models' prices (OpenRouter's price
  list; by model class when that cannot be fetched), with two to five research
  jobs, and the auditors on top.

The time does not include waiting for your decisions. If the budget is below
the low end, the card says so. While the run goes, its header shows the
estimate next to the cost, amber once it is exceeded; at the upper end and at
twice it, the run says so in its stream and sends the `estimate_exceeded`
[notification](notifications.md).
