# Skills

Skills carry know-how from one campaign to the next. They are
[Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills): a
folder with a `SKILL.md` that an agent loads when it is relevant. At the start
of a campaign AURORA writes all available skills to the campaign's
`.claude/skills/`, where every agent session finds them.

There are two sources, matching the paper's skill library with human and
self-distilled skills.

## Skills you write

Either keep them as files and list the directories in the config:

```yaml
skills_dirs:
  - ~/my-skills              # ~/my-skills/<name>/SKILL.md
```

```markdown
---
name: dft-convergence
description: How we converge plane-wave DFT calculations in this group
---
Converge the k-point mesh and cutoff to 1 meV/atom before any production run;
report both settings next to every energy ...
```

or put them in the skill library shared by all campaigns:

```bash
aurora skills add dft-convergence @dft-convergence.md --description "Our DFT convergence protocol"
aurora skills list
aurora skills remove dft-convergence
```

The web console's *Skills* panel shows the same library.

## Skills distilled from campaigns

After each phase passes its audit, AURORA asks a model to extract up to three
reusable rules from the phase's transcript and emits them as *skill
suggestions*. Nothing is saved automatically: in the web console you accept,
edit or dismiss each suggestion. Accepted skills join the library with a scope:

| Scope | Available to |
|---|---|
| `global` | every campaign |
| `campaign` | experiments of the same web project |
| `experiment` | that experiment only |
