# The feed: what to read today

The **Library** opens on your feed: new papers, posts, blogs and discussions
from the people and sources you follow, each ranked against every project you
can see, and shown under the ones it fits with one sentence on why. Its first line says what it follows (people,
topics, your projects' objectives, other sources) and that what comes in is picked from them, with one box to follow
someone (searched at once), a topic or a blog's feed.

## One paper at a time

**Go through N new papers, one at a time** shows the feed's papers one by one: why it was picked, what it says, and
four ways on. **Skip** (J or ←) passes it by; **Less like this** (X) hides it and ranks similar ones lower; **Into
notebook** (S or ↓) keeps what it says among your saves; **Read with me** (R or →) puts it in the project it fits
and opens it in the [reader](../concepts/literature.md#reading-with-pippin). U undoes the last one. On a trackpad or
a phone, swipe: left to skip, right to read, down to keep.

## A card

Each card says what it is (*Paper*, *Post*, *Blog*, *Code*, *Video*, *Discussion*), where it
comes from, when, and for a paper its checked identity (`✓ arXiv:2610.08812`).
Below the title and the people (those you follow are links to their page) and a
summary, **Why for &lt;project&gt;** says what it means for that project: its
data, a planned step, a finding it supports or contradicts. The five dots show
how well it fits.

| Button | What it does |
|---|---|
| **Save to project ▾** | Keeps it under a project, or *for later*. A paper joins that project's literature (marked *from the feed*) and its Literature page. *Remove* takes it back. |
| **Bring to brainstorm ▾** | Puts it on a brainstorm board as material: a project's, or your own ideas. The Librarian notes why it was brought (unless the agents are set to *Only when asked*). |
| **Ask the front desk** | Opens the project's chat with the item attached; write your question, or just send. The front desk reads its summary, abstract and why it was picked. A paper also joins the project's literature, marked as a *seed*. |
| **Read with Pippin** | Keeps it (in the project it fits, or where you saved it) and opens it in Pippin's [reader](../concepts/literature.md#reading-with-pippin): a paper from its PDF, a post, blog, thread or README from a snapshot of its page. Not for videos. |
| **PDF / Open thread / Read** | The item itself. |
| **Not interested** | Hides it, and similar items rank lower from now on (*Undo* brings it back). |

Filter by kind (papers, posts, blogs, discussions) and by project. Items that fit
no project well are folded away: **Show N less relevant items**.

A **discussion** gathers what people say about a paper in one of your projects
(or a new paper of someone you follow): Bluesky posts that link it, Hacker
News threads, its OpenReview forum. **Show the points** lists the main points
raised, with links to each place.

The right column lists the people you follow, with what is new from each since
you last opened their page; your daily digest; and how many items each source
brought today (and any source that did not answer, with why).

## Sources and people

**Sources and people** (top of the Library) is where you choose what the feed
follows.

**People.** Type a name, a profile link (OpenAlex, Semantic Scholar, ORCID, a homepage), an ORCID, or a handle
(`@someone.bsky.social`, `@user@mastodon.social`, `x.com/someone`). When several authors share the name, you pick the
right one by affiliation, number of papers, latest paper and co-authors. Pippin then looks for their places, and each
step shows on the page as it happens (the web search, who it found, each place as it is checked):

- with a model that can search the web, one search lists their homepage, blog, X, Bluesky, Mastodon and OpenAlex
  profile; a blog must have a feed, an account must exist and the OpenAlex author must carry their name, or the place
  is left out. Without one, the links in their ORCID record and on their homepage are followed;
- once the person is certain: their arXiv papers by name, their GitHub and their own YouTube channel (new
  repositories and videos come into the feed as *Code* and *Videos*), and talks or videos with them or about their
  papers, each checked to exist;
- links to open, not followed: their homepage, Google Scholar, Semantic Scholar, LinkedIn, ORCID.

What it is sure of is ticked; what it only found by name is offered unticked, with a note. For each person you choose
what to follow: papers, posts, blog, code, videos and discussions about their work; click a place to stop or start
following it there. A person's page lists what came from them by kind.

**Topics.** arXiv categories (`cs.AI`, `cs.LG`) and keywords. Every new paper in your categories is read (up to
300 a refresh), and the briefing, not the query, picks among them; keywords also find papers outside the categories
and tell the briefing what you care about. *Topics from my projects' objectives* adds search phrases and categories
worked out from each project's objective. The examples and the scripted demo project are not counted as your work.

**Sources.** By default three: **arXiv** (your categories), **HF Daily Papers** (the community's picks, with their
upvotes) and **X**. *More sources…* turns on the others: scholarly indexes (OpenAlex, Semantic Scholar), Bluesky and
Mastodon, blogs and newsletters (paste a page's address: its RSS or Atom feed is found), GitHub, YouTube, and
discussions around the papers in your projects.

**X** is read by the browser extension as you, every hour while the browser is open and signed in to X: the pages
under *Sources* (by default your timeline, `x.com/home`; a list or a person's page works too). The posts come into
the feed, and a post that links a paper (arXiv, HF papers, alphaXiv, through X's short links) adds to that paper who
posted it and what they said. Without the extension, an X API key (a bearer token; X's API is paid) reads the X
accounts of the people you follow. LinkedIn cannot be followed (it has no open API).

**One paper, one item.** A paper seen in several places is one item with all its signals: its arXiv category, its HF
upvotes, and everyone who posted about it on X.

## The briefing

Twice a day by default (08:30 and 20:30; clear the second time for once a day), Pippin writes a short briefing of
what came in since the last one. A model (`anthropic/claude-sonnet-5.5`, a few cents a briefing; `digest.model` to
change it) reads the new items against what you work on and care about (your topics and keywords, your projects,
what you saved in the notebook and the feed, what you marked *Not interested*) and picks what is worth your time
today: what bears on your work, and what your field is clearly talking about (many HF upvotes, several people on X)
even when it is not about your projects. There is no fixed number: a quiet day gives a short briefing, or none.

The picks come under a few headings the model chooses (a project, *Trending*, *What people are discussing*), after
one sentence on what the day comes down to. Each item has a plain sentence on what it does, one on why it is worth
your time, where it was seen (`arXiv cs.AI · HF 34 upvotes · on X: …`) and its links: the paper, its HF page, the
posts on X, and the item in the console. *Written in* chooses English or 中文. Without a model, the most talked-about
items are listed as they are.

It goes to the chat apps you linked (Feishu, WeCom, Slack, WhatsApp; see
[Talking to Pippin in a chat app](notifications.md#talking-to-pippin-in-a-chat-app)),
to one of your notification channels (set them up under **Settings → Notifications**), or both. **Send one now**
shows what it looks like; *The last one* shows the last briefing in the console.

The console link of an item keeps it in the project it fits best (or your own library) and opens it in the reader,
where Pippin has started reading it with you. In a chat app, reply **read 2** (「读 2」) to do the same from there:
Pippin keeps item 2, starts writing its notes in the margin, and answers with the link.

## How it works

Your sources are asked every three hours (and when you press **Look again
now**); the feed shows what came in over the last three days. Each new item is
first scored by the words it shares with each project's objective, the papers in
its literature and what its runs found; the most promising are then ranked by a
model (`models.brainstorm`, by default the front desk's), with your *Not
interested* items as examples of what you do not want. At most 12 ranking calls
a day per person; without a key, the word scores alone rank the feed.

A confidential project is described to the model only if the model's provider
keeps no data; otherwise its items are ranked by the word scores.

| Source | Needs |
|---|---|
| arXiv, HF Daily Papers, Bluesky, Mastodon, RSS, Hacker News, OpenReview | nothing |
| X, read as you | the Pippin browser extension, signed in to X in that browser |
| OpenAlex | works without a key, but set `OPENALEX_API_KEY` (free): the keyless daily budget is shared by everyone on your IP address |
| Semantic Scholar | works without a key, rate-limited; `SEMANTIC_SCHOLAR_API_KEY` to use it fully |
| X, without the extension | an X API key, per person (paid) |

Everything in the feed is your own: nobody else sees what you follow, save or
hide. What you save to a project or bring to a shared brainstorm is seen there
like anything else added to it.
