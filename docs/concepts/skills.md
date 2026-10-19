# Skills

Skills carry know-how from one campaign to the next: experts' conventions, the
procedures that worked and the failures that were diagnosed. They are
[Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills): a
folder with a `SKILL.md` that an agent loads when its task matches. Before every
agent session AURORA writes the current library to the campaign's
`.claude/skills/` (and, for agents inside the container, to their home), and
lists the skills in the agent's system prompt, so it knows when to use them.

## What a skill contains

A skill is a structured specification, in these sections (each a `## ` heading):

| Section | What goes there |
|---|---|
| Purpose | the task it is for, and when to use it |
| Inputs and preconditions | what must be given or true before starting |
| Tools | the programs, libraries or commands it uses |
| Procedure | the steps, with the settings that matter |
| Expected outputs | files and results it produces |
| Validation checks | how to tell the result is right |
| Failure modes | what goes wrong, how it shows, and what to do |
| Interpretation | how to read the results, and their limits |
| When to ask a human | decisions the agent should not take alone |

Each skill also has a *kind* (procedure, failure, convention or correction),
an *origin* (your corrections, learned from runs, or written by hand) and a
*version*: a revision keeps the earlier text in the skill's history, with the
reason it was replaced.

## Where skills come from

### Your corrections

When you correct an agent — an instruction relayed by the front desk to a
working agent, one given before research starts (kept for the planning and
research agents), or a message with your answer at a decision point — AURORA
asks whether it is a
reusable correction or convention (a default setting, a validity rule, a
resource or safety rule, a method constraint) rather than a decision about this
task (which data, which direction). If it is, it drafts a skill from it at its
first occurrence, or a revision of the skill that already covers it. "Never kill
processes you did not start" becomes a skill; "use only the 2024 data" does not.

### The agents' own runs (self-distillation)

After every research job, and after each other phase passes its audit, AURORA
reads what the agent did (its transcript, failures in full) together with the
library, and proposes up to three skills: procedures that worked, with the
settings that mattered and how they were validated, and failures that were
diagnosed, with how they showed, their cause and the fix. Where a skill already
covers the ground it proposes a revision instead of a near-duplicate. This runs
in the background; the campaign does not wait for it.

### Skills you write

In the web console's *Skills* panel (*+ New* starts from the template), with the
CLI:

```bash
aurora skills add dft-convergence @dft-convergence.md --description "Our DFT convergence protocol"
aurora skills list
aurora skills remove dft-convergence
```

or as files, listing the directories in the config:

```yaml
skills_dirs:
  - ~/my-skills              # ~/my-skills/<name>/SKILL.md
```

## Accepting skills

By default nothing joins the library on its own. Each proposed skill appears in
the experiment's stream as a card with its text (for a revision, the new and
the current text side by side), the reason it was proposed and, for one from
your correction, your words. *Edit first* lets you change its name, description
and text before you add it; choose where it may be used, then *Add* (or *Apply
revision*), or *Dismiss* it. What you decided is kept: the card stays answered
after a reload, and adding it twice changes nothing. Skills accepted while a
campaign runs reach its next agent sessions.

| Where it is used | Scope | Available to |
|---|---|---|
| this run only | `experiment` | the experiment it came from |
| this project | `campaign` | the experiments of its web project |
| selected projects | `projects` | the experiments of the projects you tick |
| all projects | `global` | every campaign |

In the *Skills* panel you can read, edit (each save is a new version), look
through the history of, change where it is used, and delete every skill.

```yaml
skills:
  distill: true          # learn from the agents' runs
  from_feedback: true    # learn from your corrections
  auto_accept: false     # true: add proposed skills at once (to the library)
```

The proposals are written by `models.auditor`; each costs a few cents.
