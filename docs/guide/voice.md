# Voice

On the Mac, hold **⌥⇧Space**, speak, and let go. The desktop app listens only while the key is held.

## In a brainstorm

The microphone next to the message box records what you say and writes it into the box (click again to stop),
so you can look it over before sending. **Hands-free** (above the box) makes it a spoken conversation: what you
say is sent after a short pause, the agents' answers are read aloud, and it listens again; click it again to stop.
Any answer can be read aloud with the speaker beside the agent's name. Answers are read in the console's cloud
voice when one is set up (below), otherwise in the browser's voice; citation keys and links are left out.

Speech is recognised by the console (see *Recognising speech* below: `AURORA_WHISPER`); the recording is not kept.

## What it understands

What you say goes with what you are doing at that moment, so pointing words mean something:

| You say | It means |
|---|---|
| this sentence, this paragraph | your selection; without one, the text around where you are |
| this figure | the window in front, captured when you spoke (only that window, only then) |
| this paper | what is open in front |
| the one before | what you had open before it |
| the second one | the second item of the digest you just heard |

You can ask a question (about what is in front, or of your memory), compare two things, save what is in front, keep
a thought (*note that…*), put a paper on your reading list, let something sink, ask for a citation for the sentence
you are writing, start a close reading of a paper, hear today's digest, or say *undo*.

## Short aloud, complete on the panel

The answer is spoken in a sentence or two; the full answer, with its sources and their words, appears on the panel
(**You said**). Every point names its source; when nothing you have answers it, Pippin says so.

## Writes, confirmed in words

Pippin's own changes (your reading list, saving, sinking) are made and confirmed: *added to your reading list; say
undo to take it back*. Writes into your tools follow [what you allowed](accounts.md#what-aurora-may-write): *auto*
writes and says so; *ask* asks, and your next words (*yes*, *no*) decide; *off* keeps it in Pippin and says where to
turn it on. A thought said while a paper is open becomes a Zotero note on it only when *Write notes on Zotero items*
is allowed. Changes to a manuscript are never made by voice.

## Recognising speech

Recognition runs on your Mac. Install [whisper.cpp](https://github.com/ggerganov/whisper.cpp) (`brew install
whisper-cpp`) and a model, then give the command in the desktop app's settings, for example:

```
whisper-cli -m ~/models/ggml-large-v3-turbo.bin -nt -l auto -f {audio}
```

`{audio}` is the recording (16 kHz WAV), deleted right after. Without a command, the app sends the recording to the
console, which runs the command in `AURORA_WHISPER` the same way.

## Speaking

Answers use the macOS system voice (choose one in the settings; `say -v '?'` lists them). Pressing the key again
stops it. For long things such as the digest, the console can use a cloud voice instead: set
`AURORA_TTS_BASE_URL`, `AURORA_TTS_API_KEY` (and optionally `AURORA_TTS_MODEL`, `AURORA_TTS_VOICE`) for an
OpenAI-compatible `/audio/speech` service, and tick *Read long things in the console's cloud voice*.

## The digest, to listen to

Settings → **Listen to today's digest**, or say *read me today's digest*. Items are numbered: afterwards you can say
*add the second to my reading list*, *let the third sink*, or *how does the first differ from the one I read last
week?*

## Permissions

macOS asks once for the Microphone; *this figure* also needs Screen Recording; knowing the app in front needs
Accessibility.
