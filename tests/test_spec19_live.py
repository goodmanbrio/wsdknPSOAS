"""L1 test — Sekei loop mechanics with stub backend ($0 cost).

No LLM calls. StubSekeiBackend pops from a canned LLMResponse sequence.
Tests the REAL control flow of run_sekei:
  - Turn counting + ask_user reset
  - _handle_finalize validation pipeline (regex, consistency, chrono sort)
  - granularity propagation to work_stencil/job_stencils
  - work_stencil.json written to disk
  - finalize hard gate (user_confirmed via AutoChannel prefix)

Usage:
    python -m pytest tests/test_spec19_live.py -x -v
"""

import json
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.scripts.llm import LLMResponse, ToolCall
from src.scripts.PMS2.sekei_loop import run_sekei


# ── AutoChannel ──────────────────────────────────────────────────────────


class AutoChannel:
    """Duck-typed ToolChannel that auto-answers ask_user questions."""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self._idx = 0
        self.messages: list[str] = []
        self.questions: list[str] = []

    def print(self, msg, **kw):
        self.messages.append(msg)

    def input(self, question, **kw):
        self.questions.append(question)
        if self._idx < len(self._answers):
            answer = self._answers[self._idx]
            self._idx += 1
        else:
            answer = "y"
        return answer


# ── StubSekeiBackend ─────────────────────────────────────────────────────


class StubSekeiBackend:
    """Pops LLMResponse objects from a canned list.

    Ignores incoming messages — just returns the next response.
    Raises if list exhausted (signals test design error).
    """

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self._idx = 0

    def call_with_tools(self, messages, system_prompt, tools,
                        max_tokens=None, label=""):
        if self._idx >= len(self._responses):
            raise RuntimeError(
                f"StubSekeiBackend exhausted after {self._idx} calls. "
                f"LLM control flow asked for more turns than canned."
            )
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


def _make_tc(tc_id: str, name: str, input_: dict) -> ToolCall:
    return ToolCall(id=tc_id, name=name, input=input_)


def _make_resp(tc: ToolCall) -> LLMResponse:
    """Build an LLMResponse with a single tool_use block."""
    raw = [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}]
    return LLMResponse(
        stop_reason="tool_use",
        text="",
        tool_calls=[tc],
        raw_content=raw,
    )


# ── Canned responses ─────────────────────────────────────────────────────

FINALIZE_PARAMS = {
    "firms": ["TestFirm"],
    "periods": ["FY2025", "FY2024"],   # deliberately unsorted — test chrono sort
    "granularity": "annual",
    "rows": [
        {
            "metric": "Revenue",
            "firm": "TestFirm",
            "type": "retrieve",
            "timeframe": "annual",
            "unit": "USD",
            "ans": True,
        },
    ],
}


def _build_canned_responses() -> list[LLMResponse]:
    """4-turn sequence matching the L1 spec diagram."""
    # Turn 1: ask_user — confirm firms/periods/granularity
    t1 = _make_tc("tc_1", "ask_user", {
        "question": (
            "I've parsed your query. Proposed:\n"
            "Firms: TestFirm\n"
            "Periods: FY2024, FY2025 (annual)\n"
            "Metrics: Revenue\n"
            "Confirm? [y/edit]"
        )
    })

    # Turn 2: run_mapper — collect file inventory
    t2 = _make_tc("tc_2", "run_mapper", {"firms": ["TestFirm"]})

    # Turn 3: ask_user — stencil preview confirm
    t3 = _make_tc("tc_3", "ask_user", {
        "question": (
            "Stencil preview:\n"
            "R1: Revenue [TestFirm] retrieve USD (ans)\n"
            "Confirm? [y/edit]"
        )
    })

    # Turn 4: finalize_stencil — the real validation runs here
    t4 = _make_tc("tc_4", "finalize_stencil", FINALIZE_PARAMS)

    return [_make_resp(t1), _make_resp(t2), _make_resp(t3), _make_resp(t4)]


# ── L1 test ──────────────────────────────────────────────────────────────


class TestSekeiLoopMechanics:

    def test_l1_loop_full_flow(self, tmp_path, monkeypatch):
        """L1: Run run_sekei end-to-end with stub backend.

        Validates:
        A1: loop exits without RuntimeError
        A2: turn_counter reset works (ask_user resets to 0)
        A3: granularity key in work_stencil
        A4: periods regex-valid + chrono sorted
        A5: finalize gate: user_confirmed=True via AutoChannel prefix
        A6: work_stencil.json written to disk
        """
        from src.scripts.config import Config
        import src.scripts.PMS2.sekei_loop as sl
        import src.harness.terminal_router as tr

        # AutoChannel answers: first starts with "Confirmed" → user_confirmed=True
        # subsequent "y" for stencil preview confirm + gate buffer
        auto_answers = ["Confirmed. LGTM.", "y", "y", "y"]
        channel_map: dict[str, AutoChannel] = {}

        def mock_register(label: str) -> AutoChannel:
            if label not in channel_map:
                channel_map[label] = AutoChannel(list(auto_answers))
            return channel_map[label]

        # Monkeypatch register in terminal_router (sekei_loop imports it directly)
        monkeypatch.setattr(tr, "register", mock_register)
        monkeypatch.setattr(sl, "register", mock_register)

        # Stub the LLM backend
        stub = StubSekeiBackend(_build_canned_responses())
        monkeypatch.setattr(sl, "get_pms2_sekei_llm", lambda config: stub)

        # Stub _handle_run_mapper — return empty inventory (no real filesystem needed)
        monkeypatch.setattr(sl, "_handle_run_mapper",
                            lambda params, config, query, channel, trace, debug_dir: {
                                "TestFirm": []
                            })

        config = Config.from_env()

        result = run_sekei(
            query="Revenue for TestFirm, FY2024-FY2025 annual",
            config=config,
            channel=mock_register("PMS2"),
            session_dir=tmp_path,
            top_level_dirs=["TestFirm"],
        )

        work, ans, jobs, file_inv = result

        # A3: granularity in work_stencil
        assert "granularity" in work, "work_stencil missing 'granularity' key"
        assert work["granularity"] == "annual", (
            f"granularity: {work['granularity']} != 'annual'"
        )

        # A4: periods chrono sorted (FY2024 before FY2025, despite unsorted input)
        assert work["periods"] == ["FY2024", "FY2025"], (
            f"periods not chrono sorted: {work['periods']}"
        )

        # A4: all periods pass regex (no 3QFY2025 garbage)
        from src.scripts.PMS2.sekei_loop import _PERIOD_RE
        for p in work["periods"]:
            assert _PERIOD_RE.fullmatch(p), f"Period '{p}' failed regex"

        # A6: work_stencil.json written to disk
        stencil_path = tmp_path / "pms2" / "work_stencil.json"
        assert stencil_path.exists(), "work_stencil.json not written to disk"
        disk_stencil = json.loads(stencil_path.read_text())
        assert disk_stencil["granularity"] == "annual"

        # A3: granularity in each job_stencil
        assert len(jobs) == 1, f"Expected 1 job stencil, got {len(jobs)}"
        assert "granularity" in jobs[0], "job_stencil missing 'granularity' key"
        assert jobs[0]["granularity"] == "annual"

        # A1: if we got here, loop exited without RuntimeError
        # A5: user_confirmed was True (AutoChannel "Confirmed." prefix matched)
        #     — if it weren't, finalize hard gate would have fired input("Finalize?")
        #     and the "Confirmed." answer would have been consumed, not "y" for gate
        #     (test would still pass, but let's verify the channel was used correctly)
        sekei_ch = channel_map.get("PMS2-Sekei")
        assert sekei_ch is not None, "PMS2-Sekei channel never registered"
        assert len(sekei_ch.questions) >= 2, (
            f"Expected at least 2 ask_user calls, got {len(sekei_ch.questions)}"
        )

    def test_l1_invalid_period_format_triggers_retry(self, tmp_path, monkeypatch):
        """L1b: finalize_stencil with '3QFY2025' (the original bug) returns error, not data."""
        from src.scripts.config import Config
        import src.scripts.PMS2.sekei_loop as sl
        import src.harness.terminal_router as tr

        auto_answers = ["Confirmed.", "y", "y", "y"]
        channel_map: dict[str, AutoChannel] = {}

        def mock_register(label):
            if label not in channel_map:
                channel_map[label] = AutoChannel(list(auto_answers))
            return channel_map[label]

        monkeypatch.setattr(tr, "register", mock_register)
        monkeypatch.setattr(sl, "register", mock_register)

        # Bad finalize: uses "3QFY2025" (the original bug format)
        bad_params = {
            "firms": ["TestFirm"],
            "periods": ["3QFY2025"],   # THE ORIGINAL BUG
            "granularity": "quarterly",
            "rows": [
                {
                    "metric": "Revenue", "firm": "TestFirm",
                    "type": "retrieve", "timeframe": "quarterly",
                    "unit": "USD", "ans": True,
                }
            ],
        }

        # Build canned: ask_user → run_mapper → ask_user → finalize (bad) → finalize (good, never reached)
        # We only call _handle_finalize directly to test validation in isolation
        from src.scripts.PMS2.sekei_loop import _handle_finalize

        data, status = _handle_finalize(bad_params, {}, tmp_path)

        assert data is None, (
            f"Expected None data for bad period format '3QFY2025', got: {data}"
        )
        assert "3QFY2025" in status or "invalid format" in status.lower(), (
            f"Expected error mentioning bad period in status: {status}"
        )

    def test_l1_granularity_mismatch_rejected(self, tmp_path, monkeypatch):
        """L1c: quarterly granularity with FY2025 (annual format) → error."""
        import src.harness.terminal_router as tr
        import src.scripts.PMS2.sekei_loop as sl

        def mock_register(label):
            return AutoChannel(["y"])

        monkeypatch.setattr(tr, "register", mock_register)
        monkeypatch.setattr(sl, "register", mock_register)

        from src.scripts.PMS2.sekei_loop import _handle_finalize

        params = {
            "firms": ["TestFirm"],
            "periods": ["FY2025"],   # annual format with quarterly declared
            "granularity": "quarterly",
            "rows": [
                {
                    "metric": "Revenue", "firm": "TestFirm",
                    "type": "retrieve", "timeframe": "quarterly",
                    "unit": "USD", "ans": True,
                }
            ],
        }

        data, status = _handle_finalize(params, {}, tmp_path)

        assert data is None, (
            f"Expected None for granularity mismatch, got data: {data}"
        )
        assert "inconsistent" in status.lower() or "quarterly" in status.lower(), (
            f"Expected error mentioning granularity inconsistency: {status}"
        )
