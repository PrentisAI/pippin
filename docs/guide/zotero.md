# Zotero

Connect your Zotero and choose which collection goes with which project. The
papers in the collection then join the project's literature, and the papers the
project gathers go back into the collection. The agents also see your highlights
and notes on the papers.

## Connecting

Open a project's **Literature** tab, or the **Library**, and press **Zotero**.
Choose how AURORA reaches your Zotero:

| Way | What it can do |
|---|---|
| **Your Zotero account** (recommended) | Your own library and your group libraries, both ways, wherever AURORA runs. Create a key at zotero.org → *Settings → Security → Create new private key*, with *Allow library access* and, to send papers back, *Allow write access*. Give it access to the groups you want. |
| **Zotero 7 on this computer** | Only if AURORA runs on the computer where Zotero is installed. It is read-only, and works only while Zotero is open. In Zotero, turn on *Settings → Advanced → Allow other applications on this computer to communicate with Zotero*. |

The key is stored encrypted on the server, in the same way as API keys. Nobody
can read it back, admins included. Each person connects their own Zotero.

## Collections and projects

Next, choose a project for each collection, or *Your Library* for papers that
belong to no project. Collections whose names match a project's name are
suggested. Collections you leave out are not touched. You can map a collection
only to a project you can edit.

## What flows each way

Writing into your Zotero is off until you allow it, kind by kind, under the account button → **What AURORA may
write** (see [Accounts](accounts.md#what-aurora-may-write)). Sending a project's papers during a sync needs *Save
papers and their PDFs* set to *Also on its own*; **+ Zotero** on one paper needs it at *When I do it*; your notes on
a PDF go to Zotero with *Send the notes you keep on a PDF as Zotero annotations*.

| | |
|---|---|
| **From Zotero** | Every item in the collection is checked against the scholarly indexes, like any other paper, and then joins the project's literature. An item that no index has, such as a thesis or an internal report, comes in as a paper you vouch for. |
| PDFs | Copied into the project's files under `papers/`, named by the paper's key (or its Better BibTeX key), so the agents can read them. Needs the account to have file access; files over 60 MB are skipped. |
| Highlights and notes | Your PDF highlights and comments, and your notes on the item, are shown with the paper on the Literature page. The agents see them too, so they know what you found important. |
| **To Zotero** | Papers in the project's literature that the collection lacks go into it, tagged `aurora`. That covers papers someone added and papers an agent cited and that were verified. A paper already in your library, in another collection, is put into this collection rather than copied. **Send them to Zotero** on the Literature page does this at once; **+ Zotero** sends a single paper. |
| Idea summaries | Optional (off by default): each idea a paper is evidence for, written once as a note on the paper's Zotero item (with whether the paper supports or challenges it, and where the idea stands). Needs write access. |
| Better BibTeX | If you use Better BibTeX, its citation keys name the PDFs and are used in **Export .bib**. |

Never sent: a reference that could not be verified. Never done: deleting or
changing anything in your Zotero other than adding papers to the collection, the
`aurora` tag, idea notes if you turned them on, and the annotations of the
reader (below).

## Notes on PDFs

Notes made in the paper reader (see [Brainstorm](../concepts/brainstorm.md#reading-a-paper-with-the-agents))
and annotations on the PDF in Zotero are kept in step, both ways:

| | |
|---|---|
| **A note you make** | Becomes a highlight annotation on the paper's PDF in Zotero within seconds: the same words on the same page, in yellow, tagged `aurora`. |
| **An agent's note** | Stays in AURORA while it is a suggestion. Once you keep it, it goes too, in the agent's colour, tagged `aurora` and `aurora:skeptic` (or the agent's name), its comment starting with *Skeptic (AURORA):*. |
| **Replies and the idea** | Go into the annotation's comment: the idea the note bears on, then each reply with who wrote it. |
| **An annotation you make in Zotero** | Comes into the reader as your note, at the next sync (every 5 minutes, or **Sync now** in the reader's Zotero menu). The agents read it too. |
| **Edited on both sides** | The later edit wins. |
| **Deleted** | On one side, deleted on the other. In AURORA, Zotero is told a few seconds later, so **Undo** can still bring the note back. |

The notes stay in AURORA (and the reader says why) when Zotero is not
connected, when the key can only read, or when your Zotero lacks the paper or
its PDF. In the last case **Add it to Zotero** in the reader's Zotero menu puts
the paper into the project's collection and uploads its PDF to Zotero's storage;
the notes follow.


## When it syncs

Every 5 minutes, and whenever you open a project's Literature page. After the
first sync, only items that changed since the last one are fetched. **Sync now**
in the Zotero window shows what the last sync did: items looked at, papers
added, PDFs, highlights and notes, papers sent, and anything to look at, such as
an item that could not be checked because the indexes were down. Such an item is
tried again at the next sync.

**Disconnect** forgets the key and the mappings. Papers that already came in
stay in the projects, and nothing changes in your Zotero.

## Mendeley, EndNote and others

Export a collection as BibTeX, then use **Import BibTeX** on the Literature
page. Each entry is checked; the entries that are found go in, and the rest are
listed with the reason. **Export .bib** gives the project's papers back as
BibTeX.
