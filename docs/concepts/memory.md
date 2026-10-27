# The shared memory

Everything you save, from a paper on arXiv to a thread on Zhihu, X, Reddit or Xiaohongshu, goes into one memory.
Every entry point uses it: the console's **Memory** page now, the browser extension and the desktop app next.
The memory is kept by **claim**, not by document. A claim that three sources make is one *idea unit* with three
sources. Two units that say opposite things are linked as a **conflict**.

The memory is personal: only you see yours.

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

A merge can be undone: **not the same** on a source splits it into a unit of its own. Two units the memory
failed to merge can be joined: **same claim, merge** where one qualifies the other.

## Research questions and context

The memory is organised by your research questions: those you settle with the Mentor in a brainstorm (they come
in on their own) and any you add on the Memory page. They appear as Q1, Q2, … next to **Reading**, which is what
an entry point says you have open (the page, the paper and the page you are on, your selection). On the Memory
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
surface, it comes with them, so you can see whether your view has moved.

## This week

A report of the week: what you saved and how much of it merged into what you had, new units and conflicts by
question, what changed your view, what you answered, and what has been sunk long enough to archive.

## Models and settings

| | |
|---|---|
| `models.memory` | Takes the claims out, aligns them, ranks them, asks the questions (default `anthropic/claude-haiku-5.5`). |
| `models.embedding` | An OpenRouter embedding model (default `openai/text-embedding-3-large`). A local model may come later. |
| Language | On the Memory page: explanations in the language of what you read, in Chinese, or in English. |

Saving something costs a few model calls (taking the claims out, aligning them) and one embedding call. Ranking
for a context is cached until your memory changes.

## What AURORA may write

The memory is AURORA's own. Writing into your tools (Zotero, Overleaf, your notes) is set separately: see
[What AURORA may write](../guide/accounts.md#what-aurora-may-write).
