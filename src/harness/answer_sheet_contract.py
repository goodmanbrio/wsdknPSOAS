"""Deterministic public contract for one research answer sheet.

The research synthesizer writes source references in the form
``[Source: filename — section]``.  This module owns the public answer-sheet
boundary: query metadata, local citation labels, and the bibliography.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from urllib.parse import quote


_UPSTREAM_SOURCE_REFERENCE_RE = re.compile(r"\[Source: [^\]\n]+\]")
_SOURCE_FILENAME_SECTION_RE = re.compile(
    r"^(?P<filename>.+?\.[A-Za-z0-9]{1,8})\s+—\s+(?P<section>.+)$"
)
_SOURCE_FILENAME_ONLY_RE = re.compile(r"^.+\.[A-Za-z0-9]{1,8}$")
_HTML_ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);")
_ENTITY_PREFIX_RE = re.compile(
    r"(?P<prefix>[A-Z][^;—]*?(?:\([^()]{1,12}\)|"
    r"\b(?:Inc|Corp|Corporation|Ltd|Limited|Holdings|PLC|LLC|Co)\.?))"
    r"\s+>\s+"
)
_LOCAL_CITATION_RE = re.compile(r"\[(?P<label>[1-9][0-9]*(?:\.[1-9][0-9]*)?)\]")
_BIBLIOGRAPHY_PARENT_RE = re.compile(
    r"^\[(?P<label>[1-9][0-9]*)\]\s+\[Source: (?P<filename>[^\]]+)\]$"
)
_BIBLIOGRAPHY_CHILD_RE = re.compile(
    r"^(?:\s| )+\[(?P<label>[1-9][0-9]*\.[1-9][0-9]*)\]\s+(?P<section>.+)$"
)
BIBLIOGRAPHY_CHILD_INDENT = " " * 4


@dataclass(frozen=True, slots=True)
class CitationAssignments:
    labels: tuple[str, ...]
    bibliography_entries: tuple[str, ...]


def encode_query_identifier(main_user_query: str) -> str:
    """Percent-encode the exact main query for the public OSHA_ID marker."""
    if not isinstance(main_user_query, str):
        raise TypeError("main_user_query must be a string")
    return quote(main_user_query, safe="-._~", encoding="utf-8", errors="strict")


def format_answer_sheet(main_user_query: str, answer_draft: str) -> str:
    """Wrap one synthesizer draft in the public answer-sheet contract."""
    if main_user_query == "":
        raise ValueError("main_user_query must not be empty")

    draft = _normalize_draft(answer_draft)
    references = _extract_atomic_references(draft)
    assignments = _assign_local_citation_labels(references)
    rewritten = _rewrite_upstream_citations(draft, assignments)
    rewritten = _attach_standalone_citations(rewritten, assignments.labels)

    bibliography = assignments.bibliography_entries or ("[Source: unavailable]",)
    return "\n".join(
        [
            f"[[OSHA_ID:{encode_query_identifier(main_user_query)}]]",
            "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]",
            "",
            rewritten,
            "",
            "## Bibliography",
            "",
            *_separate_bibliography_entries(bibliography),
        ]
    ).strip()


def expand_answer_sheet_for_summary(answer_sheet_markdown: str) -> str:
    """Convert public local labels back to source references for summarization.

    The standalone summary package consumes the original synthesizer citation
    form.  This adapter lets it accept the now-labeled public answer sheet
    without making the summary package depend on the upstream harness.
    """
    if "[[OSHA_ID:" not in answer_sheet_markdown:
        return answer_sheet_markdown

    lines = answer_sheet_markdown.splitlines()
    bibliography_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "## Bibliography"),
        None,
    )
    if bibliography_index is None:
        return answer_sheet_markdown

    label_to_reference = _parse_public_bibliography(lines[bibliography_index + 1 :])
    body_lines = [
        line
        for line in lines[:bibliography_index]
        if not line.startswith("[[OSHA_ID:")
        and not line.startswith("[[OSHA_SUMMARY_TYPE:")
    ]

    def replace_label(match: re.Match[str]) -> str:
        label = match.group("label")
        return label_to_reference.get(label, match.group(0))

    body = "\n".join(body_lines).strip()
    return _LOCAL_CITATION_RE.sub(replace_label, body)


def _normalize_draft(answer_draft: str) -> str:
    if not isinstance(answer_draft, str) or not answer_draft.strip():
        raise ValueError("answer draft must not be empty")

    lines = answer_draft.strip().splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```markdown", "```md"}
        and lines[-1].strip() == "```"
    ):
        lines = lines[1:-1]

    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[[OSHA_ID:") or stripped.startswith("[[OSHA_SUMMARY_TYPE:"):
            continue
        if stripped == "## Bibliography":
            break
        if stripped.lower() == "answer sheet:":
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _extract_atomic_references(text: str) -> tuple[str, ...]:
    return tuple(
        atomic
        for group in _UPSTREAM_SOURCE_REFERENCE_RE.findall(text)
        for atomic in _split_upstream_references(group)
    )


def _assign_local_citation_labels(
    upstream_references: Iterable[str],
) -> CitationAssignments:
    parent_by_filename: dict[str, str] = {}
    child_by_reference: dict[tuple[str, str], str] = {}
    sections_by_filename: dict[str, list[tuple[str, str]]] = {}
    labels: list[str] = []

    for upstream_reference in upstream_references:
        filename, section = _parse_upstream_reference(upstream_reference)
        parent_label = parent_by_filename.get(filename)
        if parent_label is None:
            parent_label = str(len(parent_by_filename) + 1)
            parent_by_filename[filename] = parent_label
            sections_by_filename[filename] = []

        key = (filename, section)
        child_label = child_by_reference.get(key)
        if child_label is None:
            child_label = f"{parent_label}.{len(sections_by_filename[filename]) + 1}"
            child_by_reference[key] = child_label
            sections_by_filename[filename].append(
                (child_label, _normalize_section_for_display(section))
            )
        labels.append(child_label)

    bibliography = tuple(
        _render_source_group(
            parent_by_filename[filename],
            filename,
            sections_by_filename[filename],
        )
        for filename in parent_by_filename
    )
    return CitationAssignments(tuple(labels), bibliography)


def _rewrite_upstream_citations(
    draft: str,
    assignments: CitationAssignments,
) -> str:
    labels = iter(assignments.labels)

    def replace(match: re.Match[str]) -> str:
        group = match.group(0)
        count = len(_split_upstream_references(group))
        return " ".join(f"[{next(labels)}]" for _ in range(count))

    return _UPSTREAM_SOURCE_REFERENCE_RE.sub(replace, draft)


def _attach_standalone_citations(text: str, labels: Iterable[str]) -> str:
    known_labels = set(labels)
    output: list[str] = []
    for line in text.strip().splitlines():
        found: list[str] = []

        def remove(match: re.Match[str]) -> str:
            label = match.group("label")
            if label in known_labels and f"[{label}]" not in found:
                found.append(f"[{label}]")
                return ""
            return match.group(0)

        cleaned = _LOCAL_CITATION_RE.sub(remove, line).rstrip()
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        previous = len(output) - 1
        while previous >= 0 and not output[previous].strip():
            previous -= 1

        if cleaned.strip():
            if found:
                cleaned = f"{cleaned} {' '.join(found)}"
            output.append(cleaned)
        elif found and previous >= 0:
            existing = set(_LOCAL_CITATION_RE.findall(output[previous]))
            missing = [label for label in found if label[1:-1] not in existing]
            if missing:
                output[previous] = f"{output[previous].rstrip()} {' '.join(missing)}"
        elif cleaned or found:
            output.append(cleaned or " ".join(found))
        else:
            output.append("")

    return "\n".join(output).strip()


def _parse_upstream_reference(reference: str) -> tuple[str, str]:
    body = reference[len("[Source: ") : -1]
    if " — " not in body:
        raise ValueError("upstream source reference must contain a section")
    filename, section = body.split(" — ", 1)
    if not filename or not section:
        raise ValueError("upstream source filename and section must be non-empty")
    return filename, section


def _split_upstream_references(group: str) -> tuple[str, ...]:
    body = group[len("[Source: ") : -1]
    parts = _split_source_group_parts(body)
    atomic: list[str] = []
    current_filename: str | None = None
    pending_filenames: list[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("Source: "):
            part = part[len("Source: ") :].strip()
        match = _SOURCE_FILENAME_SECTION_RE.fullmatch(part)
        if match:
            filename = match.group("filename").strip()
            section = match.group("section").strip()
            atomic.extend(
                f"[Source: {pending} — {section}]" for pending in pending_filenames
            )
            pending_filenames.clear()
            atomic.append(f"[Source: {filename} — {section}]")
            current_filename = filename
        elif _SOURCE_FILENAME_ONLY_RE.fullmatch(part):
            pending_filenames.append(part)
        else:
            section = part
            if pending_filenames:
                atomic.extend(
                    f"[Source: {pending} — {section}]"
                    for pending in pending_filenames
                )
                current_filename = pending_filenames[-1]
                pending_filenames.clear()
            elif current_filename is not None:
                atomic.append(f"[Source: {current_filename} — {section}]")
            else:
                # No real filename to attach this section to (e.g. an
                # absence citation like "[Source: BunNavHarness — no xlsx
                # files queried]") — drop it rather than fail the whole
                # answer sheet over one malformed citation. The bracket
                # it came from ends up replaced with nothing, not a label.
                continue

    # A bare filename with nothing after it (dangling pending_filenames)
    # is the same situation — no section to pair it with. Drop, don't raise.
    return tuple(atomic)


def _split_source_group_parts(body: str) -> list[str]:
    entities: list[str] = []

    def protect(match: re.Match[str]) -> str:
        entities.append(match.group(0))
        return f"__OSHA_ENTITY_{len(entities) - 1}__"

    parts = _HTML_ENTITY_RE.sub(protect, body).split(";")
    for index, part in enumerate(parts):
        for entity_index, entity in enumerate(entities):
            part = part.replace(f"__OSHA_ENTITY_{entity_index}__", entity)
        parts[index] = part.strip()
    return parts


def _normalize_section_for_display(section: str) -> str:
    return _ENTITY_PREFIX_RE.sub("", section)


def _render_source_group(
    parent_label: str,
    filename: str,
    sections: list[tuple[str, str]],
) -> str:
    lines = [f"[{parent_label}] [Source: {filename}]"]
    lines.extend(
        f"{BIBLIOGRAPHY_CHILD_INDENT}[{label}] {section}"
        for label, section in sections
    )
    return "\n".join(lines)


def _separate_bibliography_entries(entries: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for entry_index, entry in enumerate(entries):
        for line_index, line in enumerate(entry.splitlines()):
            if entry_index or line_index:
                lines.append("")
            lines.append(line)
    return lines


def _parse_public_bibliography(lines: Iterable[str]) -> dict[str, str]:
    current_filename: str | None = None
    mapping: dict[str, str] = {}
    for line in lines:
        parent = _BIBLIOGRAPHY_PARENT_RE.fullmatch(line.strip())
        if parent:
            current_filename = parent.group("filename")
            continue
        child = _BIBLIOGRAPHY_CHILD_RE.fullmatch(line)
        if child and current_filename is not None:
            mapping[child.group("label")] = (
                f"[Source: {current_filename} — {child.group('section').strip()}]"
            )
    return mapping
