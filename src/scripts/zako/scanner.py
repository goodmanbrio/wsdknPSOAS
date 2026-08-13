"""Deterministic names-only inventory and source expansion for Zako."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_FILES_KEY = "Root-level files"
ROOT_FILES_PATH = "__ROOT_FILES__"
_THEMATIC_PREFIX = re.compile(r"^\d+\s+")


class ScanError(RuntimeError):
    """The configured source root cannot be safely scanned."""


class SelectionInvalidated(RuntimeError):
    """A previously approved source no longer exists at relay time."""


@dataclass(frozen=True)
class SourceCandidate:
    """Internal representation of one selectable top-level source."""

    identifier: str
    root_path: Path | None
    files: tuple[str, ...]
    direct_files: tuple[str, ...]
    file_count: int | None
    complete: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class Inventory:
    """Names-only inventory plus internal candidate metadata."""

    root: Path
    candidates: dict[str, SourceCandidate]
    payload: dict[str, Any]


@dataclass(frozen=True)
class _NodeResult:
    payload: dict[str, Any]
    files: tuple[str, ...]
    direct_files: tuple[str, ...]
    file_count: int | None
    complete: bool


def _posix_relative(path: Path) -> str:
    return path.as_posix()


def _iter_entries(path: Path) -> list[os.DirEntry[str]]:
    """Return directory entries in lexical order without following links."""
    try:
        with os.scandir(path) as entries:
            return sorted(entries, key=lambda entry: entry.name)
    except OSError as exc:
        raise ScanError(f"cannot read directory: {path}") from exc


def _unreadable_node() -> _NodeResult:
    return _NodeResult(
        payload={
            "folders": {},
            "files": [],
            "file_count": None,
            "readable": False,
        },
        files=(),
        direct_files=(),
        file_count=None,
        complete=False,
    )


def _scan_node(path: Path, relative: Path) -> _NodeResult:
    """Scan one visible directory recursively, never opening file contents."""
    try:
        entries = _iter_entries(path)
    except ScanError:
        # A nested unreadable branch is represented, not fatal to the root.
        return _unreadable_node()

    folders: dict[str, dict[str, Any]] = {}
    direct_names: list[str] = []
    all_files: list[str] = []
    complete = True

    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue

        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                child = _scan_node(path / name, relative / name)
                folders[name] = child.payload
                all_files.extend(child.files)
                if not child.complete:
                    complete = False
            elif entry.is_file(follow_symlinks=False):
                direct_names.append(name)
                all_files.append(_posix_relative(relative / name))
        except OSError:
            # The entry was visible but its type could not be determined.
            # Preserve safety by excluding it and marking this branch partial.
            complete = False

    direct_names.sort()
    all_files.sort()
    payload: dict[str, Any] = {
        "folders": folders,
        "files": direct_names,
        "file_count": len(all_files) if complete else None,
    }
    if not complete:
        payload["readable"] = False

    return _NodeResult(
        payload=payload,
        files=tuple(all_files),
        direct_files=tuple(
            _posix_relative(relative / name) for name in direct_names
        ),
        file_count=len(all_files) if complete else None,
        complete=complete,
    )


def validate_source_root(source_root: Path) -> Path:
    """Validate and normalize the configured source root."""
    root = Path(source_root)
    try:
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise ScanError("source root is not a readable directory")
        # Force an access check before query/model work begins.
        _iter_entries(root)
    except (OSError, ScanError) as exc:
        if isinstance(exc, ScanError) and str(exc) == "source root is not a readable directory":
            raise
        raise ScanError("source root is not a readable directory") from exc
    return root


def scan_source_root(source_root: Path) -> Inventory:
    """Build the complete visible names-only inventory for ``source_root``."""
    root = validate_source_root(source_root)
    try:
        entries = _iter_entries(root)
    except ScanError as exc:
        raise ScanError("source root is not a readable directory") from exc

    candidates: dict[str, SourceCandidate] = {}
    direct_root_files: list[str] = []

    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue

        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if name == ROOT_FILES_KEY:
                    raise ScanError(
                        f"top-level folder uses reserved identifier: {name}"
                    )
                relative = Path(name)
                node = _scan_node(root / name, relative)
                candidates[name] = SourceCandidate(
                    identifier=name,
                    root_path=root / name,
                    files=node.files,
                    direct_files=node.direct_files,
                    file_count=node.file_count,
                    complete=node.complete,
                    payload=node.payload,
                )
            elif entry.is_file(follow_symlinks=False):
                direct_root_files.append(name)
        except ScanError:
            raise
        except OSError:
            # A visible but unclassifiable top-level entry is not a source.
            continue

    direct_root_files.sort()
    if direct_root_files:
        root_files = tuple(direct_root_files)
        candidates[ROOT_FILES_KEY] = SourceCandidate(
            identifier=ROOT_FILES_KEY,
            root_path=None,
            files=root_files,
            direct_files=root_files,
            file_count=len(root_files),
            complete=True,
            payload={
                "folders": {},
                "files": list(direct_root_files),
                "file_count": len(root_files),
            },
        )

    payload = {identifier: candidate.payload for identifier, candidate in candidates.items()}
    return Inventory(root=root, candidates=candidates, payload=payload)


def display_label(identifier: str) -> str:
    """Return the user-facing label without changing the source identifier."""
    if identifier == ROOT_FILES_KEY:
        return identifier
    if _THEMATIC_PREFIX.match(identifier):
        return f"Thematic source: {identifier}"
    return identifier


def canonicalize_source_identifier(
    raw: str,
    inventory: Inventory,
) -> str:
    """Resolve a user-provided top-level folder path to an exact identifier."""
    value = raw.strip().strip("[](){}<>")
    if not value:
        raise ValueError("source identifier is empty")

    if value in inventory.candidates:
        return value
    casefold_matches = [
        identifier
        for identifier in inventory.candidates
        if identifier.casefold() == value.casefold()
    ]
    if len(casefold_matches) == 1:
        return casefold_matches[0]

    path_value = Path(value)
    root = inventory.root
    if path_value.is_absolute():
        try:
            relative = path_value.relative_to(root)
        except ValueError as exc:
            raise ValueError("path is outside source root") from exc
    else:
        normalized = os.path.normpath(value)
        relative = Path(normalized)

    if (
        not relative.parts
        or len(relative.parts) != 1
        or relative.parts[0] in {".", ".."}
        or ".." in relative.parts
    ):
        raise ValueError("path is not an immediate child directory")

    identifier = relative.parts[0]
    candidate_path = root / identifier
    if (
        identifier not in inventory.candidates
        or identifier == ROOT_FILES_KEY
        or candidate_path.is_symlink()
        or not candidate_path.is_dir()
    ):
        raise ValueError("path is not a selectable source folder")
    return identifier


def revalidate_selection(
    previous: Inventory,
    selected: list[str],
) -> tuple[Inventory, list[str]]:
    """Rescan and report selected folders/files that disappeared."""
    current = scan_source_root(previous.root)
    missing: list[str] = []
    for identifier in selected:
        old_candidate = previous.candidates.get(identifier)
        new_candidate = current.candidates.get(identifier)
        if old_candidate is None or new_candidate is None:
            missing.append(identifier)
            continue
        if identifier == ROOT_FILES_KEY:
            old_direct = set(old_candidate.direct_files)
            new_direct = set(new_candidate.direct_files)
            if not old_direct.issubset(new_direct):
                missing.append(identifier)
    return current, missing


def expand_selection(
    inventory: Inventory,
    selected: list[str],
) -> dict[str, list[str]]:
    """Return the direct public source-label-to-file-list dictionary."""
    result: dict[str, list[str]] = {}
    for identifier in selected:
        candidate = inventory.candidates.get(identifier)
        if candidate is None:
            raise SelectionInvalidated(identifier)
        result[identifier] = list(candidate.files)
    return result
