"""Deterministic rendering and budget handling for summary blocks."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Sequence

from .citations import BIBLIOGRAPHY_CHILD_INDENT, rewrite_upstream_citations
from .contract import build_metadata_header


@dataclass(frozen=True, slots=True)
class SummaryLine:
    """One candidate answer-summary line.

    Lower priority numbers are retained ahead of higher priority numbers when
    the summary budget requires optional detail to be removed.
    """

    text: str
    priority: int
    mandatory: bool = False


def render_summary_block(
    main_user_query: str,
    answer_summary: str,
    bibliography_entries: Sequence[str],
) -> str:
    """Render one approved answer-sheet summary block."""
    answer_summary = _attach_standalone_citations(answer_summary)
    lines = [
        build_metadata_header(main_user_query),
        "",
        "## Answer Summary",
        "",
        answer_summary.strip(),
        "",
        "## Bibliography",
        "",
    ]
    for index, bibliography_entry in enumerate(bibliography_entries):
        lines.extend(_format_bibliography_entry(bibliography_entry))
        if index < len(bibliography_entries) - 1:
            lines.append("")
    return "\n".join(lines)


_SOURCE_GROUP_RE = re.compile(
    r"^\[(?P<parent>[1-9][0-9]*)\]\s+\[Source: (?P<filename>[^\]]+)\](?P<children>.*)$",
    re.DOTALL,
)
_SOURCE_CHILD_RE = re.compile(
    r"\[(?P<label>[1-9][0-9]*\.[1-9][0-9]*)\]\s+(?P<section>.*?)(?=\s*\[[1-9][0-9]*\.[1-9][0-9]*\]\s+|\Z)",
    re.DOTALL,
)
_LOCAL_CITATION_RE = re.compile(
    r"\[(?P<label>[1-9][0-9]*(?:\.[1-9][0-9]*)?)\]"
)
_INCOMPLETE_TRAILING_WORDS = frozenset(
    {
        "a", "an", "across", "among", "and", "as", "at", "between",
        "by", "for", "from", "in", "including", "into", "of", "on",
        "or", "than", "that", "the", "through", "to", "under", "via",
        "with", "without",
    }
)


def _attach_standalone_citations(answer_summary: str) -> str:
    """Attach citation-only lines to the preceding claim line.

    Providers sometimes emit a bullet followed by one or more local citation
    labels on separate lines. Keep the citation labels, but place them at the
    end of the preceding non-blank claim so the stored Markdown renders as
    ordinary inline citations rather than a separate visual column.
    """
    output: list[str] = []
    for line in answer_summary.strip().splitlines():
        labels: list[str] = []

        def remove_citation(match: re.Match[str]) -> str:
            label = f"[{match.group('label')}]"
            if label not in labels:
                labels.append(label)
            return ""

        cleaned_line = _LOCAL_CITATION_RE.sub(remove_citation, line)
        cleaned_line = re.sub(r"[ \t]{2,}", " ", cleaned_line).rstrip()
        cleaned_line = re.sub(r"\s+([,.;:!?])", r"\1", cleaned_line)
        previous_index = len(output) - 1
        while previous_index >= 0 and not output[previous_index].strip():
            previous_index -= 1

        if cleaned_line.strip():
            if labels:
                cleaned_line = f"{cleaned_line} {' '.join(labels)}"
            output.append(cleaned_line)
            continue

        if labels and previous_index >= 0:
            _append_missing_citations(output, previous_index, labels)
        elif cleaned_line or labels:
            # Preserve malformed/orphaned content rather than dropping it.
            output.append(cleaned_line or " ".join(labels))

    return "\n".join(output).strip()


def _append_missing_citations(
    lines: list[str],
    line_index: int,
    labels: list[str],
) -> None:
    """Append labels to one claim without duplicating existing labels."""
    existing = set(_LOCAL_CITATION_RE.findall(lines[line_index]))
    missing = [label for label in labels if label[1:-1] not in existing]
    if missing:
        lines[line_index] = f"{lines[line_index].rstrip()} {' '.join(missing)}"


def _format_bibliography_entry(bibliography_entry: str) -> list[str]:
    """Render one bibliography group as visually separated Markdown lines.

    The normal citation assignment path already creates one parent and one
    indented child per line. This also repairs a flattened group such as
    ``[1] [Source: file.md] [1.1] Sales [1.2] Guidance`` at the final
    rendering boundary, so an upstream formatter cannot collapse the group.
    """
    entry = bibliography_entry.strip()
    source_match = _SOURCE_GROUP_RE.fullmatch(entry)
    if source_match is None:
        return [entry]

    lines = [
        f"[{source_match.group('parent')}] [Source: {source_match.group('filename')}]"
    ]
    children = source_match.group("children").strip()
    if not children:
        return lines

    child_matches = list(_SOURCE_CHILD_RE.finditer(children))
    if not child_matches:
        return [entry]

    for child_match in child_matches:
        section = " ".join(child_match.group("section").split())
        lines.extend(
            [
                "",
                f"{BIBLIOGRAPHY_CHILD_INDENT}[{child_match.group('label')}] {section}",
            ]
        )
    return lines


def render_draft_summary_block(
    main_user_query: str,
    summary_draft: str,
) -> str:
    """Turn a provider draft into the deterministic public summary block.

    The model draft may contain upstream ``[Source: filename — section]``
    references. The wrapper replaces those references with local numeric
    labels and owns all metadata and bibliography formatting.
    """
    draft = _normalize_summary_draft(summary_draft)
    if not draft:
        raise ValueError("summary model returned an empty draft")
    if any(
        marker in draft
        for marker in (
            "[[OSHA_ID:",
            "[[OSHA_SUMMARY_TYPE:",
            "[[OSHA_STATUS:",
        )
    ):
        raise ValueError("summary model returned final metadata instead of a draft")

    rewritten, assignments = rewrite_upstream_citations(draft)
    return render_summary_block(
        main_user_query,
        rewritten,
        assignments.bibliography_entries,
    )


def _normalize_summary_draft(summary_draft: str) -> str:
    """Remove harmless model wrappers without repairing final output fields."""
    draft = summary_draft.strip()
    lines = draft.splitlines()

    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```markdown", "```md"}
        and lines[-1].strip() == "```"
    ):
        draft = "\n".join(lines[1:-1]).strip()
        lines = draft.splitlines()

    if lines and lines[0].strip().lower() == "## answer summary":
        lines = lines[1:]

    for index, line in enumerate(lines):
        if line.strip().lower() == "## bibliography":
            lines = lines[:index]
            break

    # A provider can hit max_tokens in the middle of an upstream citation.
    # Drop that incomplete trailing claim instead of persisting raw citation
    # syntax that the deterministic citation pass cannot assign.
    for index, line in enumerate(lines):
        source_fragments = line.split("[Source:")
        if len(source_fragments) > 1 and "]" not in source_fragments[-1]:
            lines = lines[:index]
            break

    lines = _drop_incomplete_trailing_line(lines)

    return "\n".join(lines).strip()


def _drop_incomplete_trailing_line(lines: list[str]) -> list[str]:
    """Drop a visibly truncated final prose line before storing the draft."""
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return lines

    last_line = lines[-1].strip()
    if not last_line or last_line.startswith("#"):
        return lines
    if last_line[-1] in ".!?;:)]}`|]":
        return lines

    words = re.sub(r"[^A-Za-z]+$", "", last_line).split()
    if words and words[-1].lower() in _INCOMPLETE_TRAILING_WORDS:
        lines.pop()
    return lines


def render_insufficient_evidence_block(main_user_query: str) -> str:
    """Render the required block when the answer sheet is unavailable."""
    return "\n".join(
        [
            build_metadata_header(main_user_query),
            "[[OSHA_STATUS:INSUFFICIENT_EVIDENCE]]",
            "",
            "## Bibliography",
            "",
            "[Source: unavailable]",
        ]
    )


def render_summary_unavailable_block(main_user_query: str) -> str:
    """Render the required block when the provider returns no summary text."""
    return "\n".join(
        [
            build_metadata_header(main_user_query),
            "[[OSHA_STATUS:SUMMARY_UNAVAILABLE]]",
            "",
            "## Bibliography",
            "",
            "[Source: unavailable]",
        ]
    )


def render_invalid_identifier_block(
    combined_insufficient_evidence: bool = False,
) -> str:
    """Render the required block when the main query identifier is invalid."""
    status = "INVALID_IDENTIFIER"
    if combined_insufficient_evidence:
        status += "+INSUFFICIENT_EVIDENCE"

    return "\n".join(
        [
            "[[OSHA_ID:<INVALID>]]",
            "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]",
            f"[[OSHA_STATUS:{status}]]",
            "",
            "## Bibliography",
            "",
            "[Source: unavailable]",
        ]
    )


def render_summary_with_budget(
    main_user_query: str,
    summary_lines: Sequence[SummaryLine],
    bibliography_entries: Sequence[str],
    s_summary: int,
    token_counter: Callable[[str], int],
) -> str:
    """Render a summary while dropping optional lower-priority detail first.

    The token counter is injected because the production tokenizer and runtime
    counting mechanism remain external inputs in the master specification.
    """
    selected = list(summary_lines)
    candidate = _render_lines(
        main_user_query,
        selected,
        bibliography_entries,
    )
    if token_counter(candidate) <= s_summary:
        return candidate

    removable = sorted(
        (
            (index, line)
            for index, line in enumerate(selected)
            if not line.mandatory
        ),
        key=lambda item: (item[1].priority, item[0]),
        reverse=True,
    )
    for index, _line in removable:
        selected[index] = None  # type: ignore[assignment]
        candidate = _render_lines(
            main_user_query,
            [line for line in selected if line is not None],
            bibliography_entries,
        )
        if token_counter(candidate) <= s_summary:
            return candidate

    raise ValueError(
        "Ssummary is too small for the mandatory summary structure"
    )


def _render_lines(
    main_user_query: str,
    summary_lines: Sequence[SummaryLine],
    bibliography_entries: Sequence[str],
) -> str:
    return render_summary_block(
        main_user_query,
        "\n".join(line.text for line in summary_lines),
        bibliography_entries,
    )
