# Browser extension and desktop app

Two entry points bring Pippin to where you read and write. Both work against your Pippin console and share its
[notebook](../concepts/memory.md); neither keeps anything of its own.

## Connecting

Without accounts (a console only you use), nothing is needed beyond the console's address. `aurora serve` listens on
`http://localhost:8000`; the desktop app starts its own console on `http://localhost:8200`. The extension's side panel
finds a console on this machine by itself (it tries the address in its options, then ports 8000, 8200 and 8002) and
keeps the one that answers; when none does, it says so and how to start one. A console on another machine needs its
address in the extension's options (and the Zotero and Obsidian plugins' settings). With accounts, make a
token for each entry point on the console's **Notebook** page → *How things come in on their own* → **Make a
token**, and paste it into the extension's options or the app's settings. Tokens can be revoked there.

## The browser extension

Chrome, Edge, Arc and other Chromium browsers load it from `extension/` (Extensions → Developer mode → Load
unpacked). For Safari, `sh extension/safari/build.sh` on a Mac wraps the same code in an app (see the script).

| What | How |
|---|---|
| **Save anything** | Right click → *Save this page / the selection / the link to Pippin*, or ⌥⇧S. Pages behind a login (Zhihu, X, Xiaohongshu) are saved as you see them. A paper is recognised and read from its PDF. A notification says what saving did (merged into what, conflicts, the question it bears on). |
| **On a paper's page** | arXiv, OpenReview, ACL Anthology, Semantic Scholar, a DOI: a card on the right says which of your research questions it bears on and how, and which of your saved claims it touches; **Save to memory** and **Save to Zotero**. A paper unrelated to your work leaves only a small tab. Looking is not saving. |
| **Save to Zotero** | Only when you allowed Pippin to write to Zotero (account button → *What Pippin may write*); otherwise the card gives you the BibTeX and DOI to import yourself. |
| **What you are reading** | The page you are on and your selection go to Pippin as context (kept a few hours, never stored), so the notebook shows what bears on it. Turn it off, or name sites it never reports, in the options. |
| **Following** | Right click → *Follow this page in Pippin* on a person, a topic or a thread, or add follows on the Notebook page (*How things come in on their own*). Zhihu, X and Xiaohongshu are read every 15 minutes in a background tab of your own browser (where you are signed in); Reddit and blog feeds are read by the console. New posts go into your notebook and the day's digest. |
| **The side panel** | The toolbar button (⌥⇧A) opens Pippin beside the page (in Safari, in a small window): the conversation, and your notebook a tab away. |
| **Ask on any page** | Ask in the side panel and Pippin reads the page you have open, as you see it (signed-in pages too), with the words you selected; a paper's page (arXiv, a DOI) is read from the paper's PDF. Select words and click **Ask Pippin** beside them (or right click → *Ask Pippin about …*) to ask about them. Pippin says what the page says and where, and says so when it does not. **Keep as a note on the page** puts your question and its answer as a note on those words (the page goes into your Library); **Open in Pippin** opens the page in the reader at the same words, for what the browser cannot do (gaps to write in, Pippin's notes and figures, pieces into the Notebook). Turn the *Ask Pippin* button off in the options; excluded sites are never read. |
| **Sentences your saves argue with** | On a paper's card, and above the side panel when a PDF is open in the browser: the sentences of the paper that something you saved before challenges or qualifies, each opening its page. (The extension cannot draw on the browser's PDF viewer; Pippin's own reader and the Zotero plugin mark them in the margin.) |
| **Interruptions** | An experiment waits for your decision (the notification opens it), a paper you rely on was retracted, or a new paper does what you set out to do: a notification, once. Everything else waits for the digest. |

### In Overleaf

| What | How |
|---|---|
| **A quiet hint** | Stop at a sentence that makes a claim without a citation, and when papers you have may back it (judged by meaning only, no model call), a small card says so: *2 papers you have may back this sentence*. Click it (or ⌥⇧C) for the passages. |
| **Cite this** | Put the cursor in a sentence that needs support and press ⌥⇧C (or **Cite this**). Candidates come from your own memory and Library only, each with the passage that backs the sentence; when nothing does, it says so. **Insert** puts `\cite{key}` at the cursor, only when you allowed *Insert citations in the manuscript*; with *Add entries to the .bib file* allowed too, the entry is added to the project's `.bib` (the one `\bibliography{}` or `\addbibresource{}` names, else the first) when its key is missing there: the extension opens that file, appends the entry and returns to yours. Otherwise you get them to copy. Overleaf's ⌘Z undoes an insert; each write is logged. |
| **Check references** | Reads the project's `.tex` and `.bib` and checks, before you submit: every reference exists (and its metadata), none is retracted, each `\cite` supports its sentence (numbers that differ are named, with the paper's words), and which key work closest to your contribution is not cited. The report is advice: nothing in the manuscript is changed. It is also in the console's Library (**Check references**), for a pasted `.tex` and `.bib` or for the manuscript as a **PDF** (its reference list is read, numbered or author-year, and each citation in the text is matched to its entry). The references checked are watched for retractions from then on. |
| **What you are writing** | The sentence at your cursor goes to Pippin as context a few seconds after you stop typing. |

## The desktop app (macOS)

Build it from `desktop/` (see its README). An orb sits at the edge of the screen.

| What | How |
|---|---|
| **Save** | Drop a link, a selection or a file (PDF or text) on the orb. It swallows it, and a bubble says what happened. |
| **The panel** | Click the orb, or ⌥ Space. It opens on your conversation with Pippin, with the notebook (Now, Ask me, Today) a tab away. It is one thread, kept per person, that goes on where it stopped, whether you type here, speak to the orb, ask in the browser's side panel, press ⌘K in the console or write from a [chat app](notifications.md#talking-to-pippin-in-a-chat-app). Pippin answers from your notebook and about what is in front, says where your projects and runs stand and what they found, and gives you buttons to the right page of the console. When you ask it to act on a run (answer its decision, tell it something, stop it), it proposes the action and waits for you to confirm. |
| **What you are doing** | The app and window in front (with macOS Accessibility access) are sent as context. In another PDF reader (Preview, Acrobat, Skim, PDF Expert…) the paper is told from the file name (an arXiv id, a title in your Library or memory, or Zotero's "Author - Year - Title" looked up in the indexes), and when something you saved argues with it the bubble says so: *2 of your saves argue with this paper (p. 3, p. 7)*. Browsers are left to the extension, which knows the page. When something you saved bears on it, a bubble says so for a few seconds, and nothing else happens. |
| **The console** | It starts the Pippin console in the background when it is not running, and stops it when you quit. |
| **A thought** | ⌥⇧N opens a small box: type a thought about what is in front and press Return. It is kept as your own note in the notebook, and as a note on the paper's Zotero item when you allowed *Write notes on Zotero items*. |
| **Where it sits** | Drag it anywhere; it settles against the nearest side of the screen. |
| **Interruptions** | An orange dot on the orb and a macOS notification, for an experiment waiting for your decision (cleared once you decide), a retraction of a paper you rely on or a new paper that overlaps what you set out to do (at most two of those a day; quiet hours hold them). Say *not worth interrupting me for* on the panel and that kind waits for the digest from then on. |
| **Voice** | Hold ⌥⇧Space and speak; see [Voice](voice.md). |

## Today's digest

The panel's **Today** tab: what came in over the last day (saved by hand or from what you follow), by research
question: merged into what you had (+1 source), new claims, new conflicts. On top, one or two papers of the day
**worth reading closely** (the feed's best fits for your projects, papers you saved that bear most on your
questions), each with **Deep read** and **To read**. Each claim can be let sink, or compared with another
(**how does it differ from…**, then pick the other): the differences come with the words they rest on. The same goes into the daily digest
message (Settings → Notifications), with the interruptions of the day; the desktop app reads it aloud on request.

## Zotero and Obsidian

The [Zotero plugin](zotero.md#the-zotero-plugin) and the [Obsidian plugin](obsidian.md) are entry points too, with the
same tokens.
