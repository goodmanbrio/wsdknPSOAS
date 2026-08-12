"""Upstream source parsing and grouped local citation assignment."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_UPSTREAM_SOURCE_REFERENCE_RE = re.compile(r"\[Source: [^\]\n]+\]")
_SOURCE_FILENAME_SECTION_RE = re.compile(
    r"^(?P<filename>.+?\.[A-Za-z0-9]{1,8})\s+—\s+(?P<section>.+)$"
)
_SOURCE_FILENAME_ONLY_RE = re.compile(
    r"^.+\.[A-Za-z0-9]{1,8}$"
)
_HTML_ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);")
_ENTITY_PREFIX_RE = re.compile(
    r"(?P<prefix>[A-Z][^;—]*?(?:\([^()]{1,12}\)|"
    r"\b(?:Inc|Corp|Corporation|Ltd|Limited|Holdings|PLC|LLC|Co)\.?))"
    r"\s+>\s+"
)
BIBLIOGRAPHY_CHILD_INDENT = "\u00a0" * 4
_PUBLIC_BIBLIOGRAPHY_PARENT_RE = re.compile(
    r"^\[(?P<label>[1-9][0-9]*)\]\s+\[Source: (?P<filename>[^\]]+)\]$"
)
_PUBLIC_BIBLIOGRAPHY_CHILD_RE = re.compile(
    r"^(?:\s|\u00a0)+\[(?P<label>[1-9][0-9]*\.[1-9][0-9]*)\]\s+(?P<section>.+)$"
)
_LOCAL_CITATION_LABEL_RE = re.compile(
    r"\[(?P<label>[1-9][0-9]*(?:\.[1-9][0-9]*)?)\]"
)


@dataclass(frozen=True, slots=True)
class CitationAssignments:
    """Child labels for source-reference occurrences and grouped bibliography."""

    labels: tuple[str, ...]
    bibliography_entries: tuple[str, ...]


def expand_labeled_answer_sheet(answer_sheet_markdown: str) -> str:
    """Convert an option-A answer sheet back to upstream source citations.

    The summary model still reasons over exact ``[Source: filename — section]``
    references. Option-A answer sheets expose local labels publicly, so this
    adapter reconstructs those references from the answer-sheet bibliography.
    Raw pre-option-A answer sheets pass through unchanged.
    """
    if "[[OSHA_ID:" not in answer_sheet_markdown:
        return answer_sheet_markdown

    lines = answer_sheet_markdown.splitlines()
    bibliography_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "## Bibliography"
        ),
        None,
    )
    if bibliography_index is None:
        return answer_sheet_markdown

    label_to_reference: dict[str, str] = {}
    current_filename: str | None = None
    for line in lines[bibliography_index + 1 :]:
        parent_match = _PUBLIC_BIBLIOGRAPHY_PARENT_RE.fullmatch(line.strip())
        if parent_match is not None:
            current_filename = parent_match.group("filename")
            continue
        child_match = _PUBLIC_BIBLIOGRAPHY_CHILD_RE.fullmatch(line)
        if child_match is not None and current_filename is not None:
            label_to_reference[child_match.group("label")] = (
                f"[Source: {current_filename} — "
                f"{child_match.group('section').strip()}]"
            )

    body_lines = [
        line
        for line in lines[:bibliography_index]
        if not line.startswith("[[OSHA_ID:")
        and not line.startswith("[[OSHA_SUMMARY_TYPE:")
    ]
    body = "\n".join(body_lines).strip()

    if not label_to_reference:
        return body

    def replace(match: re.Match[str]) -> str:
        return label_to_reference.get(match.group("label"), match.group(0))

    return _LOCAL_CITATION_LABEL_RE.sub(replace, body)


def assign_local_citation_labels(
    upstream_references: Iterable[str],
) -> CitationAssignments:
    """Assign parent filename labels and child section labels.

    A repeated filename reuses its parent label. A repeated filename-section
    pair reuses its child label. Each bibliography entry is one parent group
    containing its indented child section lines.
    """
    parent_by_filename: dict[str, str] = {}
    child_by_reference: dict[tuple[str, str], str] = {}
    sections_by_filename: dict[str, list[tuple[str, str]]] = {}
    occurrence_labels: list[str] = []

    for upstream_reference_group in upstream_references:
        for upstream_reference in split_upstream_references(
            upstream_reference_group
        ):
            filename, section = _parse_upstream_reference(upstream_reference)

            parent_label = parent_by_filename.get(filename)
            if parent_label is None:
                parent_label = str(len(parent_by_filename) + 1)
                parent_by_filename[filename] = parent_label
                sections_by_filename[filename] = []

            reference_key = (filename, section)
            child_label = child_by_reference.get(reference_key)
            if child_label is None:
                section_number = len(sections_by_filename[filename]) + 1
                child_label = f"{parent_label}.{section_number}"
                child_by_reference[reference_key] = child_label
                sections_by_filename[filename].append(
                    (child_label, _normalize_section_for_display(section))
                )

            occurrence_labels.append(child_label)

    bibliography_entries = tuple(
        _render_source_group(
            parent_by_filename[filename],
            filename,
            sections_by_filename[filename],
        )
        for filename in parent_by_filename
    )
    return CitationAssignments(
        labels=tuple(occurrence_labels),
        bibliography_entries=bibliography_entries,
    )


def rewrite_upstream_citations(
    summary_draft: str,
) -> tuple[str, CitationAssignments]:
    """Replace upstream references with grouped child labels."""
    upstream_references = [
        atomic_reference
        for reference_group in _UPSTREAM_SOURCE_REFERENCE_RE.findall(summary_draft)
        for atomic_reference in split_upstream_references(reference_group)
    ]
    assignments = assign_local_citation_labels(upstream_references)
    labels = iter(assignments.labels)

    rewritten = _UPSTREAM_SOURCE_REFERENCE_RE.sub(
        lambda match: " ".join(
            f"[{next(labels)}]"
            for _ in split_upstream_references(match.group(0))
        ),
        summary_draft,
    )
    return rewritten, assignments


def extract_upstream_references(text: str) -> tuple[str, ...]:
    """Return exact upstream source-reference strings in first-use order."""
    return tuple(
        atomic_reference
        for reference_group in _UPSTREAM_SOURCE_REFERENCE_RE.findall(text)
        for atomic_reference in split_upstream_references(reference_group)
    )


def split_upstream_references(upstream_reference_group: str) -> tuple[str, ...]:
    """Split one bracketed group into atomic ``[Source: ...]`` references.

    The upstream synthesizer normally repeats ``Source:`` between references,
    but it can also emit a compact form such as::

        [Source: file_a.md — Section A; file_b.md — Section B]

    Semicolons delimit sections. A segment that starts with a filename and an
    em dash starts a new source file; other segments remain sections of the
    current file.
    """
    if not (
        upstream_reference_group.startswith("[Source: ")
        and upstream_reference_group.endswith("]")
    ):
        raise ValueError(
            "upstream reference group must use the approved [Source: ...] format"
        )

    body = upstream_reference_group[len("[Source: ") : -1]
    parts = _split_source_group_parts(body)
    if not parts or not parts[0]:
        raise ValueError("upstream reference group must not be empty")

    if len(parts) == 1 and " — " not in parts[0]:
        # Preserve sectionless source markers long enough for the caller to
        # report the more useful invalid-draft/invalid-reference error.
        return (upstream_reference_group,)

    atomic_references: list[str] = []
    current_filename: str | None = None
    pending_filenames: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("Source: "):
            part = part[len("Source: ") :].strip()

        filename_section_match = _SOURCE_FILENAME_SECTION_RE.fullmatch(part)
        if filename_section_match is not None:
            filename = filename_section_match.group("filename").strip()
            section = filename_section_match.group("section").strip()
            if pending_filenames:
                atomic_references.extend(
                    f"[Source: {pending_filename} — {section}]"
                    for pending_filename in pending_filenames
                )
                pending_filenames.clear()
            atomic_references.append(f"[Source: {filename} — {section}]")
            current_filename = filename
        elif _SOURCE_FILENAME_ONLY_RE.fullmatch(part):
            pending_filenames.append(part)
        else:
            section = part
            if not section:
                raise ValueError("upstream reference section must be non-empty")
            if pending_filenames:
                atomic_references.extend(
                    f"[Source: {pending_filename} — {section}]"
                    for pending_filename in pending_filenames
                )
                current_filename = pending_filenames[-1]
                pending_filenames.clear()
            elif current_filename is not None:
                atomic_references.append(
                    f"[Source: {current_filename} — {section}]"
                )
            else:
                raise ValueError(
                    "upstream reference section has no source filename"
                )

    if pending_filenames:
        raise ValueError(
            "upstream reference filename has no section"
        )

    return tuple(atomic_references)


def _split_source_group_parts(body: str) -> list[str]:
    """Split semicolon-delimited source parts without breaking HTML entities."""
    entities: list[str] = []

    def protect_entity(match: re.Match[str]) -> str:
        entities.append(match.group(0))
        return f"__OSHA_HTML_ENTITY_{len(entities) - 1}__"

    protected = _HTML_ENTITY_RE.sub(protect_entity, body)
    parts = [part.strip() for part in protected.split(";")]
    for index, part in enumerate(parts):
        for entity_index, entity in enumerate(entities):
            part = part.replace(
                f"__OSHA_HTML_ENTITY_{entity_index}__",
                entity,
            )
        parts[index] = part
    return parts


def _parse_upstream_reference(upstream_reference: str) -> tuple[str, str]:
    if not (
        upstream_reference.startswith("[Source: ")
        and upstream_reference.endswith("]")
    ):
        raise ValueError(
            "upstream reference must use the approved [Source: ...] format"
        )

    body = upstream_reference[len("[Source: ") : -1]
    if " — " not in body:
        raise ValueError(
            "upstream reference must contain a filename and section"
        )
    filename, section = body.split(" — ", 1)
    if not filename or not section:
        raise ValueError(
            "upstream reference filename and section must be non-empty"
        )
    return filename, section


def _normalize_section_for_display(section: str) -> str:
    """Remove a repeated company/entity prefix from displayed sections.

    Upstream sections may be emitted as ``Company (TICKER) > Section`` and
    may repeat that prefix across semicolon-separated section names. The full
    filename remains the parent citation, so repeating the entity in every
    child line adds noise without improving traceability.
    """
    return _ENTITY_PREFIX_RE.sub("", section)


def _render_source_group(
    parent_label: str,
    filename: str,
    sections: list[tuple[str, str]],
) -> str:
    lines = [f"[{parent_label}] [Source: {filename}]"]
    lines.extend(
        f"{BIBLIOGRAPHY_CHILD_INDENT}[{child_label}] {section}"
        for child_label, section in sections
    )
    return "\n".join(lines)
