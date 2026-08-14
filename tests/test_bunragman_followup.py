"""Tests for unrestricted research fan-out and targeted follow-ups."""

from unittest.mock import patch
from pathlib import Path

from src.config import Config
from src.harness.execute_tool import (
    begin_turn,
    execute_tool,
    set_config,
    set_session_dir,
)
from src.harness.system_prompt import TOOL_DEFINITIONS
from src.scripts.bunragman.sekei import SOURCES_SCHEMA, run_bunragman_sekei
from src.harness.turn_memory import write_turn_memory


class PrintChannel:
    def __init__(self):
        self.messages = []

    def print(self, message, **kwargs):
        self.messages.append(message)

    def input(self, question, **kwargs):
        raise AssertionError("mocked research flow should not ask for input")


def _research_tool_definition():
    return next(tool for tool in TOOL_DEFINITIONS if tool["name"] == "run_research")


def test_source_group_schema_has_no_osha_run_cap():
    sources = SOURCES_SCHEMA["properties"]["sources"]

    assert "maxItems" not in sources


def test_entrypoint_fans_out_to_all_confirmed_source_groups(tmp_path):
    sources = {
        "Source 1": ["one.md"],
        "Source 2": ["two.md"],
        "Source 3": ["three.md"],
        "Source 4": ["four.md"],
    }
    channel = PrintChannel()

    with patch(
        "src.scripts.bunragman.sekei.zako_discover",
        return_value=("query", {"dir": ["one.md"]}),
    ), patch(
        "src.scripts.bunragman.sekei._run_group_loop",
        return_value=sources,
    ), patch(
        "src.scripts.bunragman.sekei.call_bunragman",
        side_effect=[f"summary_{i}.md" for i in range(4)],
    ) as call_agent, patch(
        "src.scripts.bunragman.sekei._reconcile",
        return_value="answer",
    ) as reconcile:
        answer = run_bunragman_sekei("query", Config(), channel, tmp_path)

    assert answer == "answer"
    assert call_agent.call_count == 4
    assert len(reconcile.call_args.args[1]) == 4
    assert not any("hard limit" in message for message in channel.messages)


def test_research_tool_starts_a_normal_research_pass_without_follow_up_flag(
    tmp_path,
):
    set_session_dir(tmp_path)
    set_config(Config())
    begin_turn(False)
    try:
        with patch(
            "src.scripts.bunragman.sekei.run_bunragman_sekei",
            return_value="answer",
        ) as run_sekei:
            result = execute_tool(
                "run_research",
                {"question": "What information is missing?"},
            )
    finally:
        set_session_dir(None)  # type: ignore[arg-type]
        set_config(None)  # type: ignore[arg-type]

    assert "answer" in result
    assert run_sekei.call_args.kwargs["query"] == "What information is missing?"
    assert "reuse_summary_paths" not in run_sekei.call_args.kwargs


def test_follow_up_research_requires_turn_memory_preflight(tmp_path):
    set_session_dir(tmp_path)
    set_config(Config())
    begin_turn(True)
    try:
        with patch(
            "src.scripts.bunragman.sekei.run_bunragman_sekei",
            return_value="answer",
        ) as run_sekei:
            result = execute_tool(
                "run_research",
                {"question": "What is missing from the prior answer?"},
            )

        assert "gated" in result
        run_sekei.assert_not_called()

        write_turn_memory(tmp_path, 1, "first question", "first answer")
        write_turn_memory(tmp_path, 2, "second question", "latest answer")
        memory = execute_tool(
            "read_turn_memory",
            {"turn_id": "1"},
        )
        assert "first answer" in memory

        with patch(
            "src.scripts.bunragman.sekei.run_bunragman_sekei",
            return_value="answer",
        ) as run_sekei:
            result = execute_tool(
                "run_research",
                {
                    "question": "What specific evidence is missing?",
                    "missing_information": "Evidence supporting the key claim",
                },
            )

        assert "gated" in result
        run_sekei.assert_not_called()

        memory = execute_tool(
            "read_turn_memory",
            {"turn_id": "latest"},
        )
        assert "latest answer" in memory

        with patch(
            "src.scripts.bunragman.sekei.run_bunragman_sekei",
            return_value="targeted answer",
        ) as run_sekei:
            result = execute_tool(
                "run_research",
                {
                    "question": "What specific evidence is missing?",
                    "missing_information": "Evidence supporting the key claim",
                },
            )

        assert "targeted answer" in result
        run_sekei.assert_called_once()
    finally:
        set_session_dir(None)  # type: ignore[arg-type]
        set_config(None)  # type: ignore[arg-type]
        begin_turn(False)


def test_run_research_guides_summary_first_targeted_follow_ups():
    definition = _research_tool_definition()
    description = definition["description"]
    schema = definition["input_schema"]

    assert "read_turn_memory" in description
    assert "read_session_md" in description
    assert "existing artifacts" in description
    assert "without calling run_research" in description
    assert "targeted" in description
    assert "parallel" in description
    assert "must first call" in description
    assert "follow_up" not in schema["properties"]
    assert "missing_information" in schema["properties"]
    assert schema["required"] == ["question"]


def test_orchestrator_sysprompt_makes_artifact_first_decision_explicit():
    prompt_path = (
        Path(__file__).parents[1]
        / "sysprompts"
        / "orchestrator"
        / "deepseek_v4pro_orchestrator.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "## Follow-up research decision tree" in prompt
    assert "read_turn_memory with turn_id=\"latest\"" in prompt
    assert "read_session_md" in prompt
    assert "do not\n   call run_research" in prompt
    assert "exact missing_information" in prompt
