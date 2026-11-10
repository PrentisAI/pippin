# Notifications

A long experiment does not need you most of the time, and then suddenly it does:
a gate waits for your decision, the run finishes, or its budget is nearly gone.
Notifications tell you on your phone or in your team chat, with a link straight
to the experiment, so you need not keep the console open.

## Setting up a channel

In the console, open **Settings → Notifications → Add a channel**, pick the kind,
paste its webhook URL or key, tick what it should get, and press **Send a test**.

| Kind | What you paste | Where to get it |
|---|---|---|
| WeCom group robot (企业微信) | the robot's webhook URL | in a WeCom group: *Group settings → Group robots → Add*; copy the webhook address |
| Server酱 (personal WeChat) | your SendKey | sign in at [sct.ftqq.com](https://sct.ftqq.com) with WeChat and copy the SendKey; messages arrive through its WeChat service account |
| Feishu / Lark bot | the bot's webhook URL | in a group: *Settings → Bots → Add bot → Custom bot* |
| Slack | an incoming-webhook URL | a Slack app with *Incoming Webhooks* |
| Webhook | any URL | your own service; see the payload below |

A channel can be switched off without removing it. Several channels can receive
the same event (say, your WeChat and the team's WeCom group).

Or in a config file (`aurora run`, or the server's base config):

```yaml
notifications:
  console_url: http://localhost:8100      # how you open the console; links point here
  channels:
    - type: wecom
      name: lab group
      target: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
    - type: serverchan
      target: SCT...
      events: [decision_needed, run_failed]
```

## What is sent

| Event | When | On by default |
|---|---|---|
| `decision_needed` | a gate waits for you (an auditor escalated, screening keeps rejecting, pre-flight found problems) | yes |
| `run_finished` | an experiment completed | yes |
| `run_failed` | it failed, or stopped on its own (a budget ran out, a gate timed out on *stop*); not when you stopped it | yes |
| `budget_warning` | it has used `budget_warning_at` (80 %) of its cost or time budget, once | yes |
| `estimate_exceeded` | it cost or took more than the upper end of its [estimate](results.md#estimate-before-a-run), then twice that | yes |
| `agent_reply` | the research agent answered an instruction you sent | no |
| `skill_suggestion` | a skill was proposed and waits for your review | no |
| `compute_job` | one of its [GPU jobs](cluster-jobs.md) ended | no |
| `progress` | how a running experiment is getting on: each new phase, and every few hours | no (always to your chat apps) |

### How a running experiment is getting on

While an experiment runs, Pippin tells you how it is getting on without being
asked: a word when it enters a new phase (planning, research, analysis,
writing), and every three hours a summary: the phase, the jobs done of the plan
and which are running, what happened since the last summary (jobs done or
failed, audits, questions it asked you, cluster jobs) and what the agents have
cost. No summary is sent when nothing happened. Set the hours (0: never) and the
phase word under **Settings → Notifications**. Progress goes to your linked
chat apps, where you can reply to ask about the run or give it an instruction,
and to any channel that takes `progress`; quiet hours hold it like the rest.

Each message names the experiment, says what happened in a few lines and links
to the experiment in the console. The same message is not sent twice within a
minute.

## Answering a decision from the message

A `decision_needed` message also carries two links, **Approve and continue** and
**Stop the experiment** (buttons in Feishu; links in WeCom, Slack and Server酱;
an `actions` list for a webhook). Opening one shows a small page with the
decision and its details and a single button that confirms it; only pressing
that button acts. So a chat app that opens links to make a preview cannot
approve anything.

- A link stands for the person the message was sent to (whoever started the
  experiment) and still needs their right to answer the run's decisions: in a
  project where another person answers them, the link says so and does nothing.
- It is signed with a key only the server has (`AURORA_HOME/action.key`) and
  expires after a day. Once the decision is made (from either place), the link
  says *already decided*.
- To answer otherwise (with an instruction, say), open the experiment.
- The links need the console's address (`console_url`, below), and the console
  must be reachable from the phone that opens them. Turn them off with
  *Approve/Stop links* in the settings (`notifications.action_links: false`).

## Talking to Pippin in a chat app

The channels above only send: they cannot hear a reply. To talk to Pippin from a chat app, connect the team's own
app once under **Settings → Chat apps**: a Feishu/Lark custom app with the bot ability, a WeCom self-built app, a
Slack app with a bot, or a WhatsApp Business number. A team admin enters its IDs and secrets there and points the
app's message callback at the address shown (`/im/feishu`, `/im/wecom`, `/im/slack` or `/im/whatsapp` on this
console, which must be reachable from the internet). Each request is checked by the app's token or signature.

| App | What to set up |
|---|---|
| Feishu / Lark | A custom app with the bot ability; permissions `im:message` and `im:resource` (voice); subscribe to `im.message.receive_v1` |
| WeCom | A self-built app with *Receive messages* on (its Token and EncodingAESKey); this server's IP among its trusted IPs |
| Slack | An app with bot scopes `chat:write`, `im:history`, `im:write`, `app_mentions:read`, `files:read`, `files:write`; events `message.im` and `app_mention`; messages allowed in App Home. Mention the bot in a channel and it answers in the thread |
| WhatsApp | A Meta Business app with the WhatsApp product; a permanent system-user token; the webhook with your verify token, subscribed to `messages` |

WhatsApp lets a business write to someone only within 24 hours of their last message (later, only with a template
Meta approved). So a notification reaches WhatsApp when the person wrote to Pippin that day; otherwise it is logged
and their other channels still get it.

Each person then links their chat account once: the same page gives them a code, and they send `绑定 <code>` (or
`bind <code>`) to the bot. From then on what they write to the bot goes to Pippin as them (their projects, their
rights), in the same conversation as the console's ⌘K, the orb and the browser's side panel; the answer comes back
in the chat. Pippin can read a run's report and what it found, and say where a project stands. When they ask it to
act on a run (answer its decision, tell it something, stop it), it proposes the action and nothing happens until
they reply `确认` / `yes` (`取消` / `no` drops it). Their decision and run notifications come to the same chat, so
they can answer them there.

**Voice.** Send a voice message instead of typing. Pippin hears it with this console's speech recognition
(a model that listens, told your projects' names and terms, or the whisper.cpp command in `AURORA_WHISPER`; the same
as the orb's, see [Voice](voice.md#recognising-speech)), shows what it heard
above the answer, and answers in writing and, briefly, as a voice message in the orb's voice (the cloud voice, or the
model that talks). Feishu and WhatsApp get a voice message, Slack an audio file, WeCom a voice message when the
server's ffmpeg can write AMR. Without speech recognition, the bot says so and asks you to type; without a voice,
the answer is in writing only. WeCom's own recognition is used when the app has it on.

## Quiet hours

Set **Quiet hours** (from, until; the server's clock, may span midnight) and
messages that come in between are held. When the quiet hours end, you get one
message listing what was held, each with its link. Kinds ticked under
*Decisions still come through* (`quiet_except`, e.g. `decision_needed`) are
sent at once even then. Each person has their own quiet hours.

```yaml
notifications:
  quiet_start: "23:00"
  quiet_end: "08:00"
  quiet_except: [decision_needed]
```

**Links.** They use *Console address in links* (`notifications.console_url`), or
else the address the console serves on. If you reach the console through an SSH
tunnel, put the address you type into your browser (e.g. `http://localhost:8100`).

Settings changed in the console reach experiments that are already running.

## Webhook payload

The generic webhook receives a JSON POST:

```json
{
  "event": "decision_needed",
  "title": "Decision needed: Citi Bike usage patterns",
  "text": "Pre-flight found problems ...",
  "url": "http://localhost:8100/campaigns/p_20261019_16dc60/experiments/20261019-025018_citi-bike",
  "time": 1792400000.0,
  "project_id": "p_20261019_16dc60",
  "project": "Citi Bike",
  "experiment_id": "20261019-025018_citi-bike",
  "actions": [
    {"label": "Approve and continue", "url": "http://localhost:8100/a/eyJj..."},
    {"label": "Stop the experiment", "url": "http://localhost:8100/a/eyJj..."}
  ]
}
```

`actions` is empty except for decisions. A held-messages digest arrives with
`"event": "held"`.

The *Send a test* button sends `"event": "test"`.

## Secrets

Webhook URLs and keys let anyone post to your chat, so treat them as secrets.
The console keeps them in `AURORA_HOME/console_settings.yaml` and shows only a
shortened form; they are not written into the experiment's directory (whose
`config.yaml` the agents can read). An experiment resumed with `aurora resume`
therefore sends notifications only if its config file or the server's settings
have channels.
