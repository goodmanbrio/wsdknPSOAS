"""
agent_loop.py — The while loop that drives the orchestrator.

Calls client.messages.create(), checks stop_reason, dispatches tool
calls to execute_tool, accumulates messages, enforces guard rails.

Usage:
    from src.harness.agent_loop import run_harness
    run_harness("Chart Best Buy and Amcor gross margins FY2022-2023")
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
from rich.panel import Panel

from src.harness.terminal_router import register, console, _router
from src.harness.execute_tool import execute_tool, set_session_dir, reset_counters
from src.harness.opaque_registry import registry
from src.harness.system_prompt import SYSTEM_PROMPT, TOOL_DEFINITIONS
from src.config import Config

orchestrator_out = register("ORCHESTRATOR")

MAX_TOOL_CALLS_PER_TURN = 5
MAX_TURNS_BEFORE_CHECKPOINT = 6


# ── Transcript helper ─────────────────────────────────────────────────

def _dump_turn(
    path: Path, turn_num: int, response, tool_results: list | None
) -> None:
    """Append one turn to the transcript file."""
    with open(path, "a") as f:
        f.write(f"\n## Turn {turn_num}\n\n")
        for block in response.content:
            if block.type == "text":
                f.write(f"{block.text}\n\n")
            elif block.type == "tool_use":
                args_str = json.dumps(block.input, ensure_ascii=False)
                f.write(f"**Tool call:** `{block.name}({args_str})`\n")
        if tool_results:
            f.write("\n")
            for tr in tool_results:
                content = tr["content"]
                f.write(f"**Result:** `{content}`\n")
        f.write("\n")


# ── Parallel tool dispatch ────────────────────────────────────────────

def _safe_execute(tb) -> str:
    """Execute one tool call, catch exceptions."""
    try:
        return execute_tool(tb.name, tb.input)
    except Exception as exc:
        return f"Error in {tb.name}: {type(exc).__name__}: {exc}"


def _dispatch_parallel(tool_blocks: list) -> list[dict]:
    """Execute tool calls in parallel. Returns tool_results in order."""
    results = [None] * len(tool_blocks)

    with ThreadPoolExecutor(max_workers=len(tool_blocks)) as pool:
        future_to_idx = {
            pool.submit(_safe_execute, tb): i
            for i, tb in enumerate(tool_blocks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            tb = tool_blocks[idx]
            results[idx] = {
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": future.result(),
            }

    return results


# ── Main harness ──────────────────────────────────────────────────────

def run_harness(user_query: str) -> None:
    client = anthropic.Anthropic()
    registry.reset()
    reset_counters()

    # Load orchestrator model from config
    config = Config.from_env()
    orch_profile = config.get_llm_profile("anthropic_orchestrator")
    model = orch_profile["model"]

    # Session dir + transcript
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    session_dir = Path(f"temp/sessions/{ts}")
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.md"
    transcript.write_text(
        f"# PSOAS Session {ts}\n\n## User\n\n{user_query}\n"
    )

    set_session_dir(session_dir)

    # ── Welcome banner (spec 10) ──────────────────────────────
    console.print(Panel(
        "[bold cyan]PSOAS[/bold cyan] — "
        "Poony Sophomore Orchestrated Analyst Strapon",
        subtitle="[dim]type answers when prompted[/dim]",
        border_style="cyan",
    ))

    messages = [{"role": "user", "content": user_query}]
    turn_counter = 0
    transcript_turn = 0

    while True:
        # ── LLM call with spinner ─────────────────────────────
        _router.start_spinner("ORCHESTRATOR")
        try:
            response = client.messages.create(
                model=model,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                max_tokens=4096,
            )
        finally:
            _router.stop_spinner()

        # ── end_turn ──────────────────────────────────────────
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    orchestrator_out.print(block.text)
            messages.append(
                {"role": "assistant", "content": response.content}
            )
            transcript_turn += 1
            _dump_turn(transcript, transcript_turn, response, None)
            break

        # ── tool_use ──────────────────────────────────────────
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_blocks:
            # Handle truncated response (max_tokens)
            if response.stop_reason == "max_tokens":
                orchestrator_out.print(
                    "Response truncated (max_tokens). Retrying..."
                )
                messages.append(
                    {"role": "assistant", "content": response.content}
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was truncated (max_tokens). "
                        "Please continue or reduce tool calls."
                    ),
                })
                turn_counter += 1
                transcript_turn += 1
                _dump_turn(transcript, transcript_turn, response, None)
                continue
            break

        # Print LLM text blocks (commentary alongside tool calls)
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                orchestrator_out.print(block.text)

        # Guard rail: max tool calls per turn
        if len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": (
                        f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool calls "
                        f"per turn exceeded ({len(tool_blocks)} requested)."
                    ),
                }
                for tb in tool_blocks
            ]
            console.print(
                f"[bold red]\\[ERROR][/bold red] "
                f"Max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
                f"exceeded ({len(tool_blocks)} requested)."
            )
        else:
            # Parallel dispatch
            tool_results = _dispatch_parallel(tool_blocks)

        # Accumulate messages
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        # Transcript
        turn_counter += 1
        transcript_turn += 1
        _dump_turn(transcript, transcript_turn, response, tool_results)

        # Guard rail: checkpoint
        if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
            messages.append({
                "role": "user",
                "content": (
                    "You have run 6 turns. Summarize what you have done "
                    "so far and what remains."
                ),
            })

            _router.start_spinner("ORCHESTRATOR")
            try:
                summary = client.messages.create(
                    model=model,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=[],
                    max_tokens=1024,
                )
            finally:
                _router.stop_spinner()

            messages.append(
                {"role": "assistant", "content": summary.content}
            )

            text = ""
            for block in summary.content:
                if hasattr(block, "text"):
                    text += block.text

            console.print(Panel(
                text,
                title="[bold]CHECKPOINT — 6 turns reached[/bold]",
                border_style="yellow",
            ))
            answer = orchestrator_out.input("Continue? [y/n]")

            # Dump checkpoint to transcript
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
                break

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
