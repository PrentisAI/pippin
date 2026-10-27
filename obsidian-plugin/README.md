# AURORA for Obsidian

Your AURORA research memory beside your notes.

- **Memory panel** (ribbon icon, or the command *Open the memory panel*): what surfaces for the note you are writing.
  The open note and your selection are sent as what you are doing (kept a few hours, never stored; turn it off in
  the settings).
- **Your notes into your memory** (off until you turn it on, and only the folders you name): what your notes claim
  is merged with what you saved elsewhere, and a paper that disagrees with your own note says so. Edited notes are
  read again, deleted ones leave the memory. *Send all notes* reads the whole vault once.
- **Commands**
  - *Literature note from a paper*: a DOI, arXiv id or title → a note with the paper's methods, findings and
    limitations (each marked as stated by the paper or inferred) and what your memory makes of it.
  - *Append an idea unit from my memory to this note*: search your memory, pick a unit, and its claim, its sources
    with their own words and what it conflicts with are appended.
  - *Save the selection to AURORA*, *Send this note to my memory now*.

Writing into the vault happens only as far as you allowed it in AURORA (account button → **What AURORA may write**:
*Create notes in your vault*, *Append to an existing note*). Off gives you the text to copy; *ask* asks each time;
*auto* writes. Every write is logged there.

## Install

No build step: copy this folder into your vault as `.obsidian/plugins/aurora-memory/` (the files `manifest.json`,
`main.js`, `styles.css`), then **Settings → Community plugins → AURORA** (turn off restricted mode if needed). In
the plugin's settings, set the console's address and, when the console has accounts, a token (console → Memory →
Browser extension and desktop app → Make a token).

If the console runs on the same machine as the vault, you can instead let the console read the vault folder itself:
console → Memory → **Your notes**.
