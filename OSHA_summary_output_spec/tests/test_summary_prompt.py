"""Provider-neutral prompt construction tests."""

from osha_summary.prompt import build_summary_prompt


def test_summary_prompt_contains_only_approved_context() -> None:
    prompt = build_summary_prompt(
        "What is the bull case for LITE?",
        "Sales improved. [Source: report.md — Sales]",
        1200,
    )

    assert "What is the bull case for LITE?" in prompt
    assert "Sales improved. [Source: report.md — Sales]" in prompt
    assert "SSUMMARY TOKEN BUDGET:\n1200" in prompt
    assert "Do not resolve paths" in prompt
    assert "Do not decompose the main query" in prompt
    assert "Return only the draft summary content" in prompt
    assert "Do not emit OSHA_ID" in prompt
    assert "OSHA_SUMMARY_TYPE" in prompt
    assert "Do not emit" in prompt and "Bibliography" in prompt
    assert "[Source: filename — section]" in prompt
    assert "Never abbreviate" in prompt


def test_summary_prompt_marks_missing_answer_sheet() -> None:
    prompt = build_summary_prompt(
        "What is the bull case for LITE?",
        None,
        1200,
    )

    assert "[ANSWER SHEET UNAVAILABLE]" in prompt
