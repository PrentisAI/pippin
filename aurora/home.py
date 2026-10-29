"""The start page's conversation, once a model key is there: what the person means by what they typed.

The start page only finds out what someone wants and takes them to the right place (a project's front
desk, a brainstorm with the Mentor, the demo); it does no research itself. One short call to a small
model reads the message and says which of these it is, with a sentence to answer it.
"""

from __future__ import annotations

import json
import re
from typing import Any

INTENTS = ("question", "unsure", "how", "other")

SYSTEM = """You are the start page of Pippin, a console where AI agents carry out research for a person,
with auditors that check the work and the person in charge. The start page only finds out what the
person wants and sends them on; it does not do research or answer research questions itself.

Read the person's last message (the conversation so far is given for context) and decide what it is:
- "question": something they want to find out or do, even rough: a research question, a topic with a
  direction, a paper to reproduce, a dataset to analyse.
- "unsure": they do not know yet what to research, or say they have no idea.
- "how": they ask what Pippin is, what it can do, or how it works.
- "other": anything else (a greeting, small talk, something unclear).

Then write one or two short, warm sentences to them, in the language they wrote in:
- question: restate what they want in a few words, to show you understood ("Got it: whether ...").
- unsure: only say, in one sentence, that this is a fine place to start (the page then offers the ways on).
- how: answer in plain words (agents propose directions, plan, run code in a sandbox, analyse and write
  a report in which every number links to the script that computed it; you approve the plan and the
  big decisions).
- other: answer briefly and ask what they would like to find out.
No lists, no markdown, no questions back except for "other".

Answer with JSON only: {"intent": "question" | "unsure" | "how" | "other", "reply": "..."}"""


def parse(raw: str) -> dict[str, str] | None:
    """The model's answer as {"intent", "reply"}, or None when it is not one."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in INTENTS:
        return None
    return {"intent": intent, "reply": str(data.get("reply", "")).strip()[:600]}


async def _ask_model(messages: list[dict[str, str]], *, api_key: str, base_url: str, model: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    try:
        r = await client.chat.completions.create(model=model, max_tokens=200, temperature=0.2, messages=messages)
        return r.choices[0].message.content or ""
    finally:
        await client.close()


async def reply(text: str, history: list[dict[str, Any]], *, api_key: str, base_url: str, model: str) -> dict[str, str] | None:
    """What the person means and a sentence back; None when the model cannot be asked or answers oddly
    (the page then goes on as if it were a research question)."""
    convo = "\n".join(f"{'Person' if h.get('from') == 'user' else 'Pippin'}: {str(h.get('text', ''))[:400]}"
                      for h in history[-8:])
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Conversation so far:\n{convo}\n\nTheir last message:\n{text[:1000]}"}]
    try:
        return parse(await _ask_model(messages, api_key=api_key, base_url=base_url, model=model))
    except Exception:
        return None
