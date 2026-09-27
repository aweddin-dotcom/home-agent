"""The chat assistant: turns a conversation into a streamed answer.

A follow-up ("what about Friday the 2nd?") is first rewritten into a
standalone question using the conversation, then answered like a single
question: route, look up calendar and email, answer from what was found.
"""

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

        earlier = messages[: len(messages) - 1 - messages[::-1].index(user_messages[-1])]
        standalone = self.condense(question, history_text(earlier))
        today = self.today or datetime.now(self.tz).date()
        mail = self.open_mail() if self.open_mail else None
        context = gather(
            standalone, self.embedder, self.index, self.chat, self.top_k, self.load_events(), today, self.tz, mail
        )
        yield from self.chat.stream(*answer_prompt(standalone, context, today))

        footer = format_sources(context, self.tz)
        if standalone != question:
            footer = f"Understood as: {standalone}\n{footer}"
        yield SOURCES_MARKER + footer
