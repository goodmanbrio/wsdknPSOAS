"""Public answer-sheet summary boundary."""

from __future__ import annotations

from typing import Protocol

from .citations import assign_local_citation_labels, extract_upstream_references
from .deepseek import DeepSeekSummaryClient
from .prompt import build_summary_prompt
from .renderer import (
    render_draft_summary_block,
    render_summary_block,
    render_insufficient_evidence_block,
    render_invalid_identifier_block,
    render_summary_unavailable_block,
)
from .validator import validate_summary_output


class SummaryCompletionClient(Protocol):
    """Provider-independent interface used by the summary-stage logic."""

    def complete(self, prompt: str, max_tokens: int) -> str:
        ...


class SummaryDraftCitationError(ValueError):
    """Raised when a source-bearing input produces an entirely uncited draft."""

    def __init__(self, draft: str) -> None:
        super().__init__(
            "summary draft omitted all upstream citations despite cited input"
        )
        self.draft = draft


def summarize_answer_sheet_with_client(
    main_user_query: str,
    answer_sheet_markdown: str | None,
    s_summary: int,
    client: SummaryCompletionClient,
) -> str:
    """Run the summary stage against an injected completion client."""
    if main_user_query == "":
        return render_invalid_identifier_block()
    if answer_sheet_markdown is None or not answer_sheet_markdown.strip():
        return render_insufficient_evidence_block(main_user_query)

    prompt = build_summary_prompt(
        main_user_query,
        answer_sheet_markdown,
        s_summary,
    )
    summary_draft = client.complete(prompt, max_tokens=s_summary)
    if (
        extract_upstream_references(answer_sheet_markdown)
        and not extract_upstream_references(summary_draft)
    ):
        raise SummaryDraftCitationError(summary_draft)

    summary_block = render_draft_summary_block(
        main_user_query,
        summary_draft,
    )
    validate_summary_output(summary_block)
    return summary_block


def fallback_uncited_summary(
    main_user_query: str,
    answer_sheet_markdown: str,
    summary_draft: str,
) -> str:
    """Store an uncited retry result with all answer-sheet sources attached.

    This is the explicit second-stage fallback: the model draft is retained,
    but every exact upstream source reference from the answer sheet is listed
    in the bibliography because claim-level mapping is unavailable.
    """
    upstream_references = extract_upstream_references(answer_sheet_markdown)
    assignments = assign_local_citation_labels(upstream_references)
    fallback_block = render_summary_block(
        main_user_query,
        _fallback_draft_text(summary_draft),
        assignments.bibliography_entries,
    )
    validate_summary_output(fallback_block)
    return fallback_block


def summary_unavailable(main_user_query: str) -> str:
    """Return a valid stored artifact when the provider emitted no text."""
    block = render_summary_unavailable_block(main_user_query)
    validate_summary_output(block)
    return block


def _fallback_draft_text(summary_draft: str) -> str:
    """Remove harmless model wrappers before the bibliography-only fallback."""
    draft = summary_draft.strip()
    lines = draft.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```markdown", "```md"}
        and lines[-1].strip() == "```"
    ):
        lines = lines[1:-1]
    if lines and lines[0].strip().lower() == "## answer summary":
        lines = lines[1:]
    for index, line in enumerate(lines):
        if line.strip().lower() == "## bibliography":
            lines = lines[:index]
            break
    return "\n".join(lines).strip()


def summarize_answer_sheet(
    main_user_query: str,
    answer_sheet_markdown: str | None,
    s_summary: int,
) -> str:
    """Summarize one synthesized answer sheet.

    Parameters
    ----------
    main_user_query:
        The exact original user query. Used for OSHA_ID.

    answer_sheet_markdown:
        One Markdown answer sheet from the upstream synthesizer.
        None means the answer sheet is unavailable.

    s_summary:
        The assigned maximum token budget for the output block.

    Returns
    -------
    str
        Exactly one Markdown summary block.

    The live path reads DeepSeek configuration from the environment. Tests and
    embedding callers can use summarize_answer_sheet_with_client with an
    injected client instead.
    """
    if main_user_query == "":
        return render_invalid_identifier_block()
    if answer_sheet_markdown is None or not answer_sheet_markdown.strip():
        return render_insufficient_evidence_block(main_user_query)

    client = DeepSeekSummaryClient.from_environment()
    return summarize_answer_sheet_with_client(
        main_user_query,
        answer_sheet_markdown,
        s_summary,
        client,
    )
