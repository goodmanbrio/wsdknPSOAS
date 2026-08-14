"""Durable, follow-up-oriented memory for completed orchestrator turns.

The transcript is an audit log. Turn memory is the compact, structured layer
the orchestrator can use to continue a conversation without treating a prior
summary page as primary evidence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MEMORY_DIRNAME = "memory"
INDEX_FILENAME = "index.json"
ACTIVE_CONTEXT_FILENAME = "active_context.md"
MAX_RESULT_CHARS = 2_000
MAX_ACTIVE_CONTEXT_CHARS = 6_000


def _turn_name(turn_id: int | str) -> str:
    """Normalize a numeric turn id to the on-disk ``turn_NNN`` convention."""
    if isinstance(turn_id, int):
        number = turn_id
    else:
        match = re.fullmatch(r"(?:turn[_-]?)?(\d+)", str(turn_id).strip(), re.I)
        if not match:
            raise ValueError("turn_id must be 'latest' or a numeric turn id")
        number = int(match.group(1))
    if number < 1:
        raise ValueError("turn_id must be positive")
    return f"turn_{number:03d}"


def _clip(value: Any, limit: int = MAX_RESULT_CHARS) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _safe_json(value: Any) -> Any:
    """Return JSON-friendly data without persisting large registry payloads."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    return str(value)


def _normalize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    return [
        {
            "id": call.get("id", ""),
            "name": call.get("name", ""),
            "input": _safe_json(call.get("input", {})),
        }
        for call in tool_calls
    ]


def _normalize_tool_results(tool_results: list[dict]) -> list[dict]:
    return [
        {
            "id": result.get("id", ""),
            "name": result.get("name", ""),
            "content": _clip(result.get("content", "")),
        }
        for result in tool_results
    ]


def _normalize_artifacts(stored_vars: list[tuple]) -> list[dict]:
    return [
        {
            "handle": handle,
            "description": description,
            "type": type(data).__name__,
        }
        for handle, description, data in stored_vars
    ]


def _extract_citations(answer: str) -> list[str]:
    """Collect citation lines for quick context, without rewriting them."""
    lines = []
    for line in answer.splitlines():
        if "[Source:" in line or line.startswith("## Bibliography"):
            lines.append(line.strip())
    return lines


def _build_markdown(record: dict) -> str:
    lines = [
        f"# Turn {record['turn_id']}",
        "",
        "## User request",
        "",
        record["user_query"],
        "",
        "## Assistant answer",
        "",
        record["assistant_answer"] or "_(no final text)_",
        "",
        "## Follow-up context",
        "",
        "This page is conversation memory. Re-check the cited original documents before making new factual claims.",
        "",
    ]

    citations = record.get("citations", [])
    if citations:
        lines.extend(["## Citations mentioned", "", *citations, ""])

    if record.get("tool_calls"):
        lines.extend(["## Tool activity", ""])
        for call in record["tool_calls"]:
            lines.append(f"- `{call['name']}`: `{json.dumps(call['input'], ensure_ascii=False)}`")
        lines.append("")

    if record.get("tool_results"):
        lines.extend(["### Result previews", ""])
        for result in record["tool_results"]:
            lines.extend([
                f"#### {result['name'] or 'tool'}",
                "",
                "```text",
                result["content"],
                "```",
                "",
            ])

    if record.get("artifacts"):
        lines.extend(["## Artifacts", ""])
        for artifact in record["artifacts"]:
            lines.append(
                f"- `{artifact['handle']}` — {artifact['description']} ({artifact['type']})"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _read_turn_record(memory_dir: Path, turn_id: str) -> dict | None:
    path = memory_dir / f"{turn_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _build_active_context(
    memory_dir: Path,
    latest_record: dict,
    index: dict,
) -> str:
    """Build a bounded cumulative context with the latest turn highlighted."""
    lines = [
        "## Cumulative session memory",
        f"Latest completed turn: {latest_record['turn_id']}",
        f"Detailed latest memory: `{MEMORY_DIRNAME}/{latest_record['turn_id']}.md`",
        "",
        "Completed turn history:",
    ]

    for entry in index.get("turns", []):
        turn_id = entry.get("turn_id", "")
        record = (
            latest_record
            if turn_id == latest_record.get("turn_id")
            else _read_turn_record(memory_dir, turn_id)
        )
        if not record:
            continue
        user_query = _clip(record.get("user_query", ""), 500)
        answer = _clip(record.get("assistant_answer", ""), 900)
        lines.extend([
            f"### {turn_id}",
            f"User: {user_query}",
            f"Assistant: {answer or '_(no final text)_'}",
            "",
        ])

    lines.extend([
        "Use this cumulative context to resolve follow-up references. The "
        "latest detailed page is available through read_turn_memory(latest).",
        "",
        "Treat the original cited documents—not this memory page—as evidence.",
    ])
    context = "\n".join(lines)
    if len(context) <= MAX_ACTIVE_CONTEXT_CHARS:
        return context.rstrip() + "\n"

    # Keep the latest turn and the instruction even when many turns exist.
    latest_block = "\n".join([
        f"### {latest_record['turn_id']}",
        f"User: {_clip(latest_record.get('user_query', ''), 500)}",
        f"Assistant: {_clip(latest_record.get('assistant_answer', ''), 900)}",
    ])
    prefix = "\n".join(lines[:5])
    suffix = "\n\nUse the cumulative context to resolve follow-up references. " \
        "Treat original cited documents—not this memory page—as evidence."
    available = max(0, MAX_ACTIVE_CONTEXT_CHARS - len(prefix) - len(suffix) - len(latest_block) - 8)
    history = "\n".join(lines[5:])
    history = history[-available:] if available else ""
    return f"{prefix}\n{history}\n\n{latest_block}{suffix}\n"


def write_turn_memory(
    session_dir: Path,
    turn_id: int,
    user_query: str,
    assistant_answer: str,
    *,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    stored_vars: list[tuple] | None = None,
) -> Path:
    """Write one complete turn and update the session memory index."""
    memory_dir = session_dir / MEMORY_DIRNAME
    memory_dir.mkdir(parents=True, exist_ok=True)
    name = _turn_name(turn_id)
    record = {
        "turn_id": name,
        "user_query": user_query,
        "assistant_answer": assistant_answer,
        "citations": _extract_citations(assistant_answer),
        "tool_calls": _normalize_tool_calls(tool_calls or []),
        "tool_results": _normalize_tool_results(tool_results or []),
        "artifacts": _normalize_artifacts(stored_vars or []),
    }

    json_path = memory_dir / f"{name}.json"
    markdown_path = memory_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    markdown_path.write_text(_build_markdown(record), encoding="utf-8")

    index_path = memory_dir / INDEX_FILENAME
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = {"turns": []}
    else:
        index = {"turns": []}

    turns = [entry for entry in index.get("turns", []) if entry.get("turn_id") != name]
    turns.append({
        "turn_id": name,
        "user_query": user_query,
        "markdown": f"{MEMORY_DIRNAME}/{name}.md",
        "json": f"{MEMORY_DIRNAME}/{name}.json",
    })
    turns.sort(key=lambda entry: entry["turn_id"])
    index = {"latest_turn": name, "turns": turns}
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (memory_dir / ACTIVE_CONTEXT_FILENAME).write_text(
        _build_active_context(memory_dir, record, index),
        encoding="utf-8",
    )
    return markdown_path


def load_active_context(session_dir: Path) -> str:
    path = session_dir / MEMORY_DIRNAME / ACTIVE_CONTEXT_FILENAME
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_turn_memory(session_dir: Path, turn_id: str = "latest") -> str:
    """Read a full turn memory page for the orchestrator's follow-up tool."""
    memory_dir = session_dir / MEMORY_DIRNAME
    if turn_id.strip().lower() == "latest":
        index_path = memory_dir / INDEX_FILENAME
        if not index_path.exists():
            return "No completed turn memory exists yet."
        index = json.loads(index_path.read_text(encoding="utf-8"))
        turn_id = index.get("latest_turn", "")
        if not turn_id:
            return "No completed turn memory exists yet."

    name = _turn_name(turn_id)
    path = memory_dir / f"{name}.md"
    if not path.exists():
        return f"Turn memory '{name}' does not exist."
    return path.read_text(encoding="utf-8")
