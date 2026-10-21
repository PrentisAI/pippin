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

| | |
|---|---|
| **From Zotero** | Every item in the collection is checked against the scholarly indexes, like any other paper, and then joins the project's literature. An item that no index has, such as a thesis or an internal report, comes in as a paper you vouch for. |
| PDFs | Copied into the project's files under `papers/`, named by the paper's key (or its Better BibTeX key), so the agents can read them. Needs the account to have file access; files over 60 MB are skipped. |
| Highlights and notes | Your PDF highlights and comments, and your notes on the item, are shown with the paper on the Literature page. The agents see them too, so they know what you found important. |
| **To Zotero** | Papers in the project's literature that the collection lacks go into it, tagged `aurora`. That covers papers someone added and papers an agent cited and that were verified. A paper already in your library, in another collection, is put into this collection rather than copied. **Send them to Zotero** on the Literature page does this at once; **+ Zotero** sends a single paper. |
| Better BibTeX | If you use Better BibTeX, its citation keys name the PDFs and are used in **Export .bib**. |

Never sent: a reference that could not be verified. Never done: deleting or
changing anything in your Zotero other than adding papers to the collection and
the `aurora` tag.

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
