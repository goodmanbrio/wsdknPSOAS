"""Deterministic validation for the approved summary Markdown block."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .citations import BIBLIOGRAPHY_CHILD_INDENT
from .identifier import encode_query_identifier


_BIBLIOGRAPHY_PARENT_RE = re.compile(
    r"^\[(?P<label>[1-9][0-9]*)\]\s+\[Source: (?P<filename>[^\]]+)\]$"
)
_BIBLIOGRAPHY_CHILD_RE = re.compile(
    rf"^(?:    |{re.escape(BIBLIOGRAPHY_CHILD_INDENT)})"
    r"\[(?P<label>[1-9][0-9]*\.[1-9][0-9]*)\]\s+(?P<section>.+)$"
)
_BIBLIOGRAPHY_DERIVED_RE = re.compile(
    r"^\[(?P<label>[1-9][0-9]*)\]\s+\[(?P<body>Derived: .+)\]$"
)
_INLINE_CITATION_RE = re.compile(
    r"\[(?P<label>[1-9][0-9]*(?:\.[1-9][0-9]*)?)\]"
)
_DERIVED_BODY_RE = re.compile(
    r"^Derived: FROM (?P<inputs>"
    r"[1-9][0-9]*(?:\.[1-9][0-9]*)?"
    r"(?:,[1-9][0-9]*(?:\.[1-9][0-9]*)?)*"
    r")"
    r" \| FORMULA: (?P<formula>.+)$"
)
_MINI_OSHA_MARKER_RE = re.compile(r"(?i)\bmini[-_ ]osha\b")


@dataclass(frozen=True, slots=True)
class _BibliographyEntry:
    label: str
    kind: str
    body: str
    parent_label: str | None = None


def validate_section_order(summary_block: str) -> None:
    """Validate metadata and the required summary-section order."""
    lines = summary_block.splitlines()
    if len(lines) < 4:
        raise ValueError("summary block is too short")

    if not lines[0].startswith("[[OSHA_ID:") or not lines[0].endswith("]]"):
        raise ValueError("summary block must begin with OSHA_ID metadata")
    if lines[1] != "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]":
        raise ValueError("summary type metadata is missing or out of order")

    answer_heading_indices = [
        index for index, line in enumerate(lines) if line == "## Answer Summary"
    ]
    bibliography_heading_indices = [
        index for index, line in enumerate(lines) if line == "## Bibliography"
    ]

    if len(answer_heading_indices) != 1:
        raise ValueError("summary block must contain one Answer Summary heading")
    if len(bibliography_heading_indices) != 1:
        raise ValueError("summary block must contain one Bibliography heading")
    if answer_heading_indices[0] >= bibliography_heading_indices[0]:
        raise ValueError("Answer Summary must precede Bibliography")


def _bibliography_entries(summary_block: str) -> list[_BibliographyEntry]:
    """Return ordered bibliography entries from a structurally valid block."""
    validate_section_order(summary_block)
    bibliography_start = summary_block.splitlines().index("## Bibliography") + 1
    entries: list[_BibliographyEntry] = []
    seen_parent_labels: set[str] = set()
    seen_child_labels: set[str] = set()
    seen_derived_labels: set[str] = set()
    current_parent: str | None = None
    saw_derived = False

    for line in summary_block.splitlines()[bibliography_start:]:
        if not line.strip():
            continue

        parent_match = _BIBLIOGRAPHY_PARENT_RE.fullmatch(line)
        if parent_match is not None:
            if saw_derived:
                raise ValueError(
                    "ordinary bibliography groups must precede derived entries"
                )
            label = parent_match.group("label")
            if label in seen_parent_labels:
                raise ValueError(f"duplicate bibliography label: {label}")
            filename = parent_match.group("filename")
            if "..." in filename:
                raise ValueError("bibliography filename must be complete")
            seen_parent_labels.add(label)
            current_parent = label
            entries.append(_BibliographyEntry(label, "source_parent", filename))
            continue

        child_match = _BIBLIOGRAPHY_CHILD_RE.fullmatch(line)
        if child_match is not None:
            if saw_derived or current_parent is None:
                raise ValueError("bibliography child is outside a source group")
            label = child_match.group("label")
            parent_label = label.split(".", 1)[0]
            if parent_label != current_parent:
                raise ValueError(
                    f"bibliography child {label} is under the wrong parent"
                )
            if label in seen_child_labels:
                raise ValueError(f"duplicate bibliography label: {label}")
            seen_child_labels.add(label)
            entries.append(
                _BibliographyEntry(
                    label,
                    "source_section",
                    child_match.group("section"),
                    parent_label,
                )
            )
            continue

        derived_match = _BIBLIOGRAPHY_DERIVED_RE.fullmatch(line)
        if derived_match is not None:
            saw_derived = True
            current_parent = None
            label = derived_match.group("label")
            if label in seen_derived_labels:
                raise ValueError(f"duplicate bibliography label: {label}")
            seen_derived_labels.add(label)
            entries.append(
                _BibliographyEntry(label, "derived", derived_match.group("body"))
            )
            continue

        raise ValueError(f"invalid bibliography entry: {line}")

    for parent_label in seen_parent_labels:
        if not any(
            entry.kind == "source_section"
            and entry.parent_label == parent_label
            for entry in entries
        ):
            raise ValueError(f"source group {parent_label} has no sections")

    return entries


def validate_citation_integrity(summary_block: str) -> None:
    """Ensure every inline numeric citation resolves to the bibliography."""
    entries = _bibliography_entries(summary_block)
    bibliography_labels = {
        entry.label
        for entry in entries
        if entry.kind in {"source_section", "derived"}
    }
    answer_summary = summary_block.split("## Bibliography", 1)[0]

    for match in _INLINE_CITATION_RE.finditer(answer_summary):
        label = match.group("label")
        if label not in bibliography_labels:
            raise ValueError(f"inline citation has no bibliography entry: {label}")


def validate_derived_lineage(summary_block: str) -> None:
    """Validate derived records, prior inputs, formulas, and label sequence."""
    entries = _bibliography_entries(summary_block)
    seen_labels: set[str] = {
        entry.label
        for entry in entries
        if entry.kind == "source_section"
    }
    parent_count = sum(
        1 for entry in entries if entry.kind == "source_parent"
    )
    derived_count = 0

    for entry in entries:
        if entry.kind != "derived":
            continue

        label = entry.label
        body = entry.body
        match = _DERIVED_BODY_RE.fullmatch(body)
        if match is None:
            raise ValueError(f"invalid derived bibliography entry: {body}")

        input_labels = match.group("inputs").split(",")
        if not seen_labels:
            raise ValueError("derived citation has no prior cited inputs")
        derived_count += 1
        expected_label = str(parent_count + derived_count)
        if label != expected_label:
            raise ValueError(
                f"derived citation {label} is not the next top-level label"
            )
        if any(input_label not in seen_labels for input_label in input_labels):
            raise ValueError("derived citation references an unknown input")
        if any(input_label == label for input_label in input_labels):
            raise ValueError("derived citation contains a forward reference")

        seen_labels.add(label)


def validate_bibliography_order(summary_block: str) -> None:
    """Validate ordinary-label order and derived-record placement/order."""
    entries = _bibliography_entries(summary_block)
    ordinary_labels: list[int] = []
    derived_labels: list[int] = []
    saw_derived = False
    expected_child_by_parent: dict[str, int] = {}

    for entry in entries:
        if entry.kind == "source_parent":
            label = int(entry.label)
            if saw_derived:
                raise ValueError(
                    "ordinary bibliography groups must precede derived entries"
                )
            ordinary_labels.append(label)
            expected_child_by_parent[entry.label] = 1
        elif entry.kind == "source_section":
            parent_label, child_number = entry.label.split(".", 1)
            expected_child = expected_child_by_parent.get(parent_label)
            if expected_child is None or int(child_number) != expected_child:
                raise ValueError(
                    f"source sections for parent {parent_label} are out of order"
                )
            expected_child_by_parent[parent_label] += 1
        elif entry.kind == "derived":
            saw_derived = True
            derived_labels.append(int(entry.label))

    if ordinary_labels != sorted(ordinary_labels):
        raise ValueError("ordinary bibliography entries are out of order")
    if ordinary_labels != list(range(1, len(ordinary_labels) + 1)):
        raise ValueError("ordinary bibliography parent labels are not contiguous")
    if derived_labels != sorted(derived_labels):
        raise ValueError("derived bibliography entries are out of order")


def validate_no_stale_branch_markers(summary_block: str) -> None:
    """Reject identifiers or markers from the obsolete mini-OSHA design."""
    forbidden_markers = ("SUBQUERY_ID", "ASSIGNED_SSQ")
    for marker in forbidden_markers:
        if marker in summary_block:
            raise ValueError(f"stale branch marker found: {marker}")
    if _MINI_OSHA_MARKER_RE.search(summary_block):
        raise ValueError("stale branch marker found: mini-OSHA")


def validate_summary_output(summary_block: str) -> None:
    """Validate one deterministic public summary block."""
    lines = normalize_summary_output(summary_block).splitlines()
    if len(lines) < 3:
        raise ValueError("summary output is too short")
    if not lines[0].startswith("[[OSHA_ID:") or not lines[0].endswith("]]"):
        raise ValueError(
            f"summary output has invalid OSHA_ID metadata: {lines[0]!r}"
        )
    if lines[1] != "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]":
        raise ValueError(
            f"summary output has invalid summary-type metadata: {lines[1]!r}"
        )

    status_lines = [
        line for line in lines if line.startswith("[[OSHA_STATUS:")
    ]
    if status_lines:
        if len(status_lines) != 1:
            raise ValueError("summary output has multiple status markers")
        if status_lines[0] not in {
            "[[OSHA_STATUS:INSUFFICIENT_EVIDENCE]]",
            "[[OSHA_STATUS:SUMMARY_UNAVAILABLE]]",
            "[[OSHA_STATUS:INVALID_IDENTIFIER]]",
            "[[OSHA_STATUS:INVALID_IDENTIFIER+INSUFFICIENT_EVIDENCE]]",
        }:
            raise ValueError("summary output has an unknown status marker")
        if "## Bibliography" not in lines:
            raise ValueError("status block is missing Bibliography")
        return

    validate_section_order(summary_block)
    validate_citation_integrity(summary_block)
    validate_derived_lineage(summary_block)
    validate_bibliography_order(summary_block)
    validate_no_stale_branch_markers(summary_block)


def normalize_summary_output(
    summary_block: str,
    main_user_query: str | None = None,
) -> str:
    """Extract the single contract block from harmless model wrappers."""
    normalized = summary_block.strip()
    lines = normalized.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```markdown", "```md"}
        and lines[-1].strip() == "```"
    ):
        normalized = "\n".join(lines[1:-1]).strip()

    marker_start = normalized.find("[[OSHA_ID:")
    if marker_start > 0:
        normalized = normalized[marker_start:].strip()

    if normalized.endswith("```"):
        normalized = normalized[:-3].rstrip()

    lines = normalized.splitlines()
    if (
        len(lines) >= 3
        and lines[0].startswith("[[OSHA_ID:")
        and not lines[1].strip()
        and lines[2].lstrip().startswith("[[OSHA_SUMMARY_TYPE")
    ):
        lines.pop(1)

    if len(lines) >= 2:
        type_match = re.fullmatch(
            r'\[\[OSHA_SUMMARY_TYPE\s*:\s*([A-Za-z_-]+)\s*\]\]',
            lines[1].strip(),
        )
        if type_match:
            type_value = type_match.group(1).upper().replace("-", "_")
            if type_value in {"ANSWER_SHEET", "STORED_CONTEXT_SUMMARY"}:
                lines[1] = "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]"

    if main_user_query is not None and lines and lines[0].startswith("[[OSHA_ID:"):
        lines[0] = f"[[OSHA_ID:{encode_query_identifier(main_user_query)}]]"

    return "\n".join(lines).strip()
