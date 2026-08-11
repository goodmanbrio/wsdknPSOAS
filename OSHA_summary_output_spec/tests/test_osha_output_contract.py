"""Deterministic contract tests U0-U22."""

from pathlib import Path
import inspect

import pytest

from osha_summary.citations import (
    BIBLIOGRAPHY_CHILD_INDENT,
    assign_local_citation_labels,
)
from osha_summary.contract import build_metadata_header
from osha_summary.identifier import (
    decode_query_identifier,
    encode_query_identifier,
)
from osha_summary.renderer import (
    SummaryLine,
    render_insufficient_evidence_block,
    render_invalid_identifier_block,
    render_summary_block,
    render_summary_unavailable_block,
    render_summary_with_budget,
)
from osha_summary.summary import summarize_answer_sheet
from osha_summary.validator import (
    validate_bibliography_order,
    validate_citation_integrity,
    validate_derived_lineage,
    validate_no_stale_branch_markers,
    validate_section_order,
)


@pytest.mark.parametrize(
    ("query", "expected_identifier"),
    [
        ("abc-._~", "abc-._~"),
        ("A query? 100%", "A%20query%3F%20100%25"),
        (
            "Café / 東京",
            "Caf%C3%A9%20%2F%20%E6%9D%B1%E4%BA%AC",
        ),
        (
            "What were the company's high-layer substrate drill overseas sales and AI-dedicated capex?",
            "What%20were%20the%20company%27s%20high-layer%20substrate%20drill%20overseas%20sales%20and%20AI-dedicated%20capex%3F",
        ),
    ],
)
def test_u0_main_query_identifier_encoding_and_round_trip(
    query: str,
    expected_identifier: str,
) -> None:
    encoded = encode_query_identifier(query)

    assert encoded == expected_identifier
    assert decode_query_identifier(encoded) == query


def test_u1_metadata_header_uses_encoded_query_and_approved_order() -> None:
    query = "What is the bull case for LITE?"

    header = build_metadata_header(query)

    assert header.splitlines() == [
        "[[OSHA_ID:What%20is%20the%20bull%20case%20for%20LITE%3F]]",
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]",
    ]


VALID_SUMMARY_BLOCK = """[[OSHA_ID:What%20is%20the%20bull%20case%20for%20LITE%3F]]
[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]

## Answer Summary

- Operating profit improved. [1.1]
- Capital expenditure increased. [2.1]
- Operating margin was 12.5%. [3]

## Bibliography

[1] [Source: report.md]
    [1.1] Sales

[2] [Source: capex.md]
    [2.1] Capital Expenditure

[3] [Derived: FROM 1.1,2.1 | FORMULA: operating_profit/net_sales]
"""


def test_u2_summary_sections_appear_in_approved_order() -> None:
    validate_section_order(VALID_SUMMARY_BLOCK)

    invalid_order = VALID_SUMMARY_BLOCK.replace(
        "## Answer Summary\n\n- Operating profit improved. [1.1]\n- Capital expenditure increased. [2.1]\n- Operating margin was 12.5%. [3]\n\n## Bibliography",
        "## Bibliography\n\n## Answer Summary\n\n- Operating profit improved. [1.1]\n- Capital expenditure increased. [2.1]\n- Operating margin was 12.5%. [3]",
    )
    with pytest.raises(ValueError, match="must precede"):
        validate_section_order(invalid_order)


def test_u3_every_inline_citation_resolves_to_bibliography() -> None:
    validate_citation_integrity(VALID_SUMMARY_BLOCK)

    invalid_block = VALID_SUMMARY_BLOCK.replace(
        "Operating profit improved. [1.1]",
        "Operating profit improved. [4.1]",
    )
    with pytest.raises(ValueError, match="no bibliography entry: 4.1"):
        validate_citation_integrity(invalid_block)


def test_u4_derived_citations_use_prior_labels_and_formula() -> None:
    validate_derived_lineage(VALID_SUMMARY_BLOCK)

    invalid_block = VALID_SUMMARY_BLOCK.replace(
        "[3] [Derived: FROM 1.1,2.1 | FORMULA: operating_profit/net_sales]",
        "[3] [Derived: FROM 1.1,4.1 | FORMULA: operating_profit/net_sales]",
    )
    with pytest.raises(ValueError, match="unknown input"):
        validate_derived_lineage(invalid_block)


def test_u5_fixture_preserves_qualitative_and_quantitative_content() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "answer_sheet.md"
    fixture = fixture_path.read_text(encoding="utf-8")

    output = render_summary_block(
        "What were the company's high-layer substrate drill overseas sales and AI-dedicated capex?",
        (
            "- Overseas sales were 61% in H1 FY2026. [1.1]\n"
            "- AI-dedicated capex totaled ¥3.2 billion in H1 2026. [2.1]"
        ),
        [
            "[1] [Source: source.md]\n    [1.1] Sales",
            "[2] [Source: capex.md]\n    [2.1] Capital Expenditure",
        ],
    )

    assert "61%" in fixture
    assert "¥3.2 billion" in fixture
    assert "61%" in output
    assert "¥3.2 billion" in output


def test_u6_missing_answer_sheet_produces_insufficient_evidence_block() -> None:
    output = render_insufficient_evidence_block("What is the bull case for LITE?")

    assert output == (
        "[[OSHA_ID:What%20is%20the%20bull%20case%20for%20LITE%3F]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n"
        "[[OSHA_STATUS:INSUFFICIENT_EVIDENCE]]\n\n"
        "## Bibliography\n\n"
        "[Source: unavailable]"
    )


def test_u6a_empty_provider_produces_summary_unavailable_block() -> None:
    output = render_summary_unavailable_block("What is the bull case for LITE?")

    assert output == (
        "[[OSHA_ID:What%20is%20the%20bull%20case%20for%20LITE%3F]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n"
        "[[OSHA_STATUS:SUMMARY_UNAVAILABLE]]\n\n"
        "## Bibliography\n\n"
        "[Source: unavailable]"
    )


def test_u7_budget_drops_lower_priority_detail_first() -> None:
    query = "What is the bull case for LITE?"
    bibliography = ["[1] [Source: report.md]\n    [1.1] Sales"]
    lines = [
        SummaryLine("- Direct answer. [1.1]", priority=0, mandatory=True),
        SummaryLine("- Important supporting fact. [1.1]", priority=1),
        SummaryLine("- Lower-priority detail. [1.1]", priority=2),
    ]

    token_counter = lambda text: len(text.split())
    without_detail = render_summary_with_budget(
        query,
        lines[:2],
        bibliography,
        s_summary=10_000,
        token_counter=token_counter,
    )
    budget = token_counter(without_detail)

    output = render_summary_with_budget(
        query,
        lines,
        bibliography,
        s_summary=budget,
        token_counter=token_counter,
    )

    assert "Direct answer" in output
    assert "Important supporting fact" in output
    assert "Lower-priority detail" not in output
    assert "## Bibliography" in output


def test_u8_source_group_keeps_full_filename_and_formats_children() -> None:
    source_reference = "[1] [Source: source.md | Sales]\n    [1.1] Appendix"

    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales improved. [1.1]",
        [source_reference],
    )

    assert "[1] [Source: source.md | Sales]" in output
    assert f"\n\n{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Appendix" in output


def test_u8a_renderer_separates_bibliography_entries_with_blank_lines() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales improved. [1.1]",
        [
            "[1] [Source: report.md]\n"
            "    [1.1] Sales\n"
            "    [1.2] Capital Expenditure",
            "[2] [Source: capex.md]\n    [2.1] Guidance",
        ],
    )

    assert (
        "[1] [Source: report.md]\n\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Sales\n\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Capital Expenditure\n\n"
        f"[2] [Source: capex.md]\n\n{BIBLIOGRAPHY_CHILD_INDENT}[2.1] Guidance"
    ) in output


def test_u8b_renderer_expands_flattened_source_group() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales improved. [1.1]",
        [
            "[1] [Source: report.md] [1.1] Sales [1.2] Capital Expenditure",
        ],
    )

    assert (
        "[1] [Source: report.md]\n\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Sales\n\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Capital Expenditure"
    ) in output


def test_u9_renderer_emits_one_answer_sheet_summary_block() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )

    assert output.count("[[OSHA_ID:") == 1
    assert output.count("[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]") == 1
    assert output.count("## Answer Summary") == 1
    assert output.count("## Bibliography") == 1


def test_u10_answer_sheet_fixture_matches_approved_output_shape() -> None:
    output = render_summary_block(
        "What were the company's high-layer substrate drill overseas sales and AI-dedicated capex?",
        (
            "- The company’s high-layer substrate drill business represented "
                "61% of total overseas sales in H1 FY2026. [1.1]\n"
            "- Capital expenditure for new AI-dedicated drill production lines "
                "totaled ¥3.2 billion in H1 2026. [2.1]"
        ),
        [
            "[1] [Source: source.md]\n    [1.1] Sales",
            "[2] [Source: capex.md]\n    [2.1] Capital Expenditure",
        ],
    )

    assert output == (
        "[[OSHA_ID:What%20were%20the%20company%27s%20high-layer%20substrate%20drill%20overseas%20sales%20and%20AI-dedicated%20capex%3F]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n\n"
        "## Answer Summary\n\n"
        "- The company’s high-layer substrate drill business represented 61% of total overseas sales in H1 FY2026. [1.1]\n"
        "- Capital expenditure for new AI-dedicated drill production lines totaled ¥3.2 billion in H1 2026. [2.1]\n\n"
        "## Bibliography\n\n"
            "[1] [Source: source.md]\n\n"
            f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Sales\n\n"
            "[2] [Source: capex.md]\n\n"
            f"{BIBLIOGRAPHY_CHILD_INDENT}[2.1] Capital Expenditure"
        )


def test_u11_bibliography_places_ordinary_then_derived_entries_in_order() -> None:
    validate_bibliography_order(VALID_SUMMARY_BLOCK)

    invalid_block = VALID_SUMMARY_BLOCK.replace(
        "[1] [Source: report.md]\n"
        "    [1.1] Sales\n\n"
        "[2] [Source: capex.md]\n"
        "    [2.1] Capital Expenditure",
        "[2] [Source: capex.md]\n"
        "    [2.1] Capital Expenditure\n\n"
        "[1] [Source: report.md]\n"
        "    [1.1] Sales",
    )
    with pytest.raises(ValueError, match="ordinary bibliography"):
        validate_bibliography_order(invalid_block)


def test_u12_invalid_identifier_emits_structured_invalid_block() -> None:
    output = render_invalid_identifier_block()

    assert output == (
        "[[OSHA_ID:<INVALID>]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n"
        "[[OSHA_STATUS:INVALID_IDENTIFIER]]\n\n"
        "## Bibliography\n\n"
        "[Source: unavailable]"
    )


def test_u13_multiple_inline_citations_resolve_once_each() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales and capex both support the conclusion. [1.1] [2.1]",
        [
            "[1] [Source: report.md]\n    [1.1] Sales",
            "[2] [Source: capex.md]\n    [2.1] Capital Expenditure",
        ],
    )

    validate_citation_integrity(output)
    assert output.count("[1] [Source: report.md]") == 1
    assert output.count("[2] [Source: capex.md]") == 1


def test_u14_uncited_claim_has_no_invented_local_citation() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Management sounded confident.\n- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )

    validate_citation_integrity(output)
    answer_summary = output.split("## Bibliography", 1)[0]
    assert "- Management sounded confident." in answer_summary
    assert "- Management sounded confident. [" not in answer_summary


def test_u15_output_contains_no_stale_branch_markers() -> None:
    output = render_summary_block(
        "What is the bull case for LITE?",
        "- Sales improved. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Sales"],
    )
    validate_no_stale_branch_markers(output)

    invalid_output = output.replace(
        "- Sales improved. [1.1]",
        "- Sales improved. [1.1] SUBQUERY_ID:1a",
    )
    with pytest.raises(ValueError, match="SUBQUERY_ID"):
        validate_no_stale_branch_markers(invalid_output)


def test_u16_summary_boundary_requires_only_approved_inputs() -> None:
    parameters = inspect.signature(summarize_answer_sheet).parameters

    assert list(parameters) == [
        "main_user_query",
        "answer_sheet_markdown",
        "s_summary",
    ]
    assert "filepath" not in parameters
    assert "raw_chunks" not in parameters
    assert "retrieval_results" not in parameters


def test_u17_uncomputable_derived_result_has_input_citations_only() -> None:
    output = render_summary_block(
        "What is the operating margin?",
        (
            "- Operating margin: unavailable because operating profit could not "
            "be computed. [1.1] [1.2]"
        ),
        [
            "[1] [Source: report.md]\n"
            "    [1.1] Operating Profit\n"
            "    [1.2] Net Sales",
        ],
    )

    validate_citation_integrity(output)
    assert "unavailable" in output
    assert "[1] [Source: report.md]" in output
    assert f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Operating Profit" in output
    assert f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Net Sales" in output
    assert "[Derived:" not in output


def test_u18_missing_derived_input_has_no_derived_bibliography_record() -> None:
    output = render_summary_block(
        "What is the operating margin?",
        "- Operating margin: unavailable because net sales is missing. [1.1]",
        ["[1] [Source: report.md]\n    [1.1] Operating Profit"],
    )

    validate_citation_integrity(output)
    assert "unavailable" in output
    assert "[Derived:" not in output


def test_u19_combined_invalid_identifier_and_evidence_status() -> None:
    output = render_invalid_identifier_block(
        combined_insufficient_evidence=True,
    )

    assert output == (
        "[[OSHA_ID:<INVALID>]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]\n"
        "[[OSHA_STATUS:INVALID_IDENTIFIER+INSUFFICIENT_EVIDENCE]]\n\n"
        "## Bibliography\n\n"
        "[Source: unavailable]"
    )


def test_u20_identifier_round_trip_preserves_exact_query_text() -> None:
    query = "  香港?\\nAI 100% — café\\t "

    encoded = encode_query_identifier(query)

    assert decode_query_identifier(encoded) == query
    assert encoded != query


def test_u21_uncited_derived_input_has_no_local_derived_citation() -> None:
    output = render_summary_block(
        "What is the operating margin?",
        (
            "- Operating margin is 12.5%, calculated using an uncited "
            "net-sales input."
        ),
        ["[1] [Source: report.md]\n    [1.1] Operating Profit"],
    )

    validate_citation_integrity(output)
    answer_summary = output.split("## Bibliography", 1)[0]
    assert "Operating margin is 12.5%" in answer_summary
    assert "Operating margin is 12.5% [" not in answer_summary
    assert "[Derived:" not in output


def test_u22_repeated_upstream_reference_reuses_one_local_label() -> None:
    assignments = assign_local_citation_labels(
        [
            "[Source: report.md — Sales]",
            "[Source: report.md — Sales]",
            "[Source: report.md — Capital Expenditure]",
        ]
    )

    assert assignments.labels == ("1.1", "1.1", "1.2")
    assert assignments.bibliography_entries == (
        "[1] [Source: report.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Sales\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Capital Expenditure",
    )


def test_u23_child_display_removes_repeated_company_prefix() -> None:
    assignments = assign_local_citation_labels(
        [
            "[Source: report.md — Lumentum Holdings Inc. (LITE) > Base Case/PT; "
            "Lumentum Holdings Inc. (LITE) > Bear Case/PT]",
        ]
    )

    assert assignments.bibliography_entries == (
        "[1] [Source: report.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Base Case/PT\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Bear Case/PT",
    )


def test_u24_child_display_removes_company_prefix_after_section_separator() -> None:
    assignments = assign_local_citation_labels(
        [
            "[Source: report.md — Exhibit 29 — Lumentum Holdings Inc. (LITE) > "
            "Competitive Landscape; Lumentum Holdings Inc. (LITE) > Exhibit 12]",
        ]
    )

    assert assignments.bibliography_entries == (
        "[1] [Source: report.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Exhibit 29 — Competitive Landscape\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Exhibit 12",
    )


def test_u25_malformed_compound_group_splits_filenames_and_sections() -> None:
    assignments = assign_local_citation_labels(
        [
            "[Source: first.md — First Section; second.md — Second Section; "
            "first.md — Third Section]",
        ]
    )

    assert assignments.labels == ("1.1", "2.1", "1.2")
    assert assignments.bibliography_entries == (
        "[1] [Source: first.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] First Section\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.2] Third Section",
        "[2] [Source: second.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[2.1] Second Section",
    )


def test_u26_source_only_filenames_in_compound_group_share_following_section() -> None:
    assignments = assign_local_citation_labels(
        ["[Source: first.md; second.md — Various Sections]"],
    )

    assert assignments.labels == ("1.1", "2.1")
    assert assignments.bibliography_entries == (
        "[1] [Source: first.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Various Sections",
        "[2] [Source: second.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[2.1] Various Sections",
    )


def test_u27_html_entity_semicolon_is_not_a_section_delimiter() -> None:
    assignments = assign_local_citation_labels(
        ["[Source: report.md — Hardware &amp; Networking > Valuation]"],
    )

    assert assignments.bibliography_entries == (
        "[1] [Source: report.md]\n"
        f"{BIBLIOGRAPHY_CHILD_INDENT}[1.1] Hardware &amp; Networking > Valuation",
    )
