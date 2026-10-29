# Brainstorm

A brainstorm is where you think out loud with four agents before you commit to
an experiment:

| Agent | What it does | What it records on the board |
|---|---|---|
| **Skeptic** | finds the weakest assumption: a confounder, an alternative explanation, a measurement problem | a question on the idea; evidence against it from what is already known |
| **Methods** | turns an idea into a test: data, comparison, the result that would refute it | a next step that builds on the idea; questions the design must answer |
| **Librarian** | searches the literature (the real indexes, see [Literature](literature.md)) and the project's runs | papers and results as evidence for or against the idea |
| **Mentor** | helps you think a direction through by asking, so your research question stays yours | nothing on the board; your research plan, beside the conversation |

Open it with the lightbulb in the activity bar, or with **Brainstorm** in a
project's tab bar.

## Spaces and conversations

Every project has a brainstorm space, and you have one of your own for ideas
that belong to no project yet. A space holds conversations (brainstorms) and the
ideas on its board. A new brainstorm is **private**: only you see it, and project
owners and admins cannot open it either. **Share…** shares the whole
conversation with the project, or only the ideas you pick, together with the
evidence and questions on them.

## The page

| Column | What is there |
|---|---|
| **Left** | The space; the project's members and its **Share** button; your private brainstorms and **+ New brainstorm**; the brainstorms shared with the project (a live round table shows a green dot); the project's **Library** by source (all papers, starred, your uploads, from the feed, found by the agents, from Zotero); **Searches by the agents**, each with how many of its papers are in the literature, and *Papers they find* for this project; **Zotero**; and **Agents**, how active they are. |
| **Middle** | The conversation. In a shared brainstorm, the people **here now**. The knowledge bar counts what the agents can use (ideas, open questions, papers, runs, other brainstorms; in a shared brainstorm only what is shared, and "private material left out"). |
| **Right** | **Ideas**: *What's new* from the agents, the ideas as arguments, and material not filed under an idea yet. **Papers**: the project's literature, as on its Literature page: filter, search, add (search, upload PDFs, BibTeX or RIS, pasted DOIs or arXiv IDs, Zotero), star, remove, send to Zotero, open the PDF, export .bib; and each agent search's results. |

## Talking to the agents

Write as you think. Name an agent (`@Skeptic`, `@Methods`, `@Librarian`) to
ask it, or pick an idea on the right (**Discuss**) and use the buttons under the
conversation: *What could be wrong?*, *Find evidence*, *How to test it?* While
an agent works you see its steps, for example "Searching the literature: …" and
"Reading [Behrens12]". Each answer ends with what it added to the board.

**How active** (left column) sets whether the agents answer messages that name
nobody:

| Setting | Behaviour |
|---|---|
| Only when asked | Agents answer only when you name them or press a button. |
| Moderately (default) | A cheap check on each message decides whether one agent should answer. Thinking out loud gets no reply. |
| Very | Up to two agents answer most messages. |

**Your memory.** In your own brainstorms the Librarian and the Skeptic can also search your
[memory](memory.md) (what you saved from papers, posts and your notes) and record a claim from it as evidence,
marked *From your memory*; the Skeptic looks there first for what contradicts an idea. A shared brainstorm never
uses it. From the Memory page, **Bring to a brainstorm** does the same by hand.

**Deep search** asks the Librarian for a thorough search: many searches from
different angles (the claim, its mechanism, competing explanations, methods,
the closest prior work), then an overview of the most relevant papers and of
what the literature does not answer yet.

**+** in the message box attaches a paper from the Library or a file from the
project's files: the agent reads it (its abstract and your highlights, or the
file's text) with your message.

The agents' model calls use your API key (the server's key in single-user mode).
The model is `models.brainstorm` (default: the front desk's).

## Finding your question with the Mentor

When you are drawn to something but do not know yet what exactly to ask ("I want to work on memory for
robots, not sure what"), the Mentor answers; name it (`@Mentor`) or press **Help me think it through** on an
idea to bring it in yourself. It follows the Socratic Mentor of
[Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) (ARS), whose files Pippin
reads at run time (`aurora/third_party/ars/`, CC BY-NC 4.0):

- **It asks, it does not propose.** Each reply takes up what you said and asks one or two questions: what you
  really want to know, how you would answer it, what evidence would change your mind, where a reviewer would
  push, why it would matter. You move through these in your own time; nothing in the conversation says which
  stage you are in.
- **You predict first.** Before it shows you something, it asks what you expect; then it brings a counterpoint
  to test that, from a real paper it searched for (cited by its key, never from memory). When you sound
  certain ("obviously", "肯定"), it offers the opposite view. It surfaces one adjacent angle you have not
  raised and leaves it to you to take in or set aside.
- **The Skeptic joins as devil's advocate** when you have settled how you would answer the question, and when
  you examine your own conclusions.
- **Your question, written down.** When you can say your question in a sentence, it is written down from the
  conversation (an RQ Summary): the question in your words, what you said about it on each FINER criterion
  (feasible, interesting, novel, ethical, relevant), what is in and out, what is still open. Reply with what to
  change, or confirm it: it goes on the board as your idea. A separate, collapsed FINER scoring is marked as
  the AI's view.
- **Your plan, beside the conversation.** The **Plan** tab keeps what you work out yourself, by section
  (research question, method, evidence, limitations, contribution), each point with the words of yours it
  came from. Edit, move or remove any point.
- **Only when you ask** ("just give me some candidate questions") does it propose questions itself. It says so
  first; the candidates come with their FINER scores, and one you take is marked as the AI's suggestion, on
  the board and in your plan. Say you want to be guided again and it goes back to asking.
- **It ends when you say so**, or when your thinking has converged (in an exploratory conversation, only when
  you say so). The Librarian then offers to map the field around your question; the map starts from it and
  does not ask for the scope again. When a map of the field is ready, the Mentor asks which of its unsolved
  problems bothers you most, and why.

While it guides a conversation, your messages go to it; name another agent to ask that one instead. Say
"just tell me" (or "skip the predictions") whenever you would rather it worked differently.

## Drafts, your own versions, and whose words an idea is

Every way of working on an idea leaves room for you, after the modes compared by
Maier, Schneider and Feuerriegel (CHI 2026): a model rewriting an idea once gave
good ideas that converged and that people did not feel were theirs; a model that
gives a piece of knowledge or an analogy and asks, while the person writes, gave
good ideas that stayed diverse and theirs; going round in rounds avoided the
convergence.

- **Drafts.** Ask any agent to write or rewrite your idea ("write a few versions
  of it"): two or three drafts come into the conversation. Edit any of them in
  place, **Take this**, or **Another round from this**: the next round shows your
  version first and new drafts built on it. Take a draft as it is and the Mentor
  asks what in it is most yours.
- **A stimulus, then your versions.** When you are stuck, the Mentor may give one
  piece of knowledge or an analogy from another field, from a paper it searched
  for, and asks how it could inspire a new version; then it asks you to write two
  or three versions yourself. They are listed exactly as you wrote them; pick one
  to go on with. (This goes beyond the ARS protocol, which gives no content; it is
  Pippin's extension.)
- **Versions of an idea.** Whatever you take, pick or edit becomes the idea's next
  version, marked as yours, an AI draft you edited (with how much of the text is
  your own words), or the AI's draft as it was. The history is on the idea and
  in the **Plan** tab.
- **One step leads to the next.** Each step ends with one sentence offering the
  natural next one (a stimulus, writing versions, picking one, going deeper,
  letting the Skeptic attack it, mapping the field, the round table); answer in
  words, or say what you would rather do.

For evaluation, Pippin keeps what happens (stimuli, drafts, what was taken and how
much was changed) without showing it. `GET /api/brainstorm/spaces/{space}/ideation-metrics`
gives the diversity of a space's ideas (each idea's distance to the centroid of the
others, embedded with `openai/text-embedding-3-large`, or word overlap when
embeddings are unavailable) and how much of them is the researcher's;
`POST /api/brainstorm/chats/{chat_id}/depth` codes how far the researcher
elaborated in each answer, on the paper's five levels (a few cents).

## What the agents do on their own

| How active | On their own |
|---|---|
| Only when asked | nothing |
| Moderately | A few times a day, in the spaces you used this week, they bring things up in *What's new*: a recent paper that bears on an idea (one click adds it as evidence), two ideas that could be one, a question that has waited days for an answer. A few model calls a day. |
| Very | The same, every hour, and they keep working: the Librarian looks for evidence on ideas that have none, the Skeptic questions ideas nobody has questioned. Their work goes into each idea's discussion, and *What's new* says so. At most 40 model calls a day. |

Dismissing a suggestion (×) tells the agents to bring fewer things like it.

## Reading a paper with the agents

**Read the PDF** on a paper (in the Papers tab, on the Literature page, or from a
citation) opens it in the reader: the PDF on the left, the notes on it beside it.

- **Select words** in the PDF to highlight them, write a note on them, or ask
  the Librarian, the Skeptic or Methods about them. The agent answers in the
  note's conversation, from the passage, its page and your idea. Reply there, and
  name another agent (`@Methods`) to bring it in.
- **The agents read along.** Opened from a brainstorm idea, the paper is read for
  that idea: each agent marks up to two passages its role cares about (the
  Librarian: what the paper shows for or against the idea; the Skeptic: what the
  idea has to survive; Methods: what a test of it could reuse), with a comment
  and how it bears on the idea. These are **suggestions**, drawn with a dashed
  line, until you **Keep** or **Dismiss** them. Turn *Agents mark passages on
  their own* off and they answer only when you ask. In a conversation, an agent
  can also read a paper's PDF and mark a passage for you.
- Each author has its colour: you yellow, the Librarian blue, the Skeptic orange,
  Methods purple (Zotero's colours). Filter the notes by author; clicking a note
  shows its place in the paper, and clicking a highlight opens its note.
- **Private by default.** Your notes, and the agents' notes in your reading, are
  yours. In a project where you may comment, **Share with the project** shows a
  note to its members, who can reply.
- **Zotero.** When your Zotero has the paper and its PDF, and your key can
  write, kept notes become annotations on that PDF in Zotero, and the ones you
  make in Zotero come back. See [Zotero](../guide/zotero.md#notes-on-pdfs).

Where the PDF comes from: the project's papers (uploaded, or copied from Zotero),
your Library, another project of yours, or, for an open paper (arXiv), fetched
once and kept. Without one, the reader shows the abstract.

## An idea up close

**Start a discussion** (or *See the discussion*) on an idea opens it on the
right. It holds the idea's own discussion with people and agents; your messages
there can name agents too. It also lists the idea's links (*supports*,
*challenges*, *builds on*, *asks about*, *related*), and you can add or remove
links there. From there you can turn it into a proposal or delete it. An idea in
your own space can **move to a project**, or start a new project. Its questions
and evidence go along, and its papers join the project's literature.

**+ Add** on the Ideas tab adds an idea, a question, a note, or a paper from the
Library. A paper you bring lands under *Not attached to an idea* until you file
it as supporting or challenging one.

## The board: ideas as arguments

Each idea on the right reads as an argument:

- **its status**: *Waiting on 2 questions*, *Ready to test* or *Early: no
  evidence yet*;
- **one sentence on where it stands**, which an agent updates as evidence comes in;
- **a tally**: so many for, so many against, open questions, next steps;
- **details**, in three parts: *why we think so* (papers with their key, run
  results), *what could be wrong* (evidence against, and the questions, each of
  which you can answer), and *next steps* (designs that build on the idea).

The main button on each idea is the next thing to do: answer its open question,
ask the Librarian for evidence, or turn it into a proposal.

## Privacy in shared brainstorms

In a private brainstorm the agents use your private ideas and everything shared
with the project. In a shared brainstorm they use only what is shared. If one of
your private ideas bears on the question, the agent does not use it. It asks
you first, in a message only you see, with **Share the idea** and **Keep it
private** buttons.

Who may do what follows the project's sharing:

- Seeing a project's space needs view rights; writing in it needs comment
  rights.
- Only the author edits or deletes a card, or shares a private one.

## From an idea to an experiment

**Turn into a proposal** (on an idea, or under the conversation) puts an
experiment proposal into the project's chat. It contains:

- the idea, and the sentence on where it stands;
- the evidence for and against it, with the papers' keys;
- the questions the plan must answer (and their answers);
- the next steps from the brainstorm.

Choose how the run starts:

- **Start from this idea**: no ideation and no screening; planning works on the
  idea directly.
- **Brainstorm with me**: the round table meets live in the brainstorm (below).
- **Round table, agents only**: the seats develop the idea (its current
  version) into its strongest form instead of proposing other directions.

You also choose, for this run, which of the papers the agents find go into the
project's literature. Check the proposal in the project's chat and start it
there. An idea in your own space goes to a project you choose.

## Brainstorm with me: a run's round table, live

Choose **Brainstorm with me** when you turn an idea into a proposal, or on any
proposal card. The run's round table then meets in the project's brainstorm
space, in a brainstorm shared with the project (a green dot while it meets):

- the chair's steps and every seat's turn appear as they are written, with the
  seat's model;
- a presentation becomes an idea (*live · from the round table*). A seat's
  question or concern becomes a question on it, and a suggestion becomes an idea
  that builds on it. The chosen direction is marked;
- **you can speak**. Whatever you write is heard before the next turn ("the
  chair gives you the floor"). The seats are told a researcher is in the room
  and answer your points;
- **it starts from your idea**, and you can **rewrite the idea itself** while
  the meeting runs (open it on the right and edit it): each new version is heard
  before the next turn;
- **the chair's decision is a draft.** It comes back as drafts: the chosen
  direction, the others presented, and your own idea. Edit any of them in place,
  then go on with it, or have the seats **go another round** from it. The run
  waits for you (`human.autonomy: supervised`; in autonomous mode it goes on with
  the chair's choice). A direction you take or change becomes your idea's next
  version, and the run pursues it without screening it again (tick *screening*
  in the approval bar if you want it scored).

Speaking in a run's meeting steers the run, so it needs the right to start runs
in the project. When the meeting ends, its ideas and questions stay on the board.

In the configuration this is `ideation.live: true` (web console only).
