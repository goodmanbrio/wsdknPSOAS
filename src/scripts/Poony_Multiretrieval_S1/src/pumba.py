"""
pumba.py — PUMBA: Poony Ultimate Money Burning Acquisition.

LLM-reasoned chunk retrieval fallback for when PTO's deterministic
BM25/cosine retrieval fails. Three-tier hierarchy of LLM workers
(Dailo -> Gulei -> Leng) navigates the raw data directory, finds the
right table chunks by reasoning about filenames and section names,
and returns real docstore node_ids to the existing pto_judge for
authoritative value extraction.

PUMBA is NOT a PSOAS-level tool. It is a PMS1-internal fallback
called by orchestrator.py.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llama_index.core import VectorStoreIndex

from src.config import Config
from src.harness.terminal_router import register, ToolChannel
from src.pto import PTOBatchRequest

# ── Constants ────────────────────────────────────────────────────────────

MAX_DAILO_TURNS = 10
_MAX_CHUNK_CHARS_IN_TOOL_RESULT = 3000

# ── Dailo tools (Anthropic tool-use schema) ──────────────────────────────

DAILO_TOOLS = [
    {
        "name": "list_dir",
        "description": (
            "List files and subdirectories at a path under data/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path under data/. Use '' for root."
                    ),
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "spawn_gulei",
        "description": (
            "Survey files for relevant table chunks. Runs one Gulei "
            "worker per file in parallel. Returns per-file results "
            "with full chunk text for hits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filenames to survey (max 6). e.g. "
                        "['BESTBUY_2023_10K.md', 'BESTBUY_2022_10K.md']"
                    ),
                }
            },
            "required": ["files"],
        },
    },
    {
        "name": "report_results",
        "description": (
            "Report final chunk results. Call this when you have found "
            "chunks or exhausted all files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Node IDs of best chunks to send to Judge. "
                        "Max 3. Empty if exhausted."
                    ),
                },
                "exhausted": {
                    "type": "boolean",
                    "description": (
                        "True if all files have been surveyed with "
                        "no results."
                    ),
                },
            },
            "required": ["node_ids", "exhausted"],
        },
    },
]

# ── System prompts ───────────────────────────────────────────────────────

DAILO_SYSTEM_TEMPLATE = """\
You are Dailo -- a retrieval coordinator. You navigate a financial \
data directory to find table chunks that contain specific metrics.

## Context

You are searching for: %(metrics)s
Firm: %(firm)s
Period: %(period)s
Statement: %(statement)s

PTO (the standard retriever) already failed on this batch -- \
you are the fallback.

## Data directory structure

data/
  <Sector>/
    <FIRM>/
      <FIRM>_<YEAR>_<DOCTYPE>.md
      <UNSTRUCTURED_FILENAME>.md
      ...

Use list_dir('') to discover available sectors. Do not assume \
directory names -- they may change as new companies are added. \
Filenames may also be inconsistent, which is where your inference is needed.

## Your tools

- list_dir(path): see what's in a directory
- spawn_gulei(files): send up to 6 files to workers who will search \
  for the right table chunks. Returns full chunk text for hits, \
  null for misses, plus a list of already-surveyed files.
- report_results(node_ids, exhausted): call this when done. \
  Pass best node_ids (up to 3), or exhausted=true if nothing found.

## Strategy

1. Use list_dir to find the firm's directory
2. Pick up to 6 most promising files (consider period, filing type)
3. Call spawn_gulei with those files
4. Read the results -- chunk text is shown for hits
5. If hits: pick the best (up to 3) node_ids. \
   Call report_results with those node_ids.
6. If all null: pick next batch of files (avoid already-surveyed)
7. Repeat until you find chunks or exhaust all files
8. If all files exhausted: call report_results with exhausted=true

## Rules

- The spawn_gulei result lists already-surveyed files at the bottom. \
  NEVER re-pick files listed there.
- Pick at most 6 files per spawn_gulei call
- You MUST call report_results to finish. Do not end without it.
- report_results node_ids: at most 3, ranked best first"""

LENG_SYSTEM = """\
You are a financial data screener. Given a table chunk from a \
filing, determine if it contains ALL requested metrics. Extract \
values if found. Output JSON only, no explanation."""

GULEI_PAI_SYSTEM = """\
You are a chunk selector. Given a list of table chunk summaries \
from a financial filing, pick the ones most likely to contain \
specific metrics. Output JSON only, no explanation."""

GULEI_SAU_SYSTEM = """\
You are a chunk reviewer. Given multiple table chunks that workers \
flagged as relevant, pick the single best one for the requested \
metrics. Output JSON only, no explanation."""

# ── JSON parsing helper ──────────────────────────────────────────────────


def _parse_json_with_fences(raw: str) -> dict | None:
    """Parse JSON, stripping markdown fences. Returns None on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ── Prompt builders ──────────────────────────────────────────────────────


def _build_leng_prompt(
    file_name: str,
    section: str,
    chunk_text: str,
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    return (
        f"file: {file_name}\n"
        f"section: {section}\n"
        f"retrieve_target: {request.retrieve_target}\n"
        f"---\n"
        f"{chunk_text}\n"
        f"---\n\n"
        f"Extract ALL of these metrics from the chunk above:\n"
        f"  {metrics_str}\n"
        f"Period: {request.period}\n"
        f"Statement type: {request.statement}\n\n"
        f"For each metric, report its raw numeric value "
        f"(no commas, no $, no parentheses for negatives — "
        f"use minus sign).\n"
        f"If you CANNOT find ALL listed metrics in this chunk, "
        f"return found=false.\n\n"
        f"Output JSON only:\n"
        f'{{"found": true, '
        f'"metrics": {{"MetricName": 12345, ...}}}}\n'
        f"OR\n"
        f'{{"found": false}}'
    )


def _build_gulei_pai_prompt(
    table_chunks: list[tuple[str, str, str]],
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    lines = [
        "File: (table chunks listed below)",
        f"Batch wants: {metrics_str} for {request.period}, "
        f"{request.statement}.",
        f"Retrieve target: {request.retrieve_target}",
        "",
        "Table chunks in this file:",
    ]
    for i, (_nid, section, preview) in enumerate(table_chunks):
        lines.append(f"  [{i}] section='{section}'")
        lines.append(f"      '{preview}'")

    k = min(12, len(table_chunks))
    lines.append("")
    lines.append(
        f"Pick up to {k} chunk indices most likely to contain "
        f"ALL requested metrics."
    )
    lines.append(f'Output JSON only: {{"picks": [0, 4, 7, ...]}}')
    return "\n".join(lines)


def _build_gulei_sau_prompt(
    candidates: list[tuple[str, str]],
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    lines = [
        f"Batch wants: {metrics_str} for {request.period}, "
        f"{request.statement}.",
        f"Retrieve target: {request.retrieve_target}",
        "",
        "Workers flagged these table chunks as containing all "
        "requested metrics:",
        "",
    ]
    for i, (_nid, text) in enumerate(candidates):
        lines.append(f"CANDIDATE {i}:")
        lines.append(text)
        lines.append("")

    lines.append(
        "Which single candidate best answers the batch request?"
    )
    lines.append(
        "Consider: correct period, correct statement, data quality."
    )
    lines.append(f'Output JSON only: {{"best": 0}}')
    return "\n".join(lines)


# ── list_dir helper ──────────────────────────────────────────────────────


def _list_data_dir(data_dir: Path, rel_path: str) -> str:
    """List contents of a directory under data/.

    Returns a newline-separated list of entries, each suffixed with /
    if it's a directory. Returns error string if path escapes data_dir.
    """
    target = (data_dir / rel_path).resolve()
    if not str(target).startswith(str(data_dir.resolve())):
        return "Error: path escapes data directory. Use relative paths only."
    if not target.is_dir():
        return f"Error: '{rel_path}' is not a directory."
    entries = sorted(target.iterdir())
    lines = []
    for e in entries:
        if e.name.startswith("."):
            continue
        if e.is_dir():
            lines.append(f"{e.name}/")
        else:
            lines.append(e.name)
    if not lines:
        return "(empty directory)"
    return "\n".join(lines)


# ── spawn_gulei result formatter ─────────────────────────────────────────


def _format_spawn_gulei_result(
    files: list[str],
    results: list[str | None],
    index: VectorStoreIndex,
    blacklist: set[str],
) -> str:
    parts = ["FILE RESULTS:\n"]
    for fname, nid in zip(files, results):
        if nid is None:
            parts.append(f"{fname}: NOT FOUND\n")
            continue
        node = index.docstore.docs.get(nid)
        if node is None:
            parts.append(f"{fname}: NOT FOUND (stale node_id)\n")
            continue
        section = node.metadata.get("section", "?")
        text = node.text
        if len(text) > _MAX_CHUNK_CHARS_IN_TOOL_RESULT:
            text = text[:_MAX_CHUNK_CHARS_IN_TOOL_RESULT] + "\n[truncated]"
        parts.append(f"{fname}: FOUND")
        parts.append(f"  node_id: {nid}")
        parts.append(f"  section: {section}")
        parts.append("  ---")
        parts.append(text)
        parts.append("  ---\n")

    parts.append("Already surveyed (do NOT re-pick):")
    parts.append(f"  {', '.join(sorted(blacklist))}")
    return "\n".join(parts)


# ── Leng ─────────────────────────────────────────────────────────────────


def _run_leng(
    node_id: str,
    chunk_text: str,
    file_name: str,
    section: str,
    request: PTOBatchRequest,
    leng_llm,
) -> dict:
    """Screen one chunk. Returns {found, node_id, metrics | None}."""
    prompt = _build_leng_prompt(file_name, section, chunk_text, request)
    raw = leng_llm.complete(prompt, system_prompt=LENG_SYSTEM)
    parsed = _parse_json_with_fences(raw)

    if parsed is None:
        return {"found": False, "node_id": node_id}

    parsed["node_id"] = node_id
    return parsed


# ── GuleiPai ─────────────────────────────────────────────────────────────


def _run_gulei_pai(
    table_chunks: list[tuple[str, str, str]],
    request: PTOBatchRequest,
    gulei_llm,
) -> list[int]:
    """Pick top-k chunk indices. Returns list of valid indices, or []."""
    prompt = _build_gulei_pai_prompt(table_chunks, request)
    raw = gulei_llm.complete(prompt, system_prompt=GULEI_PAI_SYSTEM)
    parsed = _parse_json_with_fences(raw)
    if parsed is None:
        return []

    picks = parsed.get("picks", [])
    valid = [
        i for i in picks
        if isinstance(i, int) and 0 <= i < len(table_chunks)
    ]
    return valid


# ── GuleiSau ─────────────────────────────────────────────────────────────


def _run_gulei_sau(
    hits: list[dict],
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    gulei_llm,
) -> str:
    """Pick best chunk from multiple Leng hits. Returns node_id."""
    candidates = []
    for h in hits:
        nid = h["node_id"]
        node = index.docstore.docs.get(nid)
        if node is None:
            continue
        candidates.append((nid, node.text))

    if not candidates:
        return hits[0]["node_id"]

    prompt = _build_gulei_sau_prompt(candidates, request)
    raw = gulei_llm.complete(prompt, system_prompt=GULEI_SAU_SYSTEM)
    parsed = _parse_json_with_fences(raw)

    if parsed is None:
        return candidates[0][0]

    best_idx = parsed.get("best")
    if (
        not isinstance(best_idx, int)
        or best_idx < 0
        or best_idx >= len(candidates)
    ):
        return candidates[0][0]

    return candidates[best_idx][0]


# ── Single Gulei (per-file) ─────────────────────────────────────────────


def _run_single_gulei(
    file_name: str,
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    file_table_index: dict[str, list[tuple[str, str, str]]],
    gulei_llm,
    leng_llm,
    ch: ToolChannel,
) -> str | None:
    """Survey one file for relevant table chunks.
    Returns node_id of best chunk, or None.
    """
    # Step 0: pull table chunks from pre-computed index
    table_chunks = file_table_index.get(file_name, [])
    ch.print(f"  Gulei {file_name}: {len(table_chunks)} table chunks")
    if not table_chunks:
        ch.print(f"  Gulei {file_name}: NOT FOUND (0 table chunks)")
        return None

    # Step 1: GuleiPai picks top-k chunks
    k = min(12, len(table_chunks))
    ch.print(f"  Gulei {file_name}: GuleiPai picking {k}...")
    picks = _run_gulei_pai(table_chunks, request, gulei_llm)
    if not picks:
        ch.print(
            f"  Gulei {file_name}: NOT FOUND (GuleiPai returned nothing)"
        )
        return None

    # Step 2: spawn Lengs in parallel
    picked_chunks = []
    for i in picks:
        if i >= len(table_chunks):
            continue
        nid, section, _preview = table_chunks[i]
        node = index.docstore.docs.get(nid)
        if node is None:
            continue
        picked_chunks.append((nid, node.text, section))

    n_lengs = len(picked_chunks)
    ch.print(f"  Gulei {file_name}: {n_lengs} Lengs...")

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(
                _run_leng, nid, text, file_name, section,
                request, leng_llm,
            )
            for nid, text, section in picked_chunks
        ]
        leng_results = []
        for f in futures:
            try:
                leng_results.append(f.result())
            except Exception:
                leng_results.append({"found": False})

    hits = [r for r in leng_results if r.get("found")]
    ch.print(
        f"  Gulei {file_name}: {len(hits)} Leng hits out of {n_lengs}"
    )

    # Step 3: branch
    if len(hits) == 0:
        ch.print(f"  Gulei {file_name}: NOT FOUND")
        return None

    if len(hits) == 1:
        nid = hits[0]["node_id"]
        ch.print(f"  Gulei {file_name}: FOUND {nid[:12]}...")
        return nid

    # hits > 1: GuleiSau picks best
    ch.print(
        f"  Gulei {file_name}: GuleiSau picking best of {len(hits)} hits"
    )
    best_nid = _run_gulei_sau(hits, request, index, gulei_llm)
    ch.print(f"  Gulei {file_name}: FOUND {best_nid[:12]}...")
    return best_nid


# ── Parallel Gulei dispatch ──────────────────────────────────────────────


def _run_guleis_parallel(
    files: list[str],
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    file_table_index: dict[str, list[tuple[str, str, str]]],
    gulei_llm,
    leng_llm,
    ch: ToolChannel,
) -> list[str | None]:
    """Run one Gulei per file in parallel. Returns list parallel to files:
    node_id (str) if Gulei found a chunk, None if not.
    """
    def _safe_gulei(fname: str) -> str | None:
        try:
            return _run_single_gulei(
                fname, request, index, file_table_index,
                gulei_llm, leng_llm, ch,
            )
        except Exception:
            ch.print(f"  Gulei {fname}: CRASHED (exception), skipping")
            return None

    ch.print(f"Spawning {len(files)} Gulei workers...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_safe_gulei, f) for f in files]
        return [f.result() for f in futures]


# ── Entry point ──────────────────────────────────────────────────────────


def run_pumba(
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    config: Config,
    channel: ToolChannel | None = None,
) -> list[str]:
    """PUMBA fallback retrieval. Returns list of node_ids (len 0-3).

    Returns [] if user chose 'skip' (exhausted / max turns).
    Raises ValueError only if user chose 'exit'.
    """
    ch = channel or register("PUMBA")

    ch.print(f"Dailo starting for {request.firm} {request.period}...")

    # ── LLM setup ──
    # All three tiers go through llm.py factories → LLMBackend.
    from src.llm import (
        get_pumba_dailo_llm, get_pumba_gulei_llm, get_pumba_leng_llm,
        LLMResponse,
    )
    dailo_backend = get_pumba_dailo_llm(config)
    gulei_llm = get_pumba_gulei_llm(config)
    leng_llm = get_pumba_leng_llm(config)

    # ── Pre-compute file -> table chunks index ──
    file_table_index: dict[str, list[tuple[str, str, str]]] = {}
    for node_id, node in index.docstore.docs.items():
        meta = node.metadata
        if meta.get("chunk_type") != "table":
            continue
        fname = meta.get("file_name", "")
        if not fname:
            continue
        preview = node.text[:80].replace("\n", " ")
        section = meta.get("section", "")
        file_table_index.setdefault(fname, []).append(
            (node_id, section, preview)
        )

    # ── Build Dailo system prompt (templated ONCE) ──
    dailo_system_prompt = DAILO_SYSTEM_TEMPLATE % {
        "firm": request.firm,
        "period": request.period,
        "metrics": ", ".join(request.metrics),
        "statement": request.statement,
    }

    # ── Agent loop ──
    messages = [{
        "role": "user",
        "content": "Begin searching. Use list_dir to find the firm's directory.",
    }]

    blacklist: set[str] = set()
    turn_counter = 0

    while turn_counter < MAX_DAILO_TURNS:
        response = dailo_backend.call_with_tools(
            messages=messages,
            system_prompt=dailo_system_prompt,
            tools=DAILO_TOOLS,
        )

        # ── end_turn without report_results (error) ──
        if response.stop_reason == "end_turn":
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: you must call report_results. "
                    "Do not end without it."
                ),
            })
            turn_counter += 1
            continue

        # ── no tool calls (max_tokens truncation, unexpected stop) ──
        if not response.tool_calls:
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: response contained no tool calls (possibly "
                    "truncated). You must call a tool: list_dir, "
                    "spawn_gulei, or report_results."
                ),
            })
            turn_counter += 1
            continue

        # ── tool_use dispatch ──
        tool_results = []
        report = None

        for tc in response.tool_calls:
            if tc.name == "list_dir":
                path = tc.input["path"]
                listing = _list_data_dir(config.data_dir, path)
                tool_results.append({
                    "id": tc.id, "name": tc.name,
                    "content": listing,
                })

            elif tc.name == "spawn_gulei":
                files = tc.input["files"]
                # Normalize to basenames (Dailo may pass full paths)
                files = [Path(f).name for f in files]
                # Truncate to 6
                if len(files) > 6:
                    files = files[:6]
                # Python enforces blacklist even if Dailo re-picks
                files = [f for f in files if f not in blacklist]
                if not files:
                    tool_results.append({
                        "id": tc.id, "name": tc.name,
                        "content": (
                            "All requested files already surveyed. "
                            "Pick different files or call "
                            "report_results(node_ids=[], exhausted=true)."
                        ),
                    })
                else:
                    # Validate filenames exist in docstore
                    valid_files = [
                        f for f in files if f in file_table_index
                    ]
                    invalid_files = [
                        f for f in files if f not in file_table_index
                    ]
                    if not valid_files:
                        blacklist.update(files)
                        tool_results.append({
                            "id": tc.id, "name": tc.name,
                            "content": (
                                "No valid files found in index. "
                                "These filenames have no table chunks: "
                                + ", ".join(invalid_files)
                            ),
                        })
                    else:
                        results = _run_guleis_parallel(
                            valid_files, request, index,
                            file_table_index, gulei_llm, leng_llm, ch,
                        )
                        blacklist.update(valid_files)
                        blacklist.update(invalid_files)
                        n_hits = sum(
                            1 for r in results if r is not None
                        )
                        ch.print(
                            f"Round: {n_hits}/{len(valid_files)} "
                            f"files returned chunks"
                        )
                        result_str = _format_spawn_gulei_result(
                            valid_files, results, index, blacklist,
                        )
                        if len(files) > 6:
                            result_str += (
                                "\n(truncated to 6 files — send "
                                "remaining in next spawn_gulei call)"
                            )
                        tool_results.append({
                            "id": tc.id, "name": tc.name,
                            "content": result_str,
                        })

            elif tc.name == "report_results":
                # Reject if spawn_gulei was also called this turn
                has_spawn = any(
                    t.name == "spawn_gulei" for t in response.tool_calls
                )
                if has_spawn:
                    tool_results.append({
                        "id": tc.id, "name": tc.name,
                        "content": (
                            "Error: cannot report_results in the same "
                            "turn as spawn_gulei. See the spawn results "
                            "first, then call report_results."
                        ),
                    })
                else:
                    report = tc.input
                    if "node_ids" in report:
                        report["node_ids"] = report["node_ids"][:3]
                    tool_results.append({
                        "id": tc.id, "name": tc.name,
                        "content": "Received.",
                    })

        # Append conversation history
        messages.append(response.to_assistant_message())
        messages.append(
            LLMResponse.make_tool_results_message(tool_results)
        )

        if report is not None:
            if report.get("exhausted"):
                ch.print(
                    f"PUMBA exhausted all files for {request.firm}"
                )
                answer = ch.input(
                    "PUMBA failed to find the data. Options:\n"
                    "  'skip' - skip this batch\n"
                    "  'exit' - abort the entire PMS1 run\n"
                    "Your choice:"
                )
                if answer.strip().lower() == "exit":
                    raise ValueError(
                        f"User aborted after PUMBA exhausted "
                        f"all files for {request.firm}"
                    )
                return []

            node_ids = report["node_ids"]
            ch.print(f"Returning {len(node_ids)} chunks to Judge.")
            return node_ids

        turn_counter += 1

    # Exceeded max turns
    ch.print(f"PUMBA Dailo exceeded {MAX_DAILO_TURNS} turns")
    answer = ch.input(
        "Dailo ran out of turns. 'skip' or 'exit':"
    )
    if answer.strip().lower() == "exit":
        raise ValueError(
            f"User aborted after Dailo exceeded "
            f"{MAX_DAILO_TURNS} turns"
        )
    return []
