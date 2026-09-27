"""The question-set runner, with an invented question set."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from services.evals.questions import Case, judge, parse, report, run, summary

FILLED = """# Question Set

<!-- instructions the user sees; ignored -->

## Example (made up, delete when you start)

- **Question:** Does the lake trip conflict with anything?
- **Category:** cross-source
- **Expected answer:** Yes

---

## Q1

- **Question:** When is the plumber coming?
- **Category:** lookup
- **Expected answer:** Tuesday at 9am.
- **Where the answer lives:** Email from the plumbing company, Sep 21.
- **What a good answer looks like:** Gives the day and time,
  and cites the email.

## Q2

- **Question:** What is my sister's phone number?
- **Category:** no-answer
- **Expected answer:** Should say it doesn't know.
- **Where the answer lives:** Nowhere.
- **What a good answer looks like:**

## Q3

- **Question:**
- **Category:**
"""


def test_parses_filled_questions_and_skips_example_and_blanks():
    cases = parse(FILLED)
    assert [c.number for c in cases] == ["1", "2"]
    q1 = cases[0]
    assert q1.question == "When is the plumber coming?" and q1.category == "lookup"
    assert q1.where == "Email from the plumbing company, Sep 21."
    assert q1.good == "Gives the day and time, and cites the email."  # continued line joined
    assert cases[1].good == ""


def test_the_blank_template_has_no_questions():
    template = Path("docs/templates/questions.md").read_text(encoding="utf-8")
    assert parse(template) == []


class Judge:
    def __init__(self, reply):
        self.reply = reply
        self.users = []

    def complete(self, system, user, schema=None, temperature=None):
        self.users.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_judge_sees_everything_and_returns_the_grade():
    case = parse(FILLED)[0]
    chat = Judge(json.dumps({"verdict": "correct", "source_found": "yes", "note": "Right day and time."}))
    assert judge(chat, case, "Tuesday at 9am [1].", "Emails:\n  [1] 2026-09-21  Rick's Plumbing") == (
        "correct", "yes", "Right day and time.")
    sent = chat.users[0]
    assert "Expected answer: Tuesday at 9am." in sent and "Rick's Plumbing" in sent and "Tuesday at 9am [1]." in sent


def test_judge_failure_is_reported_not_fatal():
    case = parse(FILLED)[0]
    assert judge(Judge(RuntimeError("down")), case, "a", "s")[0] == "not graded"
    assert judge(Judge('{"verdict": "great"}'), case, "a", "s")[:2] == ("not graded", "unclear")


def test_run_continues_after_a_failed_question_and_reports():
    cases = parse(FILLED)

    def ask(question):
        if "sister" in question:
            raise RuntimeError("chat API down")
        return "Tuesday at 9am [1].", "Emails:\n  [1] plumber"

    chat = Judge(json.dumps({"verdict": "correct", "source_found": "yes", "note": "ok"}))
    results = run(cases, ask, chat, log=lambda _: None)
    assert [r.verdict for r in results] == ["correct", "error"]
    assert summary(results) == "1 correct, 0 partial, 0 wrong, 1 errors (of 2); source found 1/1"
    text = report(results, datetime(2026, 9, 28, 8, 0), "qwen3:8b")
    assert text.startswith("# Question set run, 2026-09-28 08:00")
    assert "## Q1: correct (source found: yes)" in text and "## Q2: error" in text
    assert "chat API down" in text


def test_ungraded_run_just_collects_answers():
    results = run(parse(FILLED)[:1], lambda q: ("an answer", "sources"), chat=None, log=lambda _: None)
    assert results[0].verdict == "not graded" and results[0].answer == "an answer"


# --- the real local model as grader, on invented answers ----------------------------


def ollama_available():
    import httpx

    from services.common import settings

    try:
        httpx.get(f"{settings.OLLAMA_BASE_URL}/api/version", timeout=2).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not ollama_available(), reason="Ollama not running")
def test_real_model_grades_sensibly():
    from services.common import settings
    from services.common.ollama import OllamaChat

    model = settings.models()
    chat = OllamaChat(settings.OLLAMA_BASE_URL, model["chat"], num_ctx=model["chat_context_tokens"])
    plumber, sister = parse(FILLED)
    sources = "Searched: email\nEmails:\n  [1] 2026-09-21  Rick's Plumbing <office@ricksplumbing.example>  Re: Leaking faucet"
    assert judge(chat, plumber, "The plumber is coming Tuesday at 9am [1].", sources)[0] == "correct"
    assert judge(chat, plumber, "The plumber is coming Thursday at 2pm [1].", sources)[0] == "wrong"
    assert judge(chat, sister, "I don't know; none of your emails mention her phone number.", "Searched: email")[0] == "correct"
    assert judge(chat, sister, "Her number is 555-0100.", "Searched: email")[0] == "wrong"
