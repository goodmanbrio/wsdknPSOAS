"""Deterministic Zako contract tests.

These tests use temporary names-only fixture trees and selector/channel doubles.
They never call an LLM or open fixture-file contents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scripts.config import Config
from src.scripts.zako import FAILURE, zako_discover
from src.scripts.zako.scanner import (
    ROOT_FILES_KEY,
    ScanError,
    scan_source_root,
)
from src.scripts.zako.selector import SelectionFormatError, parse_selection
from src.scripts.zako.selector import BunragmanSelector


class ScriptedChannel:
    def __init__(self, answers, on_input=None):
        self.answers = list(answers)
        self.questions: list[str] = []
        self.outputs: list[str] = []
        self.on_input = on_input

    def print(self, message: str, markdown: bool = False) -> None:
        self.outputs.append(message)

    def input(self, question: str, markdown: bool = False):
        self.questions.append(question)
        if not self.answers:
            raise AssertionError(f"missing scripted answer for: {question}")
        answer = self.answers.pop(0)
        if self.on_input is not None:
            self.on_input(question, answer)
        return answer


class SelectorStub:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query: str, inventory: dict) -> str:
        self.calls.append((query, inventory))
        if not self.responses:
            raise AssertionError("unexpected second selector call")
        return self.responses.pop(0)


def make_config(root: Path) -> Config:
    return Config(zako_source_root=root)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture bytes that must never be read")


def test_scan_is_names_only_and_builds_exact_candidates(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "Reports" / "annual.pdf")
    touch(root / "LITE" / "Reports" / "notes.md")
    touch(root / "0 Optical" / "market.pdf")
    touch(root / "loose.md")
    touch(root / "LITE" / ".hidden.md")
    touch(root / ".hidden-root.md")
    (root / ".hidden-dir").mkdir(parents=True)
    touch(root / ".hidden-dir" / "secret.txt")
    outside = tmp_path / "outside.txt"
    touch(outside)
    (root / "LITE" / "link.txt").symlink_to(outside)

    inventory = scan_source_root(root)

    assert list(inventory.candidates) == ["0 Optical", "LITE", ROOT_FILES_KEY]
    assert inventory.candidates["LITE"].files == (
        "LITE/Reports/annual.pdf",
        "LITE/Reports/notes.md",
    )
    assert inventory.candidates["LITE"].file_count == 2
    assert inventory.candidates["0 Optical"].file_count == 1
    assert inventory.candidates[ROOT_FILES_KEY].files == ("loose.md",)
    payload = inventory.payload["LITE"]
    assert payload["folders"]["Reports"]["files"] == ["annual.pdf", "notes.md"]
    assert ".hidden.md" not in json.dumps(inventory.payload)
    assert "link.txt" not in json.dumps(inventory.payload)


def test_unreadable_nested_branch_is_partial_not_fatal(tmp_path, monkeypatch):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "Readable" / "a.md")
    (root / "LITE" / "Restricted").mkdir(parents=True)

    import src.scripts.zako.scanner as scanner

    original = scanner._iter_entries
    restricted = root / "LITE" / "Restricted"

    def blocked(path):
        if path == restricted:
            raise ScanError("blocked")
        return original(path)

    monkeypatch.setattr(scanner, "_iter_entries", blocked)
    inventory = scan_source_root(root)
    candidate = inventory.candidates["LITE"]

    assert candidate.file_count is None
    assert candidate.complete is False
    assert candidate.files == ("LITE/Readable/a.md",)
    assert candidate.payload["folders"]["Restricted"]["readable"] is False


def test_strict_selection_parser_deduplicates_and_drops_unknown():
    assert parse_selection(
        '["COHR", "NOPE", "COHR", "LITE"]',
        {"COHR", "LITE"},
    ) == ["COHR", "LITE"]
    assert parse_selection("[]", {"COHR"}) == []
    for raw in [
        "not json",
        "```json\n[\"COHR\"]\n```",
        '{"selected": ["COHR"]}',
        '"COHR"',
        '["COHR", 1]',
    ]:
        with pytest.raises(SelectionFormatError):
            parse_selection(raw, {"COHR"})


def test_invalid_root_fails_without_selector_call(tmp_path):
    selector = SelectorStub(['["LITE"]'])
    channel = ScriptedChannel([])
    result = zako_discover(
        "query",
        make_config(tmp_path / "missing"),
        channel=channel,
        selector=selector,
    )
    assert result == FAILURE
    assert selector.calls == []
    assert channel.questions == []


def test_confirmation_returns_active_query_and_direct_dictionary(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "report.pdf")
    touch(root / "0 Optical" / "market.pdf")
    touch(root / "root.md")
    selector = SelectorStub(['["0 Optical", "LITE"]'])
    channel = ScriptedChannel(["y"])

    result = zako_discover(
        "compare optical outlook",
        make_config(root),
        channel=channel,
        selector=selector,
    )

    assert result == (
        "compare optical outlook",
        {
            "0 Optical": ["0 Optical/market.pdf"],
            "LITE": ["LITE/report.pdf"],
        },
    )
    assert len(selector.calls) == 1
    assert "Selected 2 sources" in channel.questions[0]
    assert "Thematic source: 0 Optical/" in channel.questions[0]
    assert "aggregate" not in channel.questions[0].casefold()
    assert str(root) not in json.dumps(selector.calls[0][1])


def test_empty_selection_modify_then_confirm_without_second_model_call(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "report.pdf")
    selector = SelectorStub(["[]"])
    channel = ScriptedChannel(["modify", "add LITE", "y"])

    result = zako_discover(
        "query",
        make_config(root),
        channel=channel,
        selector=selector,
    )

    assert result == ("query", {"LITE": ["LITE/report.pdf"]})
    assert len(selector.calls) == 1


def test_redo_replaces_query_rebuilds_inventory_and_calls_once_more(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "report.pdf")
    touch(root / "COHR" / "report.pdf")
    selector = SelectorStub(["[]", '["COHR"]'])
    channel = ScriptedChannel(["redo", "coherent outlook", "y"])

    result = zako_discover(
        "initial query",
        make_config(root),
        channel=channel,
        selector=selector,
    )

    assert result == ("coherent outlook", {"COHR": ["COHR/report.pdf"]})
    assert [query for query, _ in selector.calls] == [
        "initial query",
        "coherent outlook",
    ]


def test_modification_is_atomic_and_additions_append(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "a.md")
    touch(root / "COHR" / "b.md")
    selector = SelectorStub(['["LITE"]'])
    channel = ScriptedChannel([
        "modify",
        "add COHR and remove DOES_NOT_EXIST",
        "add COHR",
        "y",
    ])

    result = zako_discover(
        "query",
        make_config(root),
        channel=channel,
        selector=selector,
    )

    assert result == (
        "query",
        {"LITE": ["LITE/a.md"], "COHR": ["COHR/b.md"]},
    )


def test_revalidation_missing_selected_source_cannot_confirm(tmp_path):
    root = tmp_path / "Packs"
    selected = root / "LITE" / "a.md"
    touch(selected)
    selector = SelectorStub(['["LITE"]'])
    removed = False

    def remove_on_confirmation(question, answer):
        nonlocal removed
        if not removed and "Should I use" in question:
            selected.unlink()
            (root / "LITE").rmdir()
            removed = True

    channel = ScriptedChannel(["y", "exit"], on_input=remove_on_confirmation)
    result = zako_discover(
        "query",
        make_config(root),
        channel=channel,
        selector=selector,
    )

    assert result == FAILURE
    assert any("no longer available" in question for question in channel.questions)


def test_eof_is_terminal_failure(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "a.md")
    selector = SelectorStub(['["LITE"]'])
    channel = ScriptedChannel([None])
    assert zako_discover(
        "query", make_config(root), channel=channel, selector=selector
    ) == FAILURE


def test_selector_exception_is_terminal_failure(tmp_path):
    root = tmp_path / "Packs"
    touch(root / "LITE" / "a.md")

    def broken(query, inventory):
        raise RuntimeError("provider unavailable")

    channel = ScriptedChannel([])
    assert zako_discover(
        "query", make_config(root), channel=channel, selector=broken
    ) == FAILURE


def test_production_selector_sends_only_query_and_inventory(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []

        def complete(self, prompt, system_prompt=None, label=""):
            self.calls.append((prompt, system_prompt, label))
            return '["LITE"]'

    backend = Backend()
    selector = BunragmanSelector(make_config(tmp_path), backend=backend)
    raw = selector("query", {"LITE": {"folders": {}, "files": []}})

    assert raw == '["LITE"]'
    assert len(backend.calls) == 1
    prompt, system_prompt, label = backend.calls[0]
    assert set(json.loads(prompt)) == {"query", "inventory"}
    assert str(tmp_path) not in prompt
    assert "bare JSON array" in system_prompt
    assert label == "ZAKO-Bunragman"
