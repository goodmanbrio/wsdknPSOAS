#!/usr/bin/env python3
"""Run PSOAS once per question in a JSON question file.

Each question gets a fresh process/session so answers do not contaminate one
another. Console output is streamed to the terminal and saved per question.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("questions", data.get("items", data))
    if not isinstance(data, list):
        raise ValueError("Question JSON must be a list or contain a 'questions' list")

    questions = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise ValueError(f"Item {index} does not contain a string 'question' field")
        questions.append(item)
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question_file", type=Path)
    parser.add_argument("--limit", type=int, default=None, help="Run at most N questions")
    parser.add_argument("--start", type=int, default=0, help="Zero-based question offset")
    args = parser.parse_args()

    questions = load_questions(args.question_file)
    selected = questions[args.start :]
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        print("No questions selected.", file=sys.stderr)
        return 2

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "tests" / "eval_runs" / f"question_file_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    total = len(questions)
    for position, item in enumerate(selected, start=args.start + 1):
        question_id = str(item.get("question_id", f"question_{position}"))
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in question_id)
        output_path = output_dir / f"{position:03d}_{safe_id}.txt"
        print(f"\n{'=' * 72}\n[{position}/{total}] {question_id}\n{item['question']}\n{'=' * 72}")

        process = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "src" / "psoas.py"), item["question"]],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        with output_path.open("w", encoding="utf-8") as output:
            for line in process.stdout:
                print(line, end="")
                output.write(line)
        return_code = process.wait()

        summary.append(
            {
                "position": position,
                "question_id": question_id,
                "question_type": item.get("question_type"),
                "question": item["question"],
                "return_code": return_code,
                "output_file": str(output_path),
            }
        )

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    succeeded = sum(row["return_code"] == 0 for row in summary)
    print(f"\nCompleted {len(summary)} question(s): {succeeded} succeeded, {len(summary) - succeeded} failed.")
    print(f"Results: {output_dir}")
    return 0 if succeeded == len(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
