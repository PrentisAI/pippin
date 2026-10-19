# Working with the human

AURORA keeps you in charge without making you a bottleneck: it asks only when
a decision is yours (human gates), and you can talk to a running campaign at
any time without stopping it (duplex interaction).

## Human gates

A gate opens when

- the **milestone auditor** escalates, or a phase keeps failing its audit;
- the **live auditor** escalates during research;
- **screening** keeps rejecting proposals;
- **pre-flight** still finds problems in the plan after planning revised it;
- the campaign is about to finish and `human.approve_termination` is on.

Each gate shows what happened and asks for one of these answers:

| Answer | Effect |
|---|---|
| **continue** | go on as planned; a message, if you write one, is passed on as a note (at the screening gate: to planning) |
| **instruct** | go on, following your message (it is sent to the agent or the next phase) |
| **propose** | screening gate only: say in your own words which direction to pursue — one of the proposals ("the latest one is fine"), one of them with changes, or your own idea. A model reads your message against the proposals and the meeting's ideas on file, and the stream shows how it was understood. By default planning starts from it at once; you can also have it go through ideation (the seats develop your direction without changing it), screening (it is scored; if rejected, the gate asks you again), or both |
| **stop** | end the campaign (it can be resumed later) |

To start a campaign from your own idea in the first place, skip ideation
(`skip_phases: [ideation]`, or untick it on the proposal card): the objective
is then the direction.

You can answer in the web console's approval bar, by telling the front desk
in the chat, or — for `aurora run` in a terminal — at the prompt. Every gate and
its answer is recorded in `human_gates.jsonl`.

`human.autonomy` controls whether gates wait:

- **supervised** (default) — the gate waits for you, up to
  `human.gate_timeout_minutes`; then `human.on_timeout` (continue or stop) applies.
- **autonomous** — the gate is recorded and the campaign continues at once.

You can switch the mode of a running experiment in the web console; switching
to autonomous releases any waiting gate.

## Duplex interaction and the front desk

While a campaign runs, the **front desk** agent (`models.frontdesk`) is your
conversation partner. Ask it anything — it answers from the live status and by
reading files in the workspace. Give it an instruction ("use 5-fold
cross-validation from now on") and it relays it to the research agent, which
receives it *while it works* and acknowledges it; the reply comes back to you.
Ask it to stop the agent's current action and it interrupts it.

This is possible because every Claude Code session runs with a streaming input:
messages from you, the front desk and the live auditor are injected into the
running session instead of waiting for it to finish.

When a gate opens, the front desk explains it in the chat; if you answer there,
it records your decision.

Turn the front desk off with `human.frontdesk: false`; messages then go straight
to the research agent.

## In the web console, before a campaign

The project chat has its own front desk that helps you turn an idea into an
experiment proposal: it asks what is unclear, drafts an instruction for the
agents and suggests which phases to run. You confirm the proposal card (and can
change the phases) to start the experiment.
