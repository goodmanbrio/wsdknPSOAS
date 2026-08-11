"""Unit tests for the research-to-summary handoff boundary."""

import pytest

from src.harness.answer_sheet_summary import (
    SUMMARY_BUDGET,
    SummaryGenerationError,
    generate_answer_sheet_summary,
)


def test_summary_handoff_retries_once_and_uses_fixed_budget() -> None:
    calls: list[tuple[str, str, int]] = []

    def summarize(_query: str, answer: str, budget: int) -> str:
        calls.append((_query, answer, budget))
        if len(calls) == 1:
            raise ValueError("transient summary failure")
        return "summary block"

    result = generate_answer_sheet_summary(
        "What happened?",
        "answer sheet",
        config=None,  # type: ignore[arg-type]
        summarize_fn=summarize,
    )

    assert result == "summary block"
    assert len(calls) == 2
    assert all(call[2] == SUMMARY_BUDGET == 4000 for call in calls)


def test_summary_handoff_raises_after_two_failures() -> None:
    calls = 0

    def summarize(_query: str, _answer: str, _budget: int) -> str:
        nonlocal calls
        calls += 1
        raise ValueError("persistent summary failure")

    with pytest.raises(SummaryGenerationError, match="2 attempts"):
        generate_answer_sheet_summary(
            "What happened?",
            "answer sheet",
            config=None,  # type: ignore[arg-type]
            summarize_fn=summarize,
        )

    assert calls == 2


def test_summary_handoff_uses_uncited_source_fallback_after_two_retries() -> None:
    calls = 0
    fallback_calls: list[tuple[str, str, str]] = []

    class UncitedDraftError(ValueError):
        def __init__(self) -> None:
            super().__init__("summary draft omitted all upstream citations")
            self.draft = "- Short uncited summary"

    def summarize(_query: str, _answer: str, _budget: int) -> str:
        nonlocal calls
        calls += 1
        raise UncitedDraftError()

    def fallback(query: str, answer: str, draft: str) -> str:
        fallback_calls.append((query, answer, draft))
        return "fallback summary"

    result = generate_answer_sheet_summary(
        "What happened?",
        "answer sheet",
        config=None,  # type: ignore[arg-type]
        summarize_fn=summarize,
        fallback_fn=fallback,
    )

    assert result == "fallback summary"
    assert calls == 2
    assert fallback_calls == [
        ("What happened?", "answer sheet", "- Short uncited summary")
    ]


def test_summary_handoff_stores_unavailable_block_after_empty_retries() -> None:
    calls = 0
    unavailable_calls: list[str] = []

    def summarize(_query: str, _answer: str, _budget: int) -> str:
        nonlocal calls
        calls += 1
        raise ValueError("DeepSeek returned empty summary content")

    def unavailable(query: str) -> str:
        unavailable_calls.append(query)
        return "unavailable block"

    result = generate_answer_sheet_summary(
        "What happened?",
        "answer sheet",
        config=None,  # type: ignore[arg-type]
        summarize_fn=summarize,
        unavailable_fn=unavailable,
    )

    assert result == "unavailable block"
    assert calls == 2
    assert unavailable_calls == ["What happened?"]
