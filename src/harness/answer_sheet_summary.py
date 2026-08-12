"""Bridge the research synthesizer to the standalone answer-sheet summary."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.config import Config


SUMMARY_BUDGET = 4000
SUMMARY_ATTEMPTS = 2

SummaryFunction = Callable[[str, str, int], str]
FallbackSummaryFunction = Callable[[str, str, str], str]
UnavailableSummaryFunction = Callable[[str], str]


class SummaryGenerationError(RuntimeError):
    """Raised after both summary-generation attempts fail."""


def generate_answer_sheet_summary(
    main_user_query: str,
    answer_sheet_markdown: str,
    config: Config,
    summarize_fn: SummaryFunction | None = None,
    fallback_fn: FallbackSummaryFunction | None = None,
    unavailable_fn: UnavailableSummaryFunction | None = None,
) -> str:
    """Generate a stored-context summary, retrying once on failure.

    ``summarize_fn`` is injectable for upstream unit tests. Production calls
    load the bundled summary project, with the legacy sibling workspace path
    retained as a development fallback.
    """
    production_mode = summarize_fn is None
    if production_mode:
        _configure_summary_model(config)
        summarize_fn = _load_summary_function()
    assert summarize_fn is not None

    # The public answer-sheet boundary now carries local citation labels and
    # a bibliography. The standalone summary package still consumes the
    # original upstream source-reference form, so expand it at this seam.
    from src.harness.answer_sheet_contract import expand_answer_sheet_for_summary

    summary_input = expand_answer_sheet_for_summary(answer_sheet_markdown)

    errors: list[Exception] = []
    last_uncited_draft: str | None = None
    for _attempt in range(SUMMARY_ATTEMPTS):
        try:
            return summarize_fn(
                main_user_query,
                summary_input,
                SUMMARY_BUDGET,
            )
        except Exception as exc:  # retry policy is owned by this boundary
            errors.append(exc)
            draft = getattr(exc, "draft", None)
            if isinstance(draft, str):
                last_uncited_draft = draft

    if (
        last_uncited_draft is not None
        and all(hasattr(error, "draft") for error in errors)
    ):
        if fallback_fn is None and production_mode:
            fallback_fn = _load_fallback_function()
        if fallback_fn is not None:
            return fallback_fn(
                main_user_query,
                summary_input,
                last_uncited_draft,
            )

    if all(_is_empty_summary_error(error) for error in errors):
        if unavailable_fn is None and production_mode:
            unavailable_fn = _load_unavailable_function()
        if unavailable_fn is not None:
            return unavailable_fn(main_user_query)

    last_error = errors[-1]
    raise SummaryGenerationError(
        f"summary generation failed after {SUMMARY_ATTEMPTS} attempts: "
        f"{last_error}"
    ) from last_error


def _configure_summary_model(config: Config) -> None:
    """Use the upstream research model unless a summary model is supplied."""
    if os.getenv("DEEPSEEK_SUMMARY_MODEL"):
        return

    profile = config.get_llm_profile(config.research_synthesizer_profile)
    model = profile.get("model")
    if not model:
        raise ValueError(
            "upstream research synthesizer profile has no model for summary"
        )
    os.environ["DEEPSEEK_SUMMARY_MODEL"] = str(model)


def _load_summary_function() -> SummaryFunction:
    """Import the bundled summary package, falling back to the old sibling."""
    summary_src = _summary_src_path()
    summary_src_text = str(summary_src)
    if summary_src_text not in sys.path:
        sys.path.insert(0, summary_src_text)

    from osha_summary.summary import summarize_answer_sheet

    return summarize_answer_sheet


def _load_fallback_function() -> FallbackSummaryFunction:
    """Load the deterministic fallback from the bundled summary package."""
    summary_src = _summary_src_path()
    summary_src_text = str(summary_src)
    if summary_src_text not in sys.path:
        sys.path.insert(0, summary_src_text)

    from osha_summary.summary import fallback_uncited_summary

    return fallback_uncited_summary


def _load_unavailable_function() -> UnavailableSummaryFunction:
    """Load the deterministic empty-provider fallback."""
    summary_src = _summary_src_path()
    summary_src_text = str(summary_src)
    if summary_src_text not in sys.path:
        sys.path.insert(0, summary_src_text)

    from osha_summary.summary import summary_unavailable

    return summary_unavailable


def _summary_src_path() -> Path:
    """Return the bundled summary source, with a legacy local fallback."""
    repository_root = Path(__file__).resolve().parents[2]
    bundled = repository_root / "OSHA_summary_output_spec" / "src"
    if bundled.is_dir():
        return bundled

    return repository_root.parent / "OSHA_summary_output_spec" / "src"


def _is_empty_summary_error(error: Exception) -> bool:
    """Identify the provider's explicit empty-content failure."""
    return str(error) in {
        "DeepSeek returned empty summary content",
        "summary model returned empty draft",
    }
