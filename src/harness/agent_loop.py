"""
agent_loop.py — Outer REPL + inner agent loop.

Wraps the orchestrator while-loop in an interactive REPL. Accepts
queries, runs the inner agent loop to completion (end_turn), then
re-prompts. Session state persists across follow-ups.

Usage:
    from src.harness.agent_loop import run_harness
    run_harness()                                      # pure REPL
    run_harness(first_query="Chart Best Buy margins")  # first query pre-loaded
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Project root — absolute anchor for session_dir and debug_dir
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

from rich.panel import Panel

from src.harness.terminal_router import register, console, _router, harvest_logs
from src.harness.execute_tool import execute_tool, set_session_dir, set_debug_dir, reset_counters
from src.harness.opaque_registry import registry
from src.harness.system_prompt import TOOL_DEFINITIONS
from src.harness.sysprompts import load_sysprompt
from src.config import Config
from src.llm import get_orchestrator_llm, LLMResponse, ToolCall

orchestrator_out = register("ORCHESTRATOR")

MAX_TOOL_CALLS_PER_TURN = 5
MAX_TURNS_BEFORE_CHECKPOINT = 6


# ── Transcript helper ─────────────────────────────────────────────────

def _dump_turn(
    path: Path,
    turn_num: int,
    response: LLMResponse,
    tool_results: list[dict] | None,
    tool_logs: dict[str, list[str]] | None = None,
    stored_vars: list | None = None,
) -> None:
    """Append one turn to the transcript file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## Turn {turn_num}\n\n")

        for block in response.raw_content:
            if block["type"] == "text":
                f.write(f"{block['text']}\n\n")
            elif block["type"] == "tool_use":
                args_str = json.dumps(block["input"], ensure_ascii=False)
                f.write(f"**Tool call:** `{block['name']}({args_str})`\n")

        # Stream A: tool operational logs
        if tool_logs:
            f.write("\n### Tool Logs\n")
            for label, lines in tool_logs.items():
                f.write(f"\n#### {label}\n```\n")
                for line in lines:
                    f.write(f"{line}\n")
                f.write("```\n")

        # Stream C: tool result strings (sent to LLM)
        if tool_results:
            f.write("\n")
            for tr in tool_results:
                content = tr["content"]
                f.write(f"**Result:** `{content}`\n")

        # Stream B: stored variable data
        if stored_vars:
            f.write("\n### Stored Variables\n\n")
            for handle, desc, data in stored_vars:
                f.write(f"**{handle}** ({desc}):\n```json\n")
                f.write(json.dumps(
                    data, indent=2, ensure_ascii=False, default=str
                ))
                f.write("\n```\n\n")

        f.write("\n")


# ── Parallel tool dispatch ────────────────────────────────────────────

def _safe_execute(tc: ToolCall) -> str:
    """Execute one tool call, catch exceptions."""
    try:
        return execute_tool(tc.name, tc.input)
    except Exception as exc:
        return f"Error in {tc.name}: {type(exc).__name__}: {exc}"


def _dispatch_parallel(tool_calls: list[ToolCall]) -> list[dict]:
    """Execute tool calls in parallel. Returns result dicts in order.

    Result format: [{"id": "tc_1", "name": "run_pms1", "content": "..."}]
    """
    results = [None] * len(tool_calls)

    with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
        future_to_idx = {
            pool.submit(_safe_execute, tc): i
            for i, tc in enumerate(tool_calls)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            tc = tool_calls[idx]
            results[idx] = {
                "id": tc.id,
                "name": tc.name,
                "content": future.result(),
            }

    return results


# ── Main harness ──────────────────────────────────────────────────────

def run_harness(
    first_query: str | None = None,
    config: Config | None = None,
) -> None:
    registry.reset()
    reset_counters()

    config = config or Config.from_env()
    system_prompt = load_sysprompt("orchestrator", config.orchestrator_profile)
    backend = get_orchestrator_llm(config)

    # Session dir + transcript
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = PROJECT_ROOT / "temp" / "sessions" / ts
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.md"
    transcript.write_text(f"# PSOAS Session {ts}\n")

    # Debug trace output dir (cross-referenced by timestamp)
    debug_dir = PROJECT_ROOT / "tests" / "debug" / ts
    debug_dir.mkdir(parents=True, exist_ok=True)

    set_session_dir(session_dir)
    set_debug_dir(debug_dir)

    # Propagate config to execute_tool dispatchers (PTECA reads it)
    from src.harness.execute_tool import set_config
    set_config(config)

    # ── Welcome banner (spec 10) ──────────────────────────────
    console.print(Panel(
        "[bold cyan]PSOAS[/bold cyan] — "
        "Poony Sophomore Orchestrated Analyst Strapon",
        subtitle="[dim]type answers when prompted[/dim]",
        border_style="cyan",
    ))

    messages = []
    transcript_turn = 0
    is_first_input = True

    # ── Outer REPL (spec 11) ─────────────────────────────────
    while True:
        # ── Prompt for user input ─────────────────────────────
        if first_query is not None:
            user_input = first_query
            first_query = None  # consumed
        else:
            user_input = orchestrator_out.input("")

            if not user_input.strip():
                if _router._is_dead:
                    break  # EOF (Ctrl+D)
                orchestrator_out.print(
                    "Type a query, or 'exit' to quit."
                )
                continue

            if user_input.strip().lower() == "exit":
                break

        # ── Append user message + transcript ──────────────────
        messages.append({"role": "user", "content": user_input})

        with open(transcript, "a") as f:
            if is_first_input:
                f.write(f"\n## User\n\n{user_input}\n")
            else:
                f.write(f"\n## User (follow-up)\n\n{user_input}\n")

        is_first_input = False
        turn_counter = 0

        # ── Inner agent loop (spec 02) ────────────────────────
        while True:
            # ── LLM call with spinner ─────────────────────────
            _router.start_spinner("ORCHESTRATOR")
            try:
                response = backend.call_with_tools(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=TOOL_DEFINITIONS,
                )
            finally:
                _router.stop_spinner()

            # ── end_turn ──────────────────────────────────────
            if response.stop_reason == "end_turn":
                if response.text:
                    orchestrator_out.print(response.text, markdown=True)
                messages.append(response.to_assistant_message())
                transcript_turn += 1
                _dump_turn(transcript, transcript_turn, response, None)
                break  # → outer REPL re-prompts

            # ── tool_use ──────────────────────────────────────
            tool_calls = response.tool_calls

            if not tool_calls:
                if response.stop_reason == "max_tokens":
                    orchestrator_out.print(
                        "Response truncated (max_tokens). Retrying..."
                    )
                    messages.append(response.to_assistant_message())
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response was truncated "
                            "(max_tokens). Please continue or reduce "
                            "tool calls."
                        ),
                    })
                    turn_counter += 1
                    transcript_turn += 1
                    _dump_turn(
                        transcript, transcript_turn, response, None
                    )
                    continue
                break

            # Print LLM text
            if response.text.strip():
                orchestrator_out.print(response.text, markdown=True)

            # Guard rail: max tool calls per turn
            if len(tool_calls) > MAX_TOOL_CALLS_PER_TURN:
                tool_results_msg = LLMResponse.make_tool_results_message([
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "content": (
                            f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool "
                            f"calls per turn exceeded "
                            f"({len(tool_calls)} requested)."
                        ),
                    }
                    for tc in tool_calls
                ])
                console.print(
                    f"[bold red]\\[ERROR][/bold red] "
                    f"Max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
                    f"exceeded ({len(tool_calls)} requested)."
                )
            else:
                raw_results = _dispatch_parallel(tool_calls)
                tool_results_msg = LLMResponse.make_tool_results_message(
                    raw_results
                )

            # Harvest streams A + B after dispatch completes
            tool_logs = harvest_logs()
            stored_vars = registry.harvest_recent()

            # Accumulate messages
            messages.append(response.to_assistant_message())
            messages.append(tool_results_msg)

            # Transcript
            turn_counter += 1
            transcript_turn += 1
            _dump_turn(
                transcript, transcript_turn, response,
                tool_results_msg["content"],
                tool_logs=tool_logs, stored_vars=stored_vars,
            )

            # Guard rail: checkpoint
            if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have run 6 turns. Summarize what you have "
                        "done so far and what remains."
                    ),
                })

                _router.start_spinner("ORCHESTRATOR")
                try:
                    summary = backend.call_with_tools(
                        messages=messages,
                        system_prompt=system_prompt,
                        tools=[],
                        max_tokens=1024,
                    )
                finally:
                    _router.stop_spinner()

                messages.append(summary.to_assistant_message())
                text = summary.text

                console.print(Panel(
                    text,
                    title="[bold]CHECKPOINT — 6 turns reached[/bold]",
                    border_style="yellow",
                ))
                answer = orchestrator_out.input("Continue? [y/n]")

                transcript_turn += 1
                with open(transcript, "a") as f:
                    f.write(
                        f"\n## Turn {transcript_turn} (CHECKPOINT)\n\n"
                    )
                    f.write(f"**Summary:** {text}\n\n")
                    f.write(f"**Continue?** {answer}\n\n")

                if answer.strip().lower() in ("y", "yes"):
                    turn_counter = 0
                    messages.append({
                        "role": "user",
                        "content": "User confirmed: continue working.",
                    })
                else:
                    break  # → outer REPL re-prompts

    # ── Session end summary (spec 10) ─────────────────────────
    summary_lines = []
    for handle, entry in registry._registry.items():
        summary_lines.append(f"  {handle}: {entry['description']}")

    console.print(Panel(
        "\n".join([
            "[bold]Session complete.[/bold]",
            f"Transcript: {transcript}",
            "",
            "Variables stored:",
            *(summary_lines if summary_lines else ["  (none)"]),
        ]),
        border_style="green",
    ))
