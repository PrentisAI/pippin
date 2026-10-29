# Literature

Agents read and cite papers at three points: the ideation seats while they
prepare and answer remarks, screening when it judges novelty, and the writing
phase. Pippin gives them real papers from the scholarly indexes, and it checks
every reference they cite. A paper the indexes cannot find is never presented as
real.

## Where papers come from

Pippin searches four free indexes and merges what they return, one record per
work:

| Index | What it is good for |
|---|---|
| OpenAlex | broad coverage of journals and conferences, citation counts, abstracts |
| Crossref | the authority on DOIs; journal metadata |
| arXiv | preprints, by arXiv ID |
| Semantic Scholar | citation counts and open-access PDFs; heavily rate-limited without a key |

Set `OPENALEX_API_KEY` (free, from openalex.org): without a key OpenAlex allows
only a small daily budget, shared by everyone on the same IP address, and once it
is used up OpenAlex is reported as not answering. Set `AURORA_CONTACT_EMAIL` so
OpenAlex and Crossref answer faster (their "polite pool"). Set `SEMANTIC_SCHOLAR_API_KEY` to use Semantic Scholar fully;
without it Semantic Scholar is asked only when it is not busy.

Each paper gets a short key from its first author and its year, such as
`Behrens12`. Agents cite papers by these keys, and the console shows them too.

## How a reference is checked

An agent cites a paper either by its key or, for a paper it brings itself, as
`[ref: title; first author; year; DOI or arXiv ID]`. DOIs and arXiv links in its
text are checked as well. Each reference is looked up:

1. by DOI or arXiv ID, if one is given. If the ID exists but names a different
   paper than the cited title, the title is looked up instead;
2. otherwise by title, authors and year, in every index. A title with chemical
   formulas (`Cu/ZnO/Al2O3`) is also asked with the numbers apart, since Crossref
   keeps formulas with their subscripts marked up and otherwise misses them. A
   paper this project already found in the indexes under the same title (same
   first author, year within one) counts as verified too.

A match must agree on the title, the first author (where one is cited) and the
year (within one year, since a preprint and its journal version can differ).
Every index's own record is compared, so one index's mistake (a wrong year, say)
does not hide a paper another index has right.

| Result | Meaning |
|---|---|
| verified | the paper exists. The record from the indexes is used from then on |
| not found | every index answered, and none has the paper. The agent most likely made it up |
| wrong identifier | the DOI or arXiv ID belongs to a different paper |
| not checked yet | no index could be reached. This never counts as verified |

## During a run

**Ideation.** Before a seat presents its idea, and again before it answers
remarks on it, it chooses up to three search queries
(`literature.queries_per_turn`). Pippin runs them, and the seat writes with the
papers found in front of it. After every turn, the turn's references are checked
and a short note is added for everyone at the meeting, the chair included.
The note lists the papers that were verified and the ones that were not found,
and says the claims a flagged paper supports are unsupported. Searching costs
one short model call per turn; the indexes themselves are free.

**Screening.** Screening searches for the closest prior work to the proposal
(and looks at the project's own papers on the subject) and judges novelty
against those papers (`literature.screening_search`). It names the (at most
three) closest papers and says, for each, how the proposal differs. The
experiment's stream shows this as a card: the proposal, its novelty score, the
closest prior work with how it differs, and, while the run waits for the
decision after screening, **Start the experiment** and **Change it**.

**Citations in the console.** Wherever agents write (the round table's turns,
the stream, the front desk's answers), a citation shows as a chip: `Behrens
2012 ✓` for a real paper, `Smith 2024 ?` for one that could not be found. Click
it for the paper (abstract, where it was used, **Open PDF**) or, for one that was
not found, why, the closest real paper and **Use the real one instead**. A turn
that cites a paper that could not be found says so underneath, with **Fix it in
Literature**.

**Seed papers.** When you give the front desk a paper (from the Library's feed,
or **+ → From the Library** in the project chat), it can propose a run that
starts from it: ideation develops ideas from the paper and every phase reads it.
The proposal card shows the seeds; they join the project's literature, marked
*seed*.

**Writing.** The writer gets the verified papers the run cited and cites them by
key. Every reference in the report is then checked again. The report's
reference list and a `references.bib` next to it are built only from verified
papers; references that could not be found are left out.

**The agents' own sessions.** The research agent, and the planning, analysis
and writing sessions, have a command for the literature (inside the container
too):

```text
aurora papers search "<what you look for>" [--limit 8]   OpenAlex, arXiv, Crossref, Semantic Scholar
aurora papers show <Key|DOI|arXiv ID>    the abstract, links, the researcher's highlights and notes
aurora papers pdf <Key> [--text]         its PDF into the workspace's papers/ (and its text)
aurora papers check "<reference>" ...    is it a real paper? (or --file refs.bib)
aurora papers list                       the project's papers and their keys
aurora papers cite <Key> ...             record that the run uses them
aurora papers bib [<Key> ...]            BibTeX for them
```

Their searches are recorded as the research agent's on the Literature page,
`cite` records the use (the papers then join the literature by the project's
rule), and `check` takes a citation however it is written: a DOI, an arXiv ID,
"Behrens et al. (2012). Title. Journal", or Vancouver style "Behrens M, Studt F,
et al. Title. Science. 2012". `pdf` uses the researcher's own copy from Zotero
when the project has one, else the open-access PDF; it is downloaded by Pippin,
so it works when the container has no network.

Searches and checks show up in the experiment's stream, and so do flagged
references.

## The project's Literature page

Open a project and choose **Literature** in its tab bar.

- **Papers**: every paper in the project's literature, what it was checked
  against, where it was used ("Ideation (p02, presentation)", "Screening",
  "Report") and who added it. Click a title for its abstract and links.
- **Not found**: references that could not be verified, each with the closest
  real paper if there is one. You decide what happens to each:
  - **Use the real one instead**: the real paper replaces the reference and
    joins the literature.
  - **Remove it everywhere**: the reference is not used again in the project.
  - **I have this paper**: you vouch for it, for example an internal report.
  - **Check again**: useful if an index was down, or the paper has just appeared.
- **Searches**: every search the agents (or you) ran, with what it found, which
  results were cited and which are already in the literature. Add papers from
  a search one by one, a selection at a time, or all at once.
- **Search and add**: search the indexes, or paste a DOI, an arXiv ID or a link.
- **Upload PDFs**: each PDF is recognised (from its DOI or arXiv ID if it shows
  one, otherwise from its title) and checked, then kept in the project's files
  under `papers/`. A PDF no index has is kept as a paper you vouch for. **Your
  PDFs** lists them.
- **Import BibTeX** (or RIS, from EndNote, Mendeley and others): every entry is
  checked. The entries that are found go in, and the others are listed with the
  reason. You can also paste a list of DOIs or arXiv IDs (in the brainstorm's
  Papers tab).
- **A newer version**: when you cite an arXiv paper at an older version, the
  row says so, with the authors' comment on the new one; **Cite vN** switches
  to it.
- **Export .bib**: the project's papers, for your own manuscript.

## Which papers go in by themselves

`literature.add_found`, in Settings → Literature, sets the default. A project
can set its own default (on its Literature page, or *Papers they find* in its
brainstorm), and each run's proposal card can override it for that run.

| Setting | Papers the agents find |
|---|---|
| `cited` (default) | the papers an agent cites go in; the rest wait in their search for you |
| `all` | every paper a search finds goes in |
| `none` | nothing goes in by itself; you add papers from the searches |

Whatever the setting, a reference that is not verified never goes in.

## Zotero

Connect your Zotero to bring a collection into a project, with its PDFs and your
highlights and notes, and to send the project's papers back to it. See
[Zotero](../guide/zotero.md).

## Your Library

The **Library** (the book icon in the activity bar) holds:

- the papers you saved yourself;
- the literature of every project you can see.

Its **Search** tab searches the indexes. Save a result to your own library, or
add it to a project you can edit. With accounts, the papers of projects you are
not in stay hidden.

## Configuration

```yaml
literature:
  enabled: true            # off: no searching and no checking (mock runs are always off)
  add_found: cited         # all | cited | none
  queries_per_turn: 3      # ideation searches per presentation or answer (0: none)
  results_per_query: 6     # papers shown to the agent per search
  screening_search: true   # screening compares with the closest prior work
```

Turn literature off, together with `ideation.web_search`, when evaluating
ideation on known problems, so that ideas cannot simply be looked up.
