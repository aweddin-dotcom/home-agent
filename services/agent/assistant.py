"""The chat assistant: turns a conversation into a streamed answer.

A follow-up ("what about Friday the 2nd?") is first rewritten into a
standalone question using the conversation, then answered like a single
question: route, look up calendar and email, answer from what was found.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from services.retrieval.ask import answer_prompt, format_sources, gather

SOURCES_MARKER = "\n\n---\n"
HISTORY_TURNS = 6
HISTORY_CHARS = 600

CONDENSE_PROMPT = """Rewrite the user's latest message as a standalone question that makes
sense without the conversation. Fill in what it refers to (dates, people,
events) from the conversation. If it already stands alone, return it
unchanged. Reply with only the question."""


def message_text(message):
    """Text of an OpenAI-format message; content may be a string or a list of parts."""
    content = message.get("content") or ""
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return content


def is_task_request(text):
    """Open WebUI's own background requests (chat titles, tags, follow-up
    suggestions) start with '### Task:'. They skip email and calendar."""
    return text.lstrip().startswith("### Task:")


DIGEST_REQUEST = re.compile(r"\b(digest|morning (summary|briefing|brief)|daily (summary|briefing|brief))\b", re.I)


def is_digest_request(text):
    return bool(DIGEST_REQUEST.search(text))


def digest_reply(store, tz):
    digest = store.latest_digest() if store else None
    if not digest:
        return ("There's no digest yet. It's built each morning by the sync worker "
                "(config/digest.yaml); to build one now, run: python -m services.digest.build")
    built = datetime.fromisoformat(digest["created_at"]).astimezone(tz)
    clock = f"{built:%I:%M%p}".lstrip("0").lower()
    return f"{digest['body']}\n\n_Built {built:%a %b} {built.day}, {clock}_"


def history_text(messages):
    lines = []
    for message in messages[-HISTORY_TURNS:]:
        if message.get("role") not in ("user", "assistant"):
            continue
        text = message_text(message).split(SOURCES_MARKER)[0].strip()[:HISTORY_CHARS]
        lines.append(f"{message['role'].capitalize()}: {text}")
    return "\n".join(lines)


@dataclass
class Assistant:
    chat: object
    embedder: object
    index: object
    load_events: object  # callable returning the current calendar events
    tz: object
    top_k: int = 5
    today: object = None  # fixed date for tests; None means the real today
    open_mail: object = None  # callable returning a read-only Store, for folder names and listings
    status_notes: object = None  # callable returning sync-health lines for the footer
    portfolio: object = None  # Portfolio Analyzer client, for investment questions
    load_profile: object = None  # callable returning the user's about-me notes
    load_discgolf: object = None  # callable returning the user's UDisc scorecards, or None
    pong: object = None  # Browser Pong client, for league standings

    def condense(self, question, history):
        if not history:
            return question
        user = f"Conversation:\n{history}\n\nLatest message: {question}"
        try:
            rewritten = self.chat.complete(CONDENSE_PROMPT, user, temperature=0).strip().strip('"')
        except Exception:
            return question
        return rewritten or question

    def respond(self, messages):
        """Yield the reply to an OpenAI-format conversation, piece by piece."""
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            yield "Ask me something about your email or calendar."
            return
        question = message_text(user_messages[-1]).strip()
        if is_task_request(question):
            yield from self.chat.stream_messages(
                [{"role": m["role"], "content": message_text(m)} for m in messages]
            )
            return

        if is_digest_request(question):
            yield digest_reply(self.open_mail() if self.open_mail else None, self.tz)
            return

        earlier = messages[: len(messages) - 1 - messages[::-1].index(user_messages[-1])]
        standalone = self.condense(question, history_text(earlier))
        today = self.today or datetime.now(self.tz).date()
        mail = self.open_mail() if self.open_mail else None
        context = gather(
            standalone, self.embedder, self.index, self.chat, self.top_k, self.load_events(), today, self.tz, mail,
            self.portfolio, self.load_profile, self.load_discgolf, self.pong,
        )
        yield from self.chat.stream(*answer_prompt(standalone, context, today))

        footer = format_sources(context, self.tz)
        if standalone != question:
            footer = f"Understood as: {standalone}\n{footer}"
        if self.status_notes:
            try:
                notes = self.status_notes()
            except Exception:  # noqa: BLE001 - a status problem must not hide the answer
                notes = []
            if notes:
                footer += "\n" + "\n".join(notes)
        yield SOURCES_MARKER + footer
