"""Unit tests for durable orchestrator turn memory."""

import json

import pytest

from src.harness.execute_tool import execute_tool, set_session_dir
from src.harness.turn_memory import (
    load_active_context,
    read_turn_memory,
    write_turn_memory,
)


def test_write_turn_memory_creates_page_json_index_and_active_context(tmp_path):
    page = write_turn_memory(
        tmp_path,
        1,
        "What is the bull case for LITE?",
        "LITE has a strong position. [1.1]\n\n## Bibliography\n[1] [Source: report.md]",
        tool_calls=[
            {"id": "tc_1", "name": "run_research", "input": {"question": "bull case"}}
        ],
        tool_results=[
            {"id": "tc_1", "name": "run_research", "content": "Research complete."}
        ],
        stored_vars=[("$var_1", "Research answer", "large answer")],
    )

    assert page == tmp_path / "memory" / "turn_001.md"
    assert page.exists()
    assert "What is the bull case for LITE?" in page.read_text()
    assert "[1] [Source: report.md]" in page.read_text()
    assert "run_research" in page.read_text()

    record = json.loads((tmp_path / "memory" / "turn_001.json").read_text())
    assert record["turn_id"] == "turn_001"
    assert record["artifacts"][0]["handle"] == "$var_1"
    assert record["citations"] == ["## Bibliography", "[1] [Source: report.md]"]

    index = json.loads((tmp_path / "memory" / "index.json").read_text())
    assert index["latest_turn"] == "turn_001"
    assert index["turns"][0]["markdown"] == "memory/turn_001.md"

    context = load_active_context(tmp_path)
    assert "Cumulative session memory" in context
    assert "User: What is the bull case for LITE?" in context
    assert "memory/turn_001.md" in context


def test_active_context_accumulates_completed_turns(tmp_path):
    write_turn_memory(tmp_path, 1, "first request", "first answer")
    write_turn_memory(tmp_path, 2, "second request", "second answer")

    context = load_active_context(tmp_path)

    assert "### turn_001" in context
    assert "first request" in context
    assert "first answer" in context
    assert "### turn_002" in context
    assert "second request" in context
    assert "second answer" in context
    assert "Latest completed turn: turn_002" in context


def test_read_turn_memory_supports_latest_and_numeric_ids(tmp_path):
    write_turn_memory(tmp_path, 1, "first", "answer one")
    write_turn_memory(tmp_path, 2, "second", "answer two")

    assert "answer two" in read_turn_memory(tmp_path, "latest")
    assert "answer one" in read_turn_memory(tmp_path, "1")
    assert "answer one" in read_turn_memory(tmp_path, "turn_001")


def test_read_turn_memory_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        read_turn_memory(tmp_path, "../turn_001")


def test_read_turn_memory_tool_reads_latest_page(tmp_path):
    write_turn_memory(tmp_path, 1, "remember this", "important answer")
    set_session_dir(tmp_path)
    try:
        result = execute_tool("read_turn_memory", {"turn_id": "latest"})
    finally:
        set_session_dir(None)  # type: ignore[arg-type]

    assert "important answer" in result
