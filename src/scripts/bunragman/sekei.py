"""sekei.py — Bunragman's top-level dispatcher/reconciler.

Control flow matches Specs/wsnBunragMan.md's process diagram, SEKEI
through RECONCILE. Staged stub-then-real build, same pattern as
src/scripts/PMS2/sekei_loop.py:

    Discovery (zako_discover, src/scripts/zako/) is real — a blocking
    scan + LLM select + ask_user confirm/modify/redo loop over the
    directories under config.zako_source_root. Its file paths are
    relative to config.zako_source_root, not absolute.
    _run_group_loop (GROUP) is a real agentic tool-calling loop, same
    shape as PMS2's sekei_loop.py — ask_user is a tool the model calls
    itself, as many times as it needs, on its own dedicated
    bunragman_sekei_group role (thinking off, snappy by design — GROUP is
    a filename-classification task, not deep synthesis). _reconcile
    (RECONCILE) is a separate one-shot LLM call on the bunragman_sekei
    role (thinking on — it's doing real cross-source synthesis, that
    reasoning is earned there). The two roles are deliberately split;
    GROUP doesn't share RECONCILE's profile or its thinking budget.
    call_bunragman (the per-source agent, PARTITION through WRITEDISK)
    is real control flow with two real LLM calls (bunragman_agent role:
    xlsx target selection, summary write). Of its two external tool
    calls, OSHA is now real: _call_osha() runs run_research_pipeline()
    scoped to this source's non-xlsx files via file_scope (landed from
    the coworker's OSHA branch — src/scripts/research/__init__.py,
    retriever.py, bm25_index.py). _call_osha_stub() still exists but is
    dormant, kept only for test_bunragman_agent_live.py's fixture
    scenarios. _call_bunnavharness_stub() is still the live path —
    returns one of two fixed JSON fixtures; BunNavHarness doesn't exist
    in this repo at all yet, separate work stream.
    Every real (and stub) external call is wrapped in
    _router.start_spinner()/stop_spinner(), same convention as PMS2
    (sekei_loop.py, mapper.py, dispatcher.py) — including under
    call_bunragman's own per-source fan-out, which races multiple
    threads on the one global spinner exactly the way PMS2's per-firm
    fan-out already does.

Usage:
    from src.scripts.bunragman.sekei import run_bunragman_sekei

    answer = run_bunragman_sekei(query, config, channel, session_dir)
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.config import Config
from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel, _router
from src.scripts.zako import FAILURE, zako_discover

_REPO_ROOT = Path(__file__).resolve().parents[3]

XLSX_EXTS = (".xlsx", ".xlsm")

# JSON schema for structured_complete — GROUP's output must match this shape.
# References files by INDEX into a numbered listing, not by echoing the full
# path back — echoing 20+ long, punctuation-heavy filenames verbatim blew
# past a reliable tool-call response (truncated/invalid JSON, every attempt,
# not the ~11% flake structured_complete's docstring warns about) and risked
# the model mistyping a path into one that doesn't exist on disk.
SOURCES_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": (
                            "Short human-readable source name, e.g. "
                            "'JPM', 'Mizuho', 'Internal Model'"
                        ),
                    },
                    "file_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Indices into the numbered file listing, "
                            "belonging to this source"
                        ),
                    },
                },
                "required": ["label", "file_indices"],
            },
            "minItems": 1,
        },
    },
    "required": ["sources"],
}


# ── GROUP (real agentic tool-calling loop) ─────────────────────────────────
#
# Same shape as PMS2's sekei_loop.py, not a one-shot propose + external
# Python confirm/modify/redo state machine. ask_user is a tool the model
# calls itself, as many times as it needs, referencing specific files if
# useful — a `modify` is just the next turn of the same conversation, not
# a second cold LLM call with feedback pasted into a fresh prompt.

GROUP_TOOLS: list[dict] = [
    {
        "name": "ask_user",
        "description": (
            "Ask the user a question about the source grouping — confirm "
            "a proposed grouping, clarify an ambiguous file's owner, or "
            "ask which files to include or exclude. Reference specific "
            "filenames when useful."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "finalize_grouping",
        "description": (
            "Finalize the source grouping once the user has confirmed it "
            "via ask_user. Do not call this before that confirmation."
        ),
        "input_schema": SOURCES_SCHEMA,
    },
]

_GROUP_MAX_TURNS = 8


def _run_group_loop(
    raw_dirs: dict[str, list[str]],
    query: str,
    config: Config,
    channel: ToolChannel,
) -> dict[str, list[str]] | None:
    """GROUP: real agentic tool-calling loop, same pattern as PMS2's
    run_sekei(). Only finalize_grouping ends the loop; a hard gate blocks
    finalizing before the user has confirmed via at least one ask_user
    exchange in this same conversation — mirrors PMS2's user_confirmed
    gate on finalize_stencil.

    Returns the confirmed {label: [file_path, ...]} dict, or None if the
    user cancels/exits partway through, or the loop exhausts its turn cap
    without finalizing — a distinct outcome from "0 sources resolved"
    (N=0 is a valid finalize_grouping result; either of the above is not).
    """
    from src.scripts.llm import LLMResponse, get_bunragman_sekei_group_llm

    backend = get_bunragman_sekei_group_llm(config)
    sysprompt = load_sysprompt(
        "bunragman_sekei_group", config.bunragman_sekei_group_profile
    )

    # Flatten to a numbered listing — the model references files by index,
    # never by echoing the full path back (see SOURCES_SCHEMA comment).
    indexed: list[dict] = []
    for dir_name, paths in raw_dirs.items():
        for path in paths:
            indexed.append({"index": len(indexed), "dir": dir_name, "path": path})
    listing = "\n".join(
        f"{f['index']}: [{f['dir']}] {Path(f['path']).name}" for f in indexed
    )
    index_to_path = {f["index"]: f["path"] for f in indexed}

    messages: list[dict] = [{
        "role": "user",
        "content": (
            f"Research query:\n{query}\n\n"
            f"Numbered file listing (index: [directory] filename):\n{listing}"
        ),
    }]

    user_confirmed = False
    turn_counter = 0

    while turn_counter < _GROUP_MAX_TURNS:
        _router.start_spinner("BUNRAGMAN-GROUP")
        try:
            response = backend.call_with_tools(
                messages=messages,
                system_prompt=sysprompt,
                tools=GROUP_TOOLS,
                label="bunragman-sekei-group",
            )
        finally:
            _router.stop_spinner()

        if response.text and response.text.strip():
            channel.print(response.text, markdown=True)

        # end_turn without a tool call = error, nudge and retry
        if response.stop_reason == "end_turn":
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: you must call ask_user or finalize_grouping. "
                    "Do not end your turn without calling a tool."
                ),
            })
            turn_counter += 1
            continue

        has_ask_user = any(t.name == "ask_user" for t in response.tool_calls)

        tool_results: list[dict] = []
        final_sources: dict[str, list[str]] | None = None
        cancelled = False

        for tc in response.tool_calls:
            if tc.name == "ask_user":
                answer = channel.input(tc.input["question"], markdown=True)
                if answer is None or re.match(
                    r"^\s*(exit|quit|stop|cancel|abort)\b", answer, re.I
                ):
                    cancelled = True
                    tool_results.append({
                        "id": tc.id, "name": tc.name,
                        "content": f"User cancelled: {answer!r}",
                    })
                    continue
                user_confirmed = bool(re.match(
                    r"^\s*(y|yes|ok|confirm|confirmed|lgtm|use them|"
                    r"use these)\b", answer, re.I,
                ))
                tool_results.append({
                    "id": tc.id, "name": tc.name,
                    "content": f"User answered: {answer}",
                })
                turn_counter = 0  # no ceiling on user interaction

            elif tc.name == "finalize_grouping":
                if has_ask_user:
                    tool_results.append({
                        "id": tc.id, "name": tc.name,
                        "content": (
                            "Error: cannot finalize in the same turn as "
                            "ask_user. Call finalize_grouping alone in a "
                            "separate turn."
                        ),
                    })
                    continue
                if not user_confirmed:
                    ans = channel.input(
                        "Finalize this grouping? [y/n]", markdown=True
                    )
                    if not (ans and ans.strip().lower().startswith("y")):
                        tool_results.append({
                            "id": tc.id, "name": tc.name,
                            "content": (
                                "Error: user has not confirmed. Call "
                                "ask_user to show the grouping and get "
                                "confirmation before retrying."
                            ),
                        })
                        continue
                sources: dict[str, list[str]] = {}
                for s in tc.input["sources"]:
                    paths = [
                        index_to_path[i] for i in s["file_indices"]
                        if i in index_to_path
                    ]
                    if paths:
                        sources[s["label"]] = paths
                final_sources = sources
                tool_results.append({
                    "id": tc.id, "name": tc.name,
                    "content": f"Confirmed: {len(sources)} source(s).",
                })

            else:
                tool_results.append({
                    "id": tc.id, "name": tc.name,
                    "content": f"Error: unknown tool '{tc.name}'.",
                })

        messages.append(response.to_assistant_message())
        messages.append(LLMResponse.make_tool_results_message(tool_results))

        if cancelled:
            channel.print("[BUNRAGMAN] Cancelled by user.", markdown=True)
            return None

        if final_sources is not None:
            channel.print(
                f"[BUNRAGMAN] Batching confirmed: {len(final_sources)} "
                f"source(s) — {', '.join(final_sources.keys())}",
                markdown=True,
            )
            return final_sources

        turn_counter += 1

    channel.print(
        f"[BUNRAGMAN] GROUP loop exhausted {_GROUP_MAX_TURNS} turns "
        f"without finalizing.",
        markdown=True,
    )
    return None


# ── Bunragman per-source agent ────────────────────────────────────────────
#
# PARTITION (mechanical) -> SELECT (real LLM, xlsx only) -> CALLOSHA (stub) /
# CALLBUNNAV (stub, parallel per xlsx target) -> SUMMARIZE (real LLM) ->
# WRITEDISK. Conflict handling is a sysprompt instruction inside SUMMARIZE
# (sysprompts/bunragman_agent_summarize/), not a code-level decision — no
# D_INTERNALCONFLICT branch, no conflict schema.

# JSON schema for structured_complete — SELECT's output must match this
# shape. References xlsx files by INDEX into a numbered listing, same
# reasoning as sekei's SOURCES_SCHEMA above (no path echo-back).
XLSX_TARGETS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_index": {
                        "type": "integer",
                        "description": "Index into the numbered xlsx listing",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Specific metric(s)/period(s) to ask this file "
                            "for — not the raw user query verbatim"
                        ),
                    },
                },
                "required": ["file_index", "question"],
            },
        },
    },
    "required": ["targets"],
}

# STUB — kept dormant, not called live. OSHA's real file_scope param has
# landed (see _call_osha below, which is what call_bunragman actually
# calls now); this stub stays only because test_bunragman_agent_live.py's
# fixture scenarios still want a controllable, fixed OSHA-side text to
# pair against BunNavHarness's congruent/incongruent fixtures — BunNav
# itself is still fully stubbed, so those tests still need a fixed
# reference point on the OSHA side.
_OSHA_STUB_PATH = (
    _REPO_ROOT
    / "temp"
    / "sessions"
    / "20260810_145446"
    / "research_What_are_the_current_sell-side_analyst_price_targe.md"
)

# STUB — BunNavHarness doesn't exist in this repo yet (separate work
# stream). Two fixed fixtures, keyed off the OSHA stub answer above (which
# cites Mizuho's LITE F27E EPS of $8.77 and a $290 price target): one
# congruent with it, one that conflicts with it. Which fixture a given call
# gets is deterministic (hashed off the xlsx path), so a run with 2+ xlsx
# targets can surface both the plain and the flagged summary path.
_BUNNAV_CONGRUENT_FIXTURE = {
    "LITE_EPS": {"F26E": 4.69, "F27E": 8.77},
    "LITE_price_target": {"Mizuho_2025-11-18": 290},
}
_BUNNAV_INCONGRUENT_FIXTURE = {
    "LITE_EPS": {"F26E": 4.15, "F27E": 6.10},
    "LITE_price_target": {"Internal_Model_2026-01-15": 245},
}


def _partition_files(files: list[str]) -> tuple[list[str], list[str]]:
    """PARTITION (mechanical): non-xlsx files as one group, xlsx files apart.

    No judgment call here — file type is a fact, not a decision. Whether
    an xlsx file is worth actually querying is SELECT's job, not this
    function's.
    """
    non_xlsx = [f for f in files if not f.lower().endswith(XLSX_EXTS)]
    xlsx = [f for f in files if f.lower().endswith(XLSX_EXTS)]
    return non_xlsx, xlsx


def _select_xlsx_targets(
    query: str,
    xlsx_files: list[str],
    config: Config,
    label: str,
) -> list[tuple[str, str]]:
    """SELECT (real LLM call, xlsx only): which xlsx files are worth
    querying for this query, and what to ask each.

    Returns a list of (file_path, question) pairs — empty if no xlsx
    files were passed in, or if the model judges none are relevant (a
    purely qualitative query needs zero spreadsheet lookups).
    """
    if not xlsx_files:
        return []

    from src.scripts.llm import get_bunragman_agent_llm

    backend = get_bunragman_agent_llm(config)
    sysprompt = load_sysprompt(
        "bunragman_agent_select", config.bunragman_agent_profile
    )

    listing = "\n".join(
        f"{i}: {Path(f).name}" for i, f in enumerate(xlsx_files)
    )
    prompt = (
        f"Research query:\n{query}\n\n"
        f"Numbered spreadsheet file listing:\n{listing}\n\n"
        f"Decide which files (if any) are worth querying, and what to ask "
        f"each, per the rules above."
    )

    _router.start_spinner(f"BUNRAGMAN-{label}-select")
    try:
        result = backend.structured_complete(
            prompt=prompt,
            schema=XLSX_TARGETS_SCHEMA,
            system_prompt=sysprompt,
            label="bunragman-agent-select",
        )
    finally:
        _router.stop_spinner()

    targets: list[tuple[str, str]] = []
    for t in result["targets"]:
        idx = t["file_index"]
        if 0 <= idx < len(xlsx_files):
            targets.append((xlsx_files[idx], t["question"]))
    return targets


def _call_osha_stub(label: str) -> str:
    """STUB — kept dormant, see _OSHA_STUB_PATH comment above. Not called
    by call_bunragman; call_bunragman calls _call_osha (real) instead."""
    _router.start_spinner(f"BUNRAGMAN-{label}-osha")
    try:
        return _OSHA_STUB_PATH.read_text(encoding="utf-8")
    finally:
        _router.stop_spinner()


def _call_osha(
    query: str,
    non_xlsx_files: list[str],
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
    label: str,
) -> str:
    """CALLOSHA (real): run_research_pipeline() scoped to this source's
    non-xlsx files via file_scope. Real OSHA can return a "nothing found"
    style answer — that's a normal result, not a failure, same as before."""
    from src.scripts.research import run_research_pipeline

    _router.start_spinner(f"BUNRAGMAN-{label}-osha")
    try:
        return run_research_pipeline(
            question=query,
            config=config,
            session_dir=session_dir,
            channel=channel,
            file_scope=non_xlsx_files,
        )
    finally:
        _router.stop_spinner()


def _call_bunnavharness_stub(query: str, xlsx_path: str, label: str) -> dict:
    """STUB — see fixture comments above. Deterministic pick by path hash
    so the same file always gets the same fixture within a run. Spinner's
    here for when this becomes a real, slow call."""
    _router.start_spinner(f"BUNRAGMAN-{label}-bunnav-{Path(xlsx_path).stem}")
    try:
        digest = hashlib.md5(xlsx_path.encode()).hexdigest()
        if int(digest, 16) % 2 == 0:
            return _BUNNAV_CONGRUENT_FIXTURE
        return _BUNNAV_INCONGRUENT_FIXTURE
    finally:
        _router.stop_spinner()


def _write_summary(
    query: str,
    label: str,
    osha_text: str | None,
    bunnav_results: list[tuple[str, str, dict]],
    config: Config,
) -> str:
    """SUMMARIZE (real LLM call): OSHA text + BunNav results in, one
    source-level summary MD out. Conflict-flagging is a sysprompt
    instruction, not a code branch — see sysprompts/bunragman_agent_summarize/.
    """
    from src.scripts.llm import get_bunragman_agent_llm

    backend = get_bunragman_agent_llm(config)
    sysprompt = load_sysprompt(
        "bunragman_agent_summarize", config.bunragman_agent_profile
    )

    blocks = [f"## Source\n\n{label}", f"## Query\n\n{query}"]
    blocks.append(
        "## OSHA (narrative research)\n\n"
        + (osha_text if osha_text else "No non-xlsx files in this source.")
    )
    if bunnav_results:
        for path, question, data in bunnav_results:
            blocks.append(
                f"## BunNavHarness — {Path(path).name}\n\n"
                f"Question asked: {question}\n\n"
                f"```json\n{json.dumps(data, indent=2)}\n```"
            )
    else:
        blocks.append(
            "## BunNavHarness\n\nNo xlsx files queried for this source."
        )
    prompt = "\n\n".join(blocks)

    _router.start_spinner(f"BUNRAGMAN-{label}-summarize")
    try:
        return backend.complete(
            prompt=prompt,
            system_prompt=sysprompt,
            label="bunragman-agent-summarize",
        )
    finally:
        _router.stop_spinner()


def call_bunragman(
    source: dict[str, list[str]],
    query: str,
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
) -> str:
    """Bunragman per-source agent, real control flow.

    Argument: a single-entry dict {source_label: [file_path, ...]}. Paths
    are relative to config.zako_source_root (Zako's contract) — OSHA's
    file_scope normalizes against config.pms2_data_dir instead, but the
    two fields share the same default (data/files_ingested), so no
    translation layer is needed as long as they aren't overridden apart
    from each other. BunNavHarness is still fully stubbed — it never
    opens the xlsx files it's given.
    Returns: filepath of the summary MD written to disk.
    """
    ((label, files),) = source.items()
    non_xlsx, xlsx = _partition_files(files)
    channel.print(
        f"[BUNRAGMAN:{label}] {len(non_xlsx)} non-xlsx file(s), "
        f"{len(xlsx)} xlsx file(s)"
    )

    targets = _select_xlsx_targets(query, xlsx, config, label)
    channel.print(f"[BUNRAGMAN:{label}] SELECT chose {len(targets)} xlsx target(s)")

    osha_text = (
        _call_osha(query, non_xlsx, config, channel, session_dir, label)
        if non_xlsx else None
    )

    bunnav_results: list[tuple[str, str, dict]] = []
    if targets:
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {
                pool.submit(_call_bunnavharness_stub, question, path, label): (path, question)
                for path, question in targets
            }
            for fut in as_completed(futures):
                path, question = futures[fut]
                bunnav_results.append((path, question, fut.result()))

    summary = _write_summary(query, label, osha_text, bunnav_results, config)

    out_dir = session_dir / "bunragman"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^\w\-]", "_", label)
    out_path = out_dir / f"{safe_label}_summary.md"
    out_path.write_text(summary, encoding="utf-8")
    channel.print(f"[BUNRAGMAN:{label}] summary written: {out_path}")
    return str(out_path)


# ── RECONCILE (real LLM call) ────────────────────────────────────────────

def _reconcile(query: str, summary_paths: list[str], config: Config) -> str:
    """Real LLM call: read every source's summary MD, write the final
    cross-source answer.

    Per-source summaries already cite real underlying documents as
    [Source: filename — section] (SUMMARIZE's own citation contract).
    RECONCILE's sysprompt instructs the model to carry those citations
    forward verbatim rather than citing the summary file itself, then
    format_answer_sheet() — the same reusable contract OSHA's own
    synthesizer applies to its output (src/harness/answer_sheet_contract.py,
    zero duplicated logic) — turns them into [1]/[1.1] local labels plus a
    ## Bibliography, so citations resolve through to the real source
    documents, not to Bunragman's own intermediate summary files.
    """
    from src.harness.answer_sheet_contract import format_answer_sheet
    from src.scripts.llm import get_bunragman_sekei_llm

    backend = get_bunragman_sekei_llm(config)
    sysprompt = load_sysprompt(
        "bunragman_sekei_reconcile", config.bunragman_sekei_profile
    )

    blocks = []
    for path in summary_paths:
        text = Path(path).read_text(encoding="utf-8")
        blocks.append(f"### {Path(path).stem}\n\n{text}")
    context = "\n\n---\n\n".join(blocks)

    prompt = (
        f"## Research Query\n\n{query}\n\n"
        f"## Per-Source Summaries\n\n{context}\n\n"
        f"Write the final cross-source answer per the rules above."
    )

    _router.start_spinner("BUNRAGMAN-RECONCILE")
    try:
        draft = backend.complete(
            prompt=prompt,
            system_prompt=sysprompt,
            label="bunragman-sekei-reconcile",
        )
    finally:
        _router.stop_spinner()

    try:
        return format_answer_sheet(query, draft)
    except ValueError:
        # A malformed [Source: ...] tag (sysprompt violation, model
        # variance) shouldn't crash the whole answer — fall back to the
        # unlabeled draft rather than losing the reconciled answer.
        return draft


# ── sekei entry point ────────────────────────────────────────────────────

def run_bunragman_sekei(
    query: str,
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
    debug_dir: Path | None = None,
) -> str:
    """Bunragman's top-level dispatcher/reconciler.

    Control flow: Specs/wsnBunragMan.md, SEKEI through RECONCILE.
    Discovery (zako_discover), _run_group_loop (GROUP), _reconcile, and
    call_bunragman (the per-source agent) are all real control flow.
    Two live ask_user interactions happen in sequence, but of different
    shapes: Zako's own fixed confirm/modify/redo menu over directories,
    then GROUP's agentic loop over source batching, where ask_user is a
    tool the model itself decides to call. call_bunragman's own OSHA/
    BunNavHarness calls are monkeypatched (see module docstring).
    """
    channel.print(f"[BUNRAGMAN] sekei: {query[:100]}")

    # DISC — discovery tool (real, Zako)
    result = zako_discover(query, config, channel=channel)
    if result == FAILURE:
        return "**Discovery failed.** No directories resolved."

    # Zako's own redo flow can replace the query entirely — every step
    # after this point uses whatever query the user actually confirmed
    # sources against, not necessarily the one this function was called
    # with.
    query, raw_dirs = result
    if not raw_dirs:
        return "**No sources found.**"

    channel.print(
        f"[BUNRAGMAN] Discovered {len(raw_dirs)} raw dir(s): "
        f"{', '.join(raw_dirs)}"
    )

    # GROUP — real agentic tool-calling loop (ask_user is a tool the
    # model calls itself, not a fixed y/modify/redo menu)
    sources = _run_group_loop(raw_dirs, query, config, channel)

    if sources is None:
        return "**Cancelled by user.**"
    if not sources:
        return "**No sources found.**"

    # CALLAGENT — fan out, one stub Bunragman-agent call per source
    channel.print(
        f"[BUNRAGMAN] Fanning out to {len(sources)} Bunragman agent(s)"
    )
    summary_paths: list[str] = []
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {
            pool.submit(
                call_bunragman, {label: files}, query, config, channel, session_dir
            ): label
            for label, files in sources.items()
        }
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                summary_paths.append(fut.result())
            except Exception as e:
                channel.print(f"[BUNRAGMAN] {label} agent failed: {e}")

    if not summary_paths:
        return "**No sources found.**"

    # RECONCILE — real LLM call
    channel.print(f"[BUNRAGMAN] Reconciling {len(summary_paths)} summary(ies)")
    answer = _reconcile(query, summary_paths, config)
    channel.print(f"[BUNRAGMAN] Complete — {len(answer)} chars")

    return answer
