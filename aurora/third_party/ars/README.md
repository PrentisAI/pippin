# Academic Research Skills (ARS), in part

The files in this directory, other than this README, are unmodified copies from
[Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) by Cheng-I Wu,
commit `1ec249d` (2026-10-26; `socratic_mentor_agent.md` from commit `b34c63f`, 2026-10-27), licensed under the
[Creative Commons Attribution-NonCommercial 4.0 International License](LICENSE)
(CC BY-NC 4.0). **They may not be used for commercial purposes.** The rest of AURORA is under its own
license (MIT); this license applies to these files only.

| File here | Original path |
|---|---|
| `research_question_agent.md` | `deep-research/agents/research_question_agent.md` |
| `bibliography_agent.md` | `deep-research/agents/bibliography_agent.md` |
| `decision_rules.md` | `sr-screener/references/decision_rules.md` |
| `socratic_mentor_agent.md` | `deep-research/agents/socratic_mentor_agent.md` |

AURORA's paper search (`aurora/review/search.py`) reads these files at run time and gives the relevant
sections to the models as their protocol: scoping a research direction (topic decomposition, scope
boundaries, sub-questions), the search strategy and two-pass screening with inclusion and exclusion criteria
defined before searching, PRISMA-style search documentation and the distributional skew advisory, and the
screening decision rules (labels, ordered exclusion codes, conflicts and adjudication).

The brainstorm's Mentor (`aurora/brainstorm/mentor.py`) reads `socratic_mentor_agent.md` and the Socratic and
FINER sections of `research_question_agent.md` the same way: guided questioning in five layers without
proposing the researcher's question for them, predictions before evidence, counterpoints to certainty, one
adjacent facet at a time, the insights that become the researcher's plan, and the RQ Summary (or, when the
researcher asks for them, AI-generated candidate questions with their FINER scores).
