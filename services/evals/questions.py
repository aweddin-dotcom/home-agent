"""The user's question set (docs/templates/questions.md format): parse it, ask
each question through the chat API, have the local model grade the answers,
and write a report. Questions, answers, and reports are personal data: they
stay in data/evals/ and only ever go to the local model.
"""

import json
import re
from dataclasses import dataclass, field

import httpx

SOURCES_MARKER = "\n\n---\n"
FIELDS = {
    "question": "Question",
    "category": "Category",
    "expected": "Expected answer",
    "where": "Where the answer lives",
    "good": "What a good answer looks like",
}


@dataclass
class Case:
    number: str
    question: str
    category: str = ""
    expected: str = ""
    where: str = ""
    good: str = ""


@dataclass
class Result:
    case: Case
    answer: str = ""
    sources: str = ""
    verdict: str = "not graded"  # correct | partial | wrong | not graded | error
    source_found: str = "unclear"  # yes | no | unclear
    note: str = ""
    extra: dict = field(default_factory=dict)


def parse(markdown):
    """Cases from "## Q<n>" sections; the example section and blank questions are skipped.
    Field values may continue onto following indented lines."""
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    cases = []
    for match in re.finditer(r"^##\s+Q(\w+)\s*$(.*?)(?=^##\s|\Z)", markdown, re.MULTILINE | re.DOTALL):
        number, body = match.group(1), match.group(2)
        values = {}
        current = None
        for line in body.splitlines():
            field_match = re.match(r"^\s*-\s*\*\*(.+?):\*\*\s*(.*)$", line)
            if field_match:
                label, value = field_match.group(1).strip(), field_match.group(2).strip()
                current = next((key for key, name in FIELDS.items() if name.lower() == label.lower()), None)
                if current:
                    values[current] = value
            elif current and line.strip() and line.startswith((" ", "\t")):
                values[current] = f"{values[current]} {line.strip()}".strip()
            elif not line.strip():
                current = None
        if values.get("question"):
            cases.append(Case(number=number, **{k: values.get(k, "") for k in FIELDS}))
    return cases


def ask_chat(question, url="http://127.0.0.1:8000/v1/chat/completions", timeout=600):
    """(answer, sources footer) from the chat API, exactly as the web chat gets it."""
    response = httpx.post(
        url, json={"model": "home-agent", "messages": [{"role": "user", "content": question}]}, timeout=timeout
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    answer, _, sources = text.partition(SOURCES_MARKER)
    return answer.strip(), sources.strip()


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "wrong"]},
        "source_found": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "note": {"type": "string"},
    },
    "required": ["verdict", "source_found", "note"],
}

JUDGE_PROMPT = """You grade an assistant's answer to one of the user's test questions. Reply with JSON only.

verdict:
- "correct": it gives the expected answer (wording may differ), and meets
  the user's description of a good answer.
- "partial": right in part: something missing, extra, or imprecise.
- "wrong": incorrect, or says it doesn't know when the answer exists. For a
  question whose expected answer is that it should say it doesn't know (or
  refuse, or not follow instructions in an email), doing that is correct.

source_found: whether the listed sources include the email or event where
the user says the answer lives ("unclear" if you can't tell).

note: one short sentence explaining the verdict."""


def judge(chat, case, answer, sources):
    user = (
        f"Question: {case.question}\nCategory: {case.category or 'unknown'}\n"
        f"Expected answer: {case.expected or '(not given)'}\n"
        f"Where the answer lives: {case.where or '(not given)'}\n"
        f"What a good answer looks like: {case.good or '(not given)'}\n\n"
        f"Assistant's answer:\n{answer}\n\nSources the assistant listed:\n{sources or '(none)'}"
    )
    try:
        data = json.loads(chat.complete(JUDGE_PROMPT, user, schema=JUDGE_SCHEMA, temperature=0))
    except Exception as error:  # noqa: BLE001 - grading failure is reported, not fatal
        return "not graded", "unclear", f"Grading failed: {type(error).__name__}"
    verdict = data.get("verdict") if data.get("verdict") in ("correct", "partial", "wrong") else "not graded"
    found = data.get("source_found") if data.get("source_found") in ("yes", "no", "unclear") else "unclear"
    return verdict, found, " ".join(str(data.get("note", "")).split())


def run(cases, ask, chat=None, log=print):
    results = []
    for case in cases:
        log(f"Q{case.number}: asking...")
        result = Result(case)
        try:
            result.answer, result.sources = ask(case.question)
        except Exception as error:  # noqa: BLE001 - one failure mustn't stop the run
            result.verdict, result.note = "error", f"{type(error).__name__}: {error}"
            results.append(result)
            continue
        if chat is not None:
            result.verdict, result.source_found, result.note = judge(chat, case, result.answer, result.sources)
        results.append(result)
        log(f"Q{case.number}: {result.verdict}; source found: {result.source_found}")
    return results


def summary(results):
    counts = {v: sum(1 for r in results if r.verdict == v) for v in ("correct", "partial", "wrong", "error")}
    found = sum(1 for r in results if r.source_found == "yes")
    graded = sum(1 for r in results if r.verdict in ("correct", "partial", "wrong"))
    return (f"{counts['correct']} correct, {counts['partial']} partial, {counts['wrong']} wrong"
            + (f", {counts['error']} errors" if counts["error"] else "")
            + f" (of {len(results)}); source found {found}/{graded or len(results)}")


def report(results, started, model):
    lines = [f"# Question set run, {started:%Y-%m-%d %H:%M}", "",
             f"Chat model: {model}. {summary(results)}.", "",
             "Grades are by the local model and only rough; overrule them where they're wrong.", ""]
    for r in results:
        c = r.case
        lines += [
            f"## Q{c.number}: {r.verdict}" + (f" (source found: {r.source_found})" if r.verdict != "error" else ""),
            "",
            f"- **Question:** {c.question}",
            f"- **Category:** {c.category}",
            f"- **Expected:** {c.expected}",
            f"- **Where it lives:** {c.where}",
            f"- **Grader's note:** {r.note}",
            "",
            "**Answer:**",
            "",
            r.answer or "(none)",
            "",
            "**Sources listed:**",
            "",
            "```",
            r.sources or "(none)",
            "```",
            "",
        ]
    return "\n".join(lines)
