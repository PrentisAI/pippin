# The shared notebook

The **Notebook** page keeps your research's own record: a page for every run, written from the run; clips from
papers with your words; notes you write at length; and everything you saved, from a paper on arXiv to a thread on
Zhihu, X, Reddit or Xiaohongshu. Every entry point uses it: the console, the browser extension, the desktop app and
the chat apps. On the left: every project with its runs, your clips, your notes and what you saved; on the right,
the one open.

What you save is kept by **claim**, not by document. A claim that three sources make is one *idea unit* with three
sources. Two units that say opposite things are linked as a **conflict**.

The notebook is personal: only you see yours.

## A page for every run

Each run has a page, written from the run itself, never filled in by hand: the idea it took; the hypothesis behind
it and what would refute it; why it was worth trying (the gap, why this idea over the others, the alternatives,
screening's scores); what it expected, set against what it found (its claims and recorded results, compared by a
model once per run and again whenever the findings change); then your notes on it. Clips you made for the run are
listed under it. A run's page in the console links to it, and the page is shared with everyone who can see the run.

## Clips and notes

A **clip** is a passage taken from a paper word for word, with where it is (the paper, the page) and, under it, what
you make of it in your own words. Make one in the [reader](literature.md#reading-with-pippin) from a selection; it
can be for a project and a run, and it opens the paper at its page.

**Notes** are written at length: headings, lists, quotes, code, saved a second after you stop typing. A clip goes into
a note where the cursor is, as the piece of the page it was cut from with your words under it; pieces are moved by
their handle and set side by side, so an argument can be laid out from the sources themselves.

## Idea units

| | |
|---|---|
| **Source** | One thing saved: a paper (verified in the scholarly indexes and kept in your Library, read from its open PDF), a post, a page, a passage. Its text is kept as a snapshot, so a deleted post can still be traced. |
| **Idea unit** | One claim, in a sentence, with a short gist of what supports it. |
| **Evidence** | Each source's own words for the claim. They are found again in the source's snapshot, or not kept. |
| **Conflict** | Two units that cannot both hold under the same conditions. |
| **Bears on** | How much a unit bears on each of your research questions. |
| **Your marks** | Let it sink or raise it again, "this changed my view", your answers to the questions it asks you. Each is kept with its time. |

When you save something:

1. It is read: a link is fetched, or the text an entry point sends is used. Pages behind a login are sent by the
   browser extension, as you see them.
2. The model takes out one to five claims, each with the source's own words. Words it cannot find in the text are
   dropped.
3. Each claim is embedded, and the units that may say the same thing are found, across languages. A model then
   decides whether the claim is the same claim, the opposite, a qualification, or new. A claim reported from
   another setting is still the same claim (another GPU, another dataset, numbers that agree); it is different
   only when a condition changes the conclusion.
4. A claim that is the same as a unit is merged into it (one more source). Otherwise it becomes a unit of its
   own. Opposites are linked as conflicts.
5. Each unit is scored against your research questions.

What happened comes back in a sentence, for example *Merged into "…", now 3 sources; bears on Q1.*

A merge can be undone: **not the same** on a source splits it into a unit of its own, and the notebook remembers:
when it aligns new claims later, it is told which claims you kept apart from each unit, so the same mistake is not
made again. Two units the notebook failed to merge can be joined: **same claim, merge** where one qualifies the
other. A claim can be said in your own words (**edit**), and a saved thing taken out (**forget**; claims only it
made go too).

## Research questions and context

The notebook is organised by your research questions: those you settle with the Mentor in a brainstorm (they come
in on their own) and any you add on the Notebook page. They appear as Q1, Q2, … next to **Reading** (under *What bears on your questions*), which is what
an entry point says you have open (the page, the paper and the page you are on, your selection). On the Notebook
page you can set that by hand.

## Three layers

For the chosen context, every unit is in one of three layers:

| Layer | What is in it | At most |
|---|---|---|
| **Surface** | Bears directly on the context and tells you something new; conflicts first | 3 |
| **Absorbed** | Bears on it, but its point is already part of what the question has gathered; you need not read it | 5 |
| **Sunk** | Unrelated now, or you let it sink. Nothing is deleted: search finds it, and another context may raise it | — |

Units are ranked by embedding, then the top twenty by the model. For each, the model gives how much it bears,
whether it is new, how it stands to what you are reading (challenges, adds, supports, background, a tool) and
why it is at that depth. Open a unit for that reason, its sources (platform, language, the quoted words, the
original and the snapshot), the unit it conflicts with, and what you did with it.

## Ask me

Instead of a summary, the **Ask me** tab asks you one question from what surfaced, usually from a conflict:
*which side do you lean to, and what in your own setup decides it?* Your answer is kept. The next time those units
surface, it comes with them, so you can see whether your view has moved. It also joins the research plan of the
question it is about (the **Plan** the Mentor builds with you, under *Evidence strategy*), so your plan and your
thinking log are one record.

## What a question's claims add up to

With a research question as the context, **What it adds up to** (above the absorbed claims) writes what they amount
to: where they agree, where they conflict and why, what is open. When there is a map of the field around the
question (the one made from the conversation where you settled it, or your map nearest to it in meaning), it starts
from where the field stands: how far each capability is, what is unsolved.

## In a brainstorm

**Bring to a brainstorm** puts a claim on a board as material (*From your notebook · 3 sources*), to file under an
idea as evidence for or against. In your own (private) brainstorms the Librarian and the Skeptic search your notebook
too and record what they find as evidence, saying it is yours; in a shared brainstorm your notebook is never used.

## This week

A report of the week: what you saved and how much of it merged into what you had, new units and conflicts by
question, what changed your view, what you answered, and what has been sunk long enough to archive.

## When Pippin interrupts

Only for what you would regret missing; everything else waits for the daily digest.

| | |
|---|---|
| **Retraction** | A paper you rely on (in your notebook, your Library, or a manuscript whose references you checked) has been retracted. Checked once a day against OpenAlex and Crossref's retraction notices (which include the Retraction Watch data). Always interrupts. |
| **Overlap** | A new paper (saved, or from what you follow) overlaps a research question you settled with the Mentor and the plan you made for it, or the contribution of a manuscript whose references you checked; a model confirms the overlap. At most two a day; quiet hours hold them. |
| **Decision** | An experiment of yours waits for your decision: the dot and the notifications, until you decide (the notification channels already get it as *decision needed*). |

An interruption goes to the desktop app (a dot and a notification), the browser extension (a notification), the
panel (a banner) and your notification channels that take *Something you would regret missing*. **Not
worth interrupting me for** moves that kind to the digest from then on.

## Your own notes

Your notes are the most telling source: they say what you already think. Point the console at an Obsidian vault (or
any folder of Markdown files) on the Notebook page → *How things come in on their own* → **Your notes**, naming the subfolders to read and those never to
read (a journal, private notes), or let the [Obsidian plugin](../guide/obsidian.md) send them. A note's claims are
marked as yours and aligned with what you saved; a paper that disagrees with your own note is the conflict shown
first. An edited note is read again (its old version's evidence goes), a deleted one leaves the notebook. Any unit
copies as Markdown (claim, sources with their words, conflicts) for your notes.

## Models and settings

| | |
|---|---|
| `models.memory` | Takes the claims out, aligns them, ranks them, asks the questions (default `anthropic/claude-haiku-5.5`). |
| `models.embedding` | An OpenRouter embedding model (default `openai/text-embedding-3-large`). A local model may come later. |
| Language | On the Notebook page (*How things come in on their own*): explanations in the language of what you read, in Chinese, or in English. |

Saving something costs a few model calls (taking the claims out, aligning them) and one embedding call. Ranking
for a context is cached until your notebook changes. A paper read for the Zotero card, a citation suggestion, the
sentences your saves argue with, and a voice request are a model call or two each; checking a manuscript's
references is about one call per eight citations, plus a few for the missing work.

## What Pippin may write

The notebook is Pippin's own. Writing into your tools (Zotero, Overleaf, your notes) is set separately: see
[What Pippin may write](../guide/accounts.md#what-pippin-may-write).
