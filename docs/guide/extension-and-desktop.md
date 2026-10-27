# Browser extension and desktop app

Two entry points bring AURORA to where you read and write. Both work against your AURORA console and share its
[memory](../concepts/memory.md); neither keeps anything of its own.

## Connecting

Without accounts (a console only you use), nothing is needed beyond the console's address. With accounts, make a
token for each entry point on the console's **Memory** page → *Browser extension and desktop app* → **Make a
token**, and paste it into the extension's options or the app's settings. Tokens can be revoked there.

## The browser extension

Chrome, Edge, Arc and other Chromium browsers load it from `extension/` (Extensions → Developer mode → Load
unpacked). For Safari, `sh extension/safari/build.sh` on a Mac wraps the same code in an app (see the script).

| What | How |
|---|---|
| **Save anything** | Right click → *Save this page / the selection / the link to AURORA*, or ⌥⇧S. Pages behind a login (Zhihu, X, Xiaohongshu) are saved as you see them. A paper is recognised and read from its PDF. A notification says what saving did (merged into what, conflicts, the question it bears on). |
| **On a paper's page** | arXiv, OpenReview, ACL Anthology, Semantic Scholar, a DOI: a card on the right says which of your research questions it bears on and how, and which of your saved claims it touches; **Save to memory** and **Save to Zotero**. A paper unrelated to your work leaves only a small tab. Looking is not saving. |
| **Save to Zotero** | Only when you allowed AURORA to write to Zotero (account button → *What AURORA may write*); otherwise the card gives you the BibTeX and DOI to import yourself. |
| **What you are reading** | The page you are on and your selection go to AURORA as context (kept a few hours, never stored), so the memory shows what bears on it. Turn it off, or name sites it never reports, in the options. |
| **Following** | Right click → *Follow this page in AURORA* on a person, a topic or a thread, or add follows on the Memory page. Zhihu, X and Xiaohongshu are read every 15 minutes in a background tab of your own browser (where you are signed in); Reddit and blog feeds are read by the console. New posts go into your memory and the day's digest. |
| **The side panel** | The toolbar button opens your memory panel beside the page (in Safari, in a small window). |

## The desktop app (macOS)

Build it from `desktop/` (see its README). An orb sits at the edge of the screen.

| What | How |
|---|---|
| **Save** | Drop a link, a selection or a file (PDF or text) on the orb. It swallows it, and a bubble says what happened. |
| **The panel** | Click the orb, or ⌥ Space: your memory panel (Now, Ask me, Today). |
| **What you are doing** | The app and window in front (with macOS Accessibility access) are sent as context. Browsers are left to the extension, which knows the page. When something you saved bears on it, a bubble says so for a few seconds, and nothing else happens. |
| **The console** | It starts the AURORA console in the background when it is not running, and stops it when you quit. |

## Today's digest

The panel's **Today** tab: what came in over the last day (saved by hand or from what you follow), by research
question: merged into what you had (+1 source), new claims, new conflicts.
