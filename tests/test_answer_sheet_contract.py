from src.harness.answer_sheet_contract import (
    expand_answer_sheet_for_summary,
    format_answer_sheet,
)


def test_answer_sheet_contract_adds_query_id_local_citations_and_bibliography() -> None:
    output = format_answer_sheet(
        "What is LITE's competitive outlook?",
        (
            "## Direct Answer\n\n"
            "LITE has a strong position. [Source: report.md — Executive Summary]\n\n"
            "## Risks\n\n"
            "China competition remains a risk. [Source: report.md — Risks]"
        ),
    )

    assert output.startswith(
        "[[OSHA_ID:What%20is%20LITE%27s%20competitive%20outlook%3F]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n\n"
    )
    assert "LITE has a strong position. [1.1]" in output
    assert "China competition remains a risk. [1.2]" in output
    assert "## Bibliography" in output
    assert "[1] [Source: report.md]" in output
    assert "[1.1] Executive Summary" in output
    assert "[1.2] Risks" in output


def test_answer_sheet_contract_groups_same_file_and_attaches_standalone_citations() -> None:
    output = format_answer_sheet(
        "What happened?",
        (
            "- Sales improved. [Source: report.md — Sales]\n"
            "[Source: report.md — Capital Expenditure]"
        ),
    )

    assert "- Sales improved. [1.1] [1.2]" in output
    assert output.count("[1] [Source: report.md]") == 1
    assert "\n\n    [1.1] Sales" in output
    assert "\n\n    [1.2] Capital Expenditure" in output


def test_labeled_answer_sheet_expands_back_to_upstream_citations_for_summary() -> None:
    labeled = format_answer_sheet(
        "What happened?",
        "Sales improved. [Source: report.md — Sales]",
    )

    expanded = expand_answer_sheet_for_summary(labeled)

    assert "[[OSHA_ID:" not in expanded
    assert "[[OSHA_SUMMARY_TYPE:" not in expanded
    assert "Sales improved. [Source: report.md — Sales]" in expanded
    assert "## Bibliography" not in expanded
