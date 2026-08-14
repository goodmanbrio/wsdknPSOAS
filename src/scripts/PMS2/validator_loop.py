"""Validator agent loop and submit_verdicts handler.

T0 scope: _handle_submit_verdicts only.
M4: full Validator agent loop (max 2 turns).
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel
from src.scripts.config import Config
from src.scripts.llm import LLMResponse, get_pms2_validator_llm
from src.scripts.PMS2.denom_reconcile import DENOM_FACTORS, _UNIT_ALIASES, _DENOM_ALIASES

_MAX_TURNS = 2

# ── Validator tool schemas (spec 17 § Validator tools) ──────────────

VALIDATOR_TOOLS = [
    {
        "name": "submit_verdicts",
        "description": (
            "Submit verdicts for ALL cells from this Leng hit. "
            "TERMINAL TOOL - exits the Validator. "
            "Each verdict is either 'write' (validated) or "
            "'reject' (failed). Handler performs per-cell: "
            "unit check against stencil row (from canonical "
            "units.json), denom normalization to absolute, "
            "lock + compare-and-swap write. "
            "Returns per-cell status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cell_id": {
                                "type": "string",
                                "description": "e.g. 'A1'",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["write", "reject"],
                            },
                            "value": {
                                "type": "number",
                                "description": (
                                    "Final value (as-written, NOT scaled). "
                                    "Same as Leng claimed for confirmed, "
                                    "corrected value if Leng misread. "
                                    "Required for action='write'."
                                ),
                            },
                            "denom": {
                                "type": "string",
                                "description": (
                                    "Denomination from canonical set: "
                                    "'units', 'k', 'mn', 'bn', 'tn', "
                                    "'%', 'bps'. "
                                    "Required for action='write'."
                                ),
                            },
                            "unit": {
                                "type": "string",
                                "description": (
                                    "Unit from canonical units.json: "
                                    "'USD', 'JPY', 'EUR', 'GBP', 'CNY', "
                                    "'float'. "
                                    "Required for action='write'."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": (
                                    "Why the value was rejected. "
                                    "Required for action='reject'."
                                ),
                            },
                        },
                        "required": ["cell_id", "action"],
                    },
                },
            },
            "required": ["verdicts"],
        },
    },
]


# ── submit_verdicts handler ─────────────────────────────────────────


def _handle_submit_verdicts(
    params: dict,
    job_stencil: dict,
    stencil_lock: threading.Lock,
    node_id: str,
) -> tuple[list[tuple], str]:
    """Process all verdicts from one Validator.

    Per verdict: unit check, denom normalize, lock, compare-and-swap.
    Returns (cell_outcomes, status_string).
      cell_outcomes = [(cell_id, outcome, reason), ...]
        outcome in {"written", "skipped", "rejected"}
      status_string = semicolon-joined per-cell summary (for LLM)
    """
    results = []
    cell_outcomes = []

    verdicts = params.get("verdicts")
    if not verdicts or not isinstance(verdicts, list):
        return [], "Error: verdicts missing or not a list"

    for verdict in verdicts:
        cell_id = verdict.get("cell_id", "")
        action = verdict.get("action", "")
        if not cell_id or not action:
            results.append(f"?: rejected (missing cell_id or action)")
            cell_outcomes.append(("?", "rejected", "missing cell_id or action"))
            continue

        if action == "reject":
            reason = verdict.get("reason", "")
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        # action == "write" — guard missing/malformed fields
        value = verdict.get("value")
        denom = verdict.get("denom")
        unit = verdict.get("unit")

        if not isinstance(value, (int, float)) or value is None:
            reason = f"non-numeric value: {repr(value)}"
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        if not denom or not isinstance(denom, str):
            reason = f"missing or invalid denom: {repr(denom)}"
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        if not unit or not isinstance(unit, str):
            reason = f"missing or invalid unit: {repr(unit)}"
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        # Guard: cell_id must exist in stencil values map
        if cell_id not in job_stencil["values"]:
            results.append(f"{cell_id}: error (cell not in stencil)")
            cell_outcomes.append((cell_id, "rejected", "cell not in stencil"))
            continue

        # Extract row number from cell_id
        row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        row = job_stencil["rows"].get(row_num)
        if row is None:
            results.append(f"{cell_id}: error (unknown row {row_num})")
            cell_outcomes.append((cell_id, "rejected", f"unknown row {row_num}"))
            continue

        # Step 0.5: alias normalization (unit + denom)
        unit = _UNIT_ALIASES.get(unit, unit)
        denom = _DENOM_ALIASES.get(denom, denom)

        # Step 1: unit check
        expected_unit = row.get("unit", "")
        if expected_unit and unit and unit != expected_unit:
            reason = f"unit mismatch: {unit} vs {expected_unit}"
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        # Step 2: denom normalize
        factor = DENOM_FACTORS.get(denom)
        if factor is None:
            reason = f"unknown denom '{denom}'"
            results.append(f"{cell_id}: rejected ({reason})")
            cell_outcomes.append((cell_id, "rejected", reason))
            continue

        absolute_value = value * factor

        # Step 3: lock + compare-and-swap
        with stencil_lock:
            if job_stencil["values"].get(cell_id) is not None:
                results.append(f"{cell_id}: skipped (already filled)")
                cell_outcomes.append((cell_id, "skipped", ""))
                continue
            job_stencil["values"][cell_id] = absolute_value
            job_stencil["sources"][cell_id] = node_id

        results.append(f"{cell_id}: written")
        cell_outcomes.append((cell_id, "written", ""))

    return cell_outcomes, "; ".join(results)


# ── Validator agent loop ────────────────────────────────────────────


def _build_validator_input(
    firm: str,
    node_id: str,
    chunk_text: str,
    leng_cells: dict,
    cell_descriptions: str,
    fiscal_calendar_text: str,
    job_stencil: dict,
) -> str:
    """Build the initial user message for the Validator."""
    lines = [f"Firm: {firm}"]
    lines.append(f"\nSource chunk (node_id: {node_id}):")
    lines.append(chunk_text)

    if fiscal_calendar_text:
        lines.append(f"\n{fiscal_calendar_text}")

    lines.append("\nCells claimed by Leng:")
    for cell_id, cell_data in leng_cells.items():
        # Find cell description from job_stencil
        row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        col = cell_id[: len(cell_id) - len(row_num)]
        row = job_stencil["rows"].get(row_num, {})
        col_idx = (
            job_stencil["col_letters"].index(col)
            if col in job_stencil["col_letters"]
            else -1
        )
        period = (
            job_stencil["periods"][col_idx]
            if 0 <= col_idx < len(job_stencil["periods"])
            else "?"
        )
        metric = row.get("metric", "?")
        timeframe = row.get("timeframe", "?")
        stencil_unit = row.get("unit", "?")

        lines.append(
            f"  {cell_id} = {metric}, {period}, {timeframe}, "
            f"stencil unit={stencil_unit}"
        )
        lines.append(
            f"    claimed: value={cell_data.get('value')}, "
            f"denom={cell_data.get('denom')}, "
            f"unit={cell_data.get('unit')}"
        )

    lines.append(
        "\nFor each cell: verify value appears in chunk, check "
        "period/timeframe (use fiscal calendar to match period "
        "labels to actual dates in chunk), check denom against "
        "chunk headers, check unit. Then call submit_verdicts "
        "with your verdict for ALL cells."
    )
    return "\n".join(lines)


def run_validator(
    firm: str,
    node_id: str,
    chunk_text: str,
    leng_cells: dict,
    cell_descriptions: str,
    fiscal_calendar_text: str,
    job_stencil: dict,
    stencil_lock: threading.Lock,
    config: Config,
    channel: ToolChannel,
) -> list[tuple]:
    """Run Validator agent loop for one Leng hit.

    Returns cell_outcomes: [(cell_id, outcome, reason), ...].
    Returns [] on max turns exhaustion (treated as miss).
    """
    valid_units = json.loads(
        (Path(__file__).parent / "hardcode_dependencies" / "units.json")
        .read_text()
    )
    valid_denoms = list(DENOM_FACTORS.keys())

    sys_prompt = load_sysprompt(
        "pms2_validator",
        config.pms2_validator_profile,
        valid_denoms=json.dumps(valid_denoms),
        valid_units=json.dumps(valid_units),
        fiscal_calendar=fiscal_calendar_text if fiscal_calendar_text else "Not available.",
    )

    initial_message = _build_validator_input(
        firm=firm,
        node_id=node_id,
        chunk_text=chunk_text,
        leng_cells=leng_cells,
        cell_descriptions=cell_descriptions,
        fiscal_calendar_text=fiscal_calendar_text,
        job_stencil=job_stencil,
    )

    backend = get_pms2_validator_llm(config)
    messages: list[dict] = [{"role": "user", "content": initial_message}]
    turn_counter = 0

    while turn_counter < _MAX_TURNS:
        response = backend.call_with_tools(
            messages=messages,
            system_prompt=sys_prompt,
            tools=VALIDATOR_TOOLS,
            label=f"PMS2-val-{firm}",
        )

        # end_turn without tool call = error
        if response.stop_reason == "end_turn":
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: you must call submit_verdicts. "
                    "Do not end your turn without calling a tool."
                ),
            })
            turn_counter += 1
            continue

        # Process tool calls
        for tc in response.tool_calls:
            if tc.name == "submit_verdicts":
                cell_outcomes, status_string = _handle_submit_verdicts(
                    tc.input, job_stencil, stencil_lock, node_id,
                )
                # Terminal tool — return cell_outcomes to LengCaller
                return cell_outcomes

            # Unknown tool
            messages.append(response.to_assistant_message())
            messages.append(
                LLMResponse.make_tool_results_message([{
                    "id": tc.id,
                    "name": tc.name,
                    "content": f"Error: unknown tool '{tc.name}'.",
                }])
            )
            turn_counter += 1
            break
        else:
            # No tool calls but stop_reason != end_turn — shouldn't happen
            turn_counter += 1

    # MAX_TURNS exhausted — treated as miss
    return []
