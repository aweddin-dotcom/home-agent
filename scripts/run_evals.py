"""Run your question set against the running assistant and grade the answers.

  python scripts/run_evals.py                  # data/evals/questions.md, graded
  python scripts/run_evals.py --no-grade       # just collect the answers
  python scripts/run_evals.py --only 2 5       # some questions only

Needs the containers and Ollama running. Writes the full report to
data/evals/results/<date-time>.md and adds a line to
data/evals/results/history.csv, so runs (and later, models on the Mac) can
be compared. Everything stays in data/ and only goes to the local model.
Run it yourself, in your own terminal.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.common import settings  # noqa: E402
from services.evals.questions import ask_chat, parse, report, run, summary  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Run and grade the question set.")
    parser.add_argument("--file", default=str(ROOT / "data" / "evals" / "questions.md"))
    parser.add_argument("--no-grade", action="store_true", help="collect answers without grading")
    parser.add_argument("--only", nargs="+", metavar="N", help="question numbers to run")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"No question set at {path}. Copy docs/templates/questions.md there and fill it in.")
    cases = parse(path.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c.number in args.only]
    if not cases:
        sys.exit("No filled-in questions found (sections headed '## Q1', '## Q2', ... with a Question line).")

    chat = None
    model = settings.models()["chat"]
    if not args.no_grade:
        from services.common.ollama import OllamaChat

        chat = OllamaChat(settings.OLLAMA_BASE_URL, model, num_ctx=settings.models()["chat_context_tokens"])

    started = datetime.now()
    print(f"Running {len(cases)} questions...")
    results = run(cases, ask_chat, chat)

    out_dir = path.parent / "results"
    out_dir.mkdir(exist_ok=True)
    report_path = out_dir / f"{started:%Y-%m-%d_%H%M}.md"
    report_path.write_text(report(results, started, model), encoding="utf-8")
    history = out_dir / "history.csv"
    new = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["run", "model", "questions", "correct", "partial", "wrong", "errors", "source_found"])
        writer.writerow([
            f"{started:%Y-%m-%d %H:%M}", model, len(results),
            *(sum(1 for r in results if r.verdict == v) for v in ("correct", "partial", "wrong", "error")),
            sum(1 for r in results if r.source_found == "yes"),
        ])
    print(f"\n{summary(results)}\nReport: {report_path}\nHistory: {history}")


if __name__ == "__main__":
    main()
