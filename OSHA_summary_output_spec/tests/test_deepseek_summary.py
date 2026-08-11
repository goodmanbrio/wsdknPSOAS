"""Mocked DeepSeek summary-stage tests; no network calls."""

import pytest

from osha_summary.renderer import render_summary_block
from osha_summary.summary import (
    SummaryDraftCitationError,
    summarize_answer_sheet_with_client,
)


class FakeSummaryClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []

    def complete(self, prompt: str, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return self.response


def test_summary_stage_rejects_entirely_uncited_draft_when_input_has_sources() -> None:
    client = FakeSummaryClient("- The outlook is positive.")

    with pytest.raises(SummaryDraftCitationError, match="omitted all"):
        summarize_answer_sheet_with_client(
            "What is the bull case for LITE?",
            "Sales improved. [Source: report.md — Sales]",
            1200,
            client,
        )


def test_summary_stage_renders_draft_with_deterministic_metadata_and_citations() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "- Sales improved. [Source: report.md — Sales]"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    assert output == expected
    assert len(client.calls) == 1
    assert client.calls[0][1] == 1200
    assert "Return only the draft summary content" in client.calls[0][0]
    assert "Do not emit OSHA_ID" in client.calls[0][0]


def test_summary_stage_reuses_one_label_for_repeated_upstream_reference() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "- Sales improved. [Source: report.md — Sales]\n"
        "- Sales also exceeded guidance. [Source: report.md — Sales]"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Sales improved. [1.1]\n- Sales also exceeded guidance. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    assert output == expected


def test_summary_stage_attaches_citations_emitted_on_separate_lines() -> None:
    query = "Compare the optical companies."
    client = FakeSummaryClient(
        "- CIEN has a $215 target. [Source: cien.md — Valuation] based on the report\n"
        "  [Source: cien.md — Price Target]\n\n"
        "- COHR has a $215 target. [Source: cohr.md — Equity Ratings]"
    )

    output = summarize_answer_sheet_with_client(
        query,
        (
            "CIEN has a $215 target. [Source: cien.md — Valuation]\n"
            "COHR has a $215 target. [Source: cohr.md — Equity Ratings]"
        ),
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        (
            "- CIEN has a $215 target. based on the report [1.1] [1.2]\n\n"
            "- COHR has a $215 target. [2.1]"
        ),
        [
            "[1] [Source: cien.md]\n    [1.1] Valuation\n    [1.2] Price Target",
            "[2] [Source: cohr.md]\n    [2.1] Equity Ratings",
        ],
    )
    assert output == expected


def test_summary_stage_removes_space_left_before_punctuation() -> None:
    query = "What was revenue growth?"
    client = FakeSummaryClient(
        "- Revenue reached $2.6 billion [Source: report.md — Estimates]."
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Revenue reached $2.6 billion. [Source: report.md — Estimates]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Revenue reached $2.6 billion. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Estimates"],
    )
    assert output == expected


def test_summary_stage_splits_compound_upstream_source_group() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "- Sales improved. [Source: report.md — Sales; Source: report.md — Guidance]"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales; Source: report.md — Guidance]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Sales improved. [1.1] [1.2]",
        [
            "[1] [Source: report.md]\n"
            "    [1.1] Sales\n"
            "    [1.2] Guidance"
        ],
    )
    assert output == expected


def test_summary_stage_preserves_uncited_claims() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "- Management sounded confident.\n"
        "- Sales improved. [Source: report.md — Sales]"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Management sounded confident.\n- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    assert output == expected


def test_summary_stage_rejects_model_final_metadata() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        render_summary_block(
            query,
            "- Sales improved. [1.1]",
            ["[1] [Source: report.md]\n    [1.1] Sales"],
        )
    )

    with pytest.raises(ValueError, match="draft"):
        summarize_answer_sheet_with_client(
            query,
            "Sales improved. [Source: report.md — Sales]",
            1200,
            client,
        )


def test_summary_stage_does_not_call_client_for_missing_answer_sheet() -> None:
    client = FakeSummaryClient("unused")

    output = summarize_answer_sheet_with_client(
        "What is the bull case for LITE?",
        None,
        1200,
        client,
    )

    assert "[[OSHA_STATUS:INSUFFICIENT_EVIDENCE]]" in output
    assert client.calls == []


def test_summary_stage_strips_draft_fence_and_bibliography_wrapper() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "```markdown\n"
        "## Answer Summary\n\n"
        "- Sales improved. [Source: report.md — Sales]\n\n"
        "## Bibliography\n\n"
        "[1] stale model bibliography\n"
        "```"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    assert output == expected


def test_summary_stage_drops_truncated_upstream_citation_line() -> None:
    query = "What is the bull case for LITE?"
    client = FakeSummaryClient(
        "- Sales improved. [Source: report.md — Sales]\n"
        "- A truncated final claim [Source: report.md — Incomplete"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Sales improved. [Source: report.md — Sales]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    assert output == expected


def test_summary_stage_drops_truncated_trailing_prose_line() -> None:
    query = "What are the revenue growth rates?"
    client = FakeSummaryClient(
        "- LITE revenue grew 60%. [Source: report.md — Growth]\n"
        "- The comparison is limited across the"
    )

    output = summarize_answer_sheet_with_client(
        query,
        "Revenue growth is available. [Source: report.md — Growth]",
        1200,
        client,
    )

    expected = render_summary_block(
        query,
        "- LITE revenue grew 60%. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Growth"],
    )
    assert output == expected


def test_summary_stage_rejects_abbreviated_source_filename() -> None:
    client = FakeSummaryClient(
        "- Sales improved. [Source: report_...md — Sales]"
    )

    with pytest.raises(ValueError, match="filename must be complete"):
        summarize_answer_sheet_with_client(
            "What is the bull case for LITE?",
            "Sales improved. [Source: report.md — Sales]",
            1200,
            client,
        )
