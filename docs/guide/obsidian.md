# Obsidian and other note apps

Your notes join your [memory](../concepts/memory.md) as *your own* source: their claims are aligned with what you
saved, and a paper that disagrees with your note surfaces first.

## Reading the vault

Two ways, use either:

- **The console reads the folder** (when it runs on the machine that has the vault): Memory page → **Your notes**.
  Give the vault folder, the subfolders to read (empty: all) and those never to read (`Journal, Private`). New and
  edited notes are read every few minutes; deleted ones leave the memory. Only an admin may point a shared console
  at its disks.
- **The plugin sends notes** as you write them (after a minute without edits), from the folders you name in its
  settings. Off until you turn it on.

Notes shorter than a couple of sentences are skipped. Front matter is ignored. Logseq graphs are Markdown folders
too and work the same way.

## The plugin

Copy `obsidian-plugin/` into your vault as `.obsidian/plugins/aurora-memory/` and turn it on under Community
plugins. Set the console's address and, with accounts, a token. No build step.

| What | How |
|---|---|
| **Memory panel** | The ribbon icon: what surfaces for the note you are writing (the note and your selection go to Pippin as context; kept a few hours, never stored). |
| **Literature note from a paper** | A DOI, arXiv id or title: a note in your literature folder with the paper's methods, findings and limitations (each marked as stated or inferred) and what your memory makes of it. |
| **Append an idea unit** | Search your memory, pick a unit: its claim, its sources with their own words, and its conflicts are appended to the note you are in. |
| **Save the selection to Pippin**, **Send this note now** | Commands. |

Creating and appending notes are writes: they happen only as far as you allowed *Create notes in your vault* and
*Append to an existing note* (account button → What Pippin may write). Off gives you the Markdown to copy, *ask*
shows it first with a **Write it** button, *auto* writes. Each write is logged; Obsidian's file recovery undoes an
append.

In the console, any unit has **Copy as Markdown** for pasting into any note app.
