"""CLI tests with the live summary function replaced by a fake."""

from pathlib import Path

from osha_summary import cli


def test_cli_reads_answer_sheet_and_emits_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    answer_sheet_path = tmp_path / "answer.md"
    answer_sheet_path.write_text("Sales improved.", encoding="utf-8")
    received: dict[str, object] = {}

    def fake_summarize(
        query: str,
        answer_sheet: str | None,
        budget: int,
    ) -> str:
        received.update(
            query=query,
            answer_sheet=answer_sheet,
            budget=budget,
        )
        return "summary output"

    monkeypatch.setattr(cli, "summarize_answer_sheet", fake_summarize)

    exit_code = cli.main(
        [
            "--query",
            "What is the bull case for LITE?",
            "--answer-sheet",
            str(answer_sheet_path),
            "--summary-budget",
            "1200",
        ]
    )

    assert exit_code == 0
    assert received == {
        "query": "What is the bull case for LITE?",
        "answer_sheet": "Sales improved.",
        "budget": 1200,
    }
    assert capsys.readouterr().out == "summary output\n"


def test_cli_passes_missing_answer_sheet_as_none(monkeypatch) -> None:
    received: dict[str, object] = {}

    def fake_summarize(
        query: str,
        answer_sheet: str | None,
        budget: int,
    ) -> str:
        received.update(
            query=query,
            answer_sheet=answer_sheet,
            budget=budget,
        )
        return "insufficient output"

    monkeypatch.setattr(cli, "summarize_answer_sheet", fake_summarize)

    exit_code = cli.main(
        [
            "--query",
            "What is the bull case for LITE?",
            "--answer-sheet",
            "/path/that/does/not/exist.md",
            "--summary-budget",
            "1200",
        ]
    )

    assert exit_code == 0
    assert received["answer_sheet"] is None
