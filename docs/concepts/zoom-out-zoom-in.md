# Zoom out and zoom in

Two ways to read a field, built to be used together:

- **Zoom out**: a *map* of a research direction. It answers, in order, why the thing studied is needed,
  what the field means by it, what design space every paper chooses in, how far the field has come,
  where it is moving, what it assumes without saying so, and which problems are attempted but not
  solved well.
- **Zoom in**: a *deep read* of one paper, as twelve questions. The most important one reconstructs how
  the authors may have come to the idea from what they could know before it, and you write your own
  guess before you see it.

Both happen in conversation. In a brainstorm, ask what has been done in a direction, how far a field has
come, or whether an idea is new: the Librarian proposes to map it, and you start it with one click. Each
step's finding comes into the conversation as it is done (the capabilities, the definition, the design
space, the progress, the unsolved problems), and the map stays beside the brainstorm in the **Map** tab.
Disagree in the conversation ("aren't the policy's weights memory too?") and the options come back there,
each with what it would change; one click rebuilds the map that way. From then on the agents read the
map with every answer: where an idea sits in the design space, which unsolved problem it answers, which
papers are nearest. The ideas of the space are placed on the map too.

Ask the Librarian to deep-read a paper and it proposes one; when the reading is done it asks how you would
have come to the paper's idea, your next message is your guess, and the authors' path follows. The twelve
answers are in the paper reader's **Deep read** tab.

## Finding the papers

Before any step, the papers are found the way a careful literature review finds them, following
[Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) (ARS; its protocol text is
read at run time from `aurora/third_party/ars/`, CC BY-NC 4.0):

1. **Scope first.** The question the map answers, what is in and out of scope, two to four sub-questions, and
   inclusion and exclusion criteria fixed before searching. The map stops here for your word in the
   conversation: reply with what to change, or go on. A map started from a research question you settled
   with the Mentor is scoped from it and does not stop.
2. **Search.** Web searches per sub-question and for surveys and lists, the papers of your conversation and
   your space, and searches of the indexes; every paper named is checked in the indexes, and one that is not
   found is dropped.
3. **Screening.** Two models of different families screen each paper by title and abstract against the
   criteria; where they disagree, a third adjudicates; uncertain ones are read in full text. A sub-question
   with too few papers, or a skew (most papers from one year or venue), gets a targeted search.
4. **Your word again**, with the counts at each stage (PRISMA style) and every decision: add or drop papers by
   name, or go on.

## The map is a chain

Each step uses only what the steps before it produced. That makes every part traceable, and every
part open to challenge.

| Step | It answers | It produces |
|---|---|---|
| 0. Why | What goes wrong without it, in concrete tasks? | The **capabilities** a solution needs (four to eight). They are the yardstick for everything after. |
| 1. Definition | What does each community mean by the term? Where do they disagree? | A working definition that covers the capabilities, its explicit **boundary decisions**, and its **components**. |
| 2. Design space | Which choices does every paper make? | Dimensions, each one component of the definition. Three models (the ideation seats' models) propose one framework each. |
| 3. Placement | Where does each paper sit? Does the framework hold? | Every paper placed, with its own words as evidence; held-out papers placed separately; misfits as proposals. |
| 4. Progress | How well is each capability solved? | Per capability: status, best results, the conditions they need, limitations that recur, conflicting results, cost, where evaluation and need part. |
| 5. Trends | How did the field get here, and where is it going? | A timeline with what drove each turn, and how papers move through the design space over time. |
| 6. Assumptions | What does nearly every paper accept without saying so? | Each with why it is accepted, what would overturn it, who challenges it. |
| 7. Problems | What is attempted but not solved well? | Problems chosen by importance and distance to solved, each **diagnosed** (signal, data, representation, evaluation, compute, an assumption, the definition), why now, approaches, a first experiment, the likely failure, and the closest existing work, found by a search. |
| 8. Read next | Which papers do the judgements rest on most? | Three to five papers, with the deep-read questions to focus on. |

Empty regions of the design space are not treated as opportunities in themselves: an empty region is
often empty because it is not worth filling. They appear only as possible *approaches* to a problem
that is attempted and not solved.

### How the frameworks are scored

All three frameworks place the same papers. Three scores are **computed** from the placements:

- *papers fit*: the share of papers placed without a strained reading;
- *held-out fit*: the same for the held-out papers, which took no part in building the framework and are
  placed in a separate call that sees no other placement;
- *dimensions independent*: one minus the mean normalised mutual information between pairs of dimensions.

One is **judged** by a model, with its reason: how well the positions explain differences in what the
papers achieve and what they cost. The framework with the best weighted score is used; you can switch.

### The loops back up the chain

- **A paper that does not fit** (a held-out paper, or one that joined the literature later) becomes a
  proposal to revise the dimensions. Accept it and every paper is placed again; the trends and problems
  follow.
- **A placement you dispute**: say what is wrong; the paper is placed again with your note.
- **Challenging the definition** ("aren't the model's weights memory too?"): first you get what supports
  the challenge and two or three ways to redraw the boundary, each with what it would change (a new
  dimension, which papers join, which capabilities' progress changes) and a search for the papers it
  would bring in. Nothing changes until you apply one; then the map is rebuilt from the definition.
- **New papers**: papers that joined the space's literature since the map was built are placed in it
  (when you open the map, at most once an hour, or with *place them*).

Every change is a new **version** with its reason; earlier versions stay readable.

### Evidence and labels

Papers come only from the scholarly indexes, checked like every reference. A paper's card is drawn
from its PDF when an open copy exists (its first pages and its limitations or conclusion), else from
its abstract, and says which. Quotes are kept only when the text contains them.

Every paragraph says what kind of claim it makes: **Paper says**, **Literature**, **Inference**, or
**Guess**. Trends, gaps and directions are mostly inference or guesses, and say so.

## The deep read

Twelve questions, each answered in its own call with the paper's text, in this order:

1. the problem, why it matters, what solving it is worth;
2. whether it was solved before, and why earlier work fell short;
3. **how the authors may have come to the idea** (below);
4. the intuition, in a paragraph or two;
5. the method, followed on one real example from input to output;
6. the core mathematics, explained from the ground up (or that there is none);
7. the experiments, as question, experiment, answer;
8. the take-aways beyond the method;
9. the weakest assumption (the Skeptic);
10. the smallest reproduction of a core claim in one week (Methods);
11. counterexamples that would break it (the Skeptic);
12. a follow-up idea from a real limitation (the Librarian), with the closest existing work found by a search.

**Question 3** is answered from what the authors could have known: the introduction with the paragraphs
and sentences that state the paper's own contributions removed, the related work, and abstracts of
earlier papers (from the map, or the space's literature). It never sees the method or the experiments.
A second call then checks each step of the reconstruction against the introduction: *the authors say
so*, *fits what they write*, or *reconstruction only*. The answer stays covered until you write your
own guess (or skip); afterwards you tick which steps you had made, and see the one to study.

Quotes in the answers light up the passage in the PDF. You can ask more about any question, answer it
again, and send the follow-up idea to the brainstorm with its evidence.

## Cost

A map of 30 papers makes about 80 model calls with the brainstorm model (`models.brainstorm`, else the
front desk's), plus three framework proposals by the ideation seats' models (`models.ideation_seats`).
A deep read makes about fourteen calls. The console shows the estimate before you start.
