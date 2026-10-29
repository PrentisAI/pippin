# Pippin for Zotero 7

Every paper you add to Zotero is read in the background. The next time you open it, the item pane's **Pippin**
section has its methods, findings and limitations (each marked as stated by the paper or inferred), and what your
Pippin notebook makes of it: the research questions it bears on, what it conflicts with among what you saved, and which
of its claims you already had from elsewhere.

- **Sentences your saves argue with**: the sentences of the paper that something you saved before challenges or
  qualifies. Click one to open the PDF at its page.
- **Save to Pippin** in the reader's selection popup saves the passage into your notebook. While you select text, the
  plugin tells Pippin which paper and passage you are reading (kept a few hours, never stored; you can turn it off).
- **Save the card as a note** writes the card as a child note of the item, only if you allowed “Write notes on Zotero
  items” in Pippin (account button → What Pippin may write). The write is logged there.

## Install

```bash
zotero-plugin/build.sh          # → dist/aurora-zotero.xpi
```

In Zotero 7: **Tools → Plugins → ⚙ → Install Plugin From File…** and pick `dist/aurora-zotero.xpi`. Then
**Settings → Pippin**: the console's address (for example `http://localhost:8200`) and, when the console has accounts,
a token (console → Notebook → How things come in on their own → Make a token).

## What is sent

The item's DOI, arXiv id and title, and its PDF when Zotero has one (so the paper is read from its full text, not
only its abstract). The console keeps the PDF in its paper cache so the reader can open it too.
