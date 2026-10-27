# Multi-agent ideation

A single model, sampled repeatedly, tends to propose variations of the same
idea. AURORA draws on several model families instead and lets a chair from
another family choose.

```yaml
models:
  ideation_seats: [anthropic/claude-sonnet-5.5, openai/gpt-5.6-sol, google/gemini-3.1-pro-preview]
  ideation_chair: moonshotai/kimi-k3
ideation:
  mode: roundtable        # or: independent
```

## Two configurations

**`independent`** — every seat prepares one idea on its own. The chair sees the
ideas anonymised and in random order, picks one with a rationale and a reason
for each idea not chosen, and the author of the chosen idea writes it up as the
proposal. Cheap and, in the paper's evaluation, at least as good as the meeting.

**`roundtable`** — a research group meeting:

1. *Preparation* — every seat prepares a presentation of one main idea.
2. *Agenda* — one item per presentation, in random order. The other seats make
   remarks (questions, concerns, suggestions, evidence, build-ons) or pass; the
   presenter responds; seats that raised a hand may follow up; up to
   `ideation.rounds_per_item` exchanges. The chair takes notes on every thread
   and its outcome.
3. *Open discussion* — cross-cutting remarks.
4. *Decision* — the chair decides the direction from the notes and the
   transcript (no voting by the seats); the chosen presenter writes the proposal
   with the changes the meeting accepted.

The full transcript, the chair's notes and the decision are saved with the
proposal in `phases/ideation/round_N/`. In the paper's evaluation the meeting
tended to merge directions rather than choose among them and cost about four
times as much as independent selection.

## Starting from the researcher's idea

`ideation.develop_objective: true` makes the objective the researcher's own idea:
the seats develop it into its strongest version (a sharper question, a testable
hypothesis, a concrete method) and raise what is wrong with it, instead of
proposing other directions. A round table started from a brainstorm idea does
this. With `ideation.live` the meeting runs in the brainstorm, hears each new
version of the idea the researcher writes, and hands its decision back to them as
drafts (see [Brainstorm](brainstorm.md#brainstorm-with-me-a-runs-round-table-live)).

## Customising seats

```yaml
ideation:
  seats:
    - {name: Theorist,     model: anthropic/claude-sonnet-5.5, prompt: "You favour first-principles explanations ..."}
    - {name: Experimentalist, model: openai/gpt-5.6-sol}
    - {name: Skeptic,      model: google/gemini-3.1-pro-preview, prompt: "You look for confounders ..."}
  chair_prompt: ""        # empty: the built-in chair prompt
  web_search: true        # seats search the literature (OpenRouter web plugin); the default
```

Before a seat presents, and before it answers remarks on its idea, it chooses
search queries. AURORA runs them on the scholarly indexes and gives the seat the
papers found. Every reference a seat cites is checked, and one that cannot be
found is marked unsupported for everyone at the meeting (see
[Literature](literature.md)). With `web_search`, seats can also search the web
through OpenRouter's web plugin, for data sources and documentation; this costs a
little extra per call. Turn both off when evaluating ideation on known problems,
so ideas cannot simply be looked up; the paper's evaluation scripts do so.

## Choosing the mode

In the paper's evaluation (20 tasks, a Kimi chair choosing among three ideas),
three different models gave genuinely different ideas: the chair called three
samples of one model essentially the same study in 19 of 20 tasks for Claude
Sonnet and 12 for GPT, but three models' ideas in only 4. The group meeting did
not sharpen them: presenters accepted 90% of their colleagues' remarks, and in 5
of 20 meetings the chair merged directions into one broader plan, at about four
times the cost (US$2.71 against US$0.72 per task). For a focused direction at
low cost, choose *Independent*; the round table is useful when
you want to watch the ideas being argued.

Set `ideation.mode` in the config, or pick *Round table* or *Independent* on
the proposal card in the web console (the front desk asks if you have a
preference). Seats see the campaign's current understanding — the mental model,
findings and open questions — when ideation runs again in a later round.

## Failures

If a seat's call fails it is left out and the meeting continues with the
others (a warning is logged). If every seat fails, or the chair cannot decide,
the campaign stops with an error naming the models — check the model IDs and
your key.
