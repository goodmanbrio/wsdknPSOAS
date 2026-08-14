"""sekei.py — Bunragman's top-level dispatcher/reconciler.

Control flow matches Specs/wsnBunragMan.md's process diagram, SEKEI
through RECONCILE. Same pattern as src/scripts/PMS2/sekei_loop.py:

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
    call_bunragman (the per-source agent) is a thin wrapper around
    OSHA: one real call to _call_osha() (run_research_pipeline(),
    scoped to this source's file list via file_scope), then its
    already-formatted answer sheet is unwound back to raw
    [Source: ...] citations via expand_answer_sheet_for_summary()
    (src/harness/answer_sheet_contract.py) and written to disk. The
    per-source agent makes no LLM call of its own — OSHA's synthesis
    is the only per-source LLM call left. There used to be a second
    external call here (BunNavHarness, for xlsx spreadsheet data) and
    a Bunragman-level summarization LLM call reconciling OSHA's
    narrative against BunNavHarness's structured output; both are
    gone — BunNavHarness was never implemented (always stubbed), and
    dropping it removes the summarization pass it existed to feed.
    Every real external call is wrapped in
    _router.start_spinner()/stop_spinner(), same convention as PMS2
    (sekei_loop.py, mapper.py, dispatcher.py) — including under
    sekei's own per-source fan-out, which races multiple threads on
    the one global spinner exactly the way PMS2's per-firm fan-out
    already does.

Usage:
    from src.scripts.bunragman.sekei import run_bunragman_sekei

    answer = run_bunragman_sekei(query, config, channel, session_dir)
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.config import Config
from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel, _router
from src.scripts.zako import FAILURE, zako_discover

_REPO_ROOT = Path(__file__).resolve().parents[3]

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
        _router.start_spinner("LANE-GROUP")
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
            channel.print("Cancelled by user.", markdown=True)
            return None

        if final_sources is not None:
            channel.print(
                f"Batching confirmed: {len(final_sources)} "
                f"source(s) — {', '.join(final_sources.keys())}",
                markdown=True,
            )
            return final_sources

        turn_counter += 1

    channel.print(
        f"GROUP loop exhausted {_GROUP_MAX_TURNS} turns without finalizing.",
        markdown=True,
    )
    return None


# ── Bunragman per-source agent ────────────────────────────────────────────
#
# CALLOSHA (real) -> WRITEDISK. No SELECT, no BunNavHarness, no Bunragman-
# level summarize LLM call — OSHA's own already-cited answer is the
# per-source summary, unwound back to raw citations for RECONCILE to
# read. See module docstring for what used to be here and why it's gone.

# STUB — kept dormant, not called live. OSHA's real file_scope param has
# landed (see _call_osha below, which is what call_bunragman actually
# calls now); this stub stays only because test_bunragman_agent_live.py's
# fixture scenarios still want a controllable, fixed OSHA-side text.
_OSHA_STUB_PATH = (
    _REPO_ROOT
    / "temp"
    / "sessions"
    / "20260810_145446"
    / "research_What_are_the_current_sell-side_analyst_price_targe.md"
)


def _call_osha_stub(label: str) -> str:
    """STUB — kept dormant, see _OSHA_STUB_PATH comment above. Not called
    by call_bunragman; call_bunragman calls _call_osha (real) instead."""
    _router.start_spinner(f"LANE-{label}-osha")
    try:
        return _OSHA_STUB_PATH.read_text(encoding="utf-8")
    finally:
        _router.stop_spinner()


def _call_osha(
    query: str,
    files: list[str],
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
    label: str,
) -> str:
    """CALLOSHA (real): run_research_pipeline() scoped to this source's
    file list via file_scope. xlsx paths in the list contribute nothing —
    OSHA's index never ingests them, file_scope hard-scopes to whatever
    matches, so passing them through is harmless, not a bug. Real OSHA
    can return a "nothing found" style answer — that's a normal result,
    not a failure."""
    from src.harness.terminal_router import register
    from src.scripts.research import run_research_pipeline

    research_channel = register(f"RESEARCH-{label}")

    _router.start_spinner(f"LANE-{label}-osha")
    try:
        return run_research_pipeline(
            question=query,
            config=config,
            session_dir=session_dir,
            channel=research_channel,
            file_scope=files,
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
    from each other.

    OSHA's own synthesized answer is the per-source summary — no
    Bunragman-level SUMMARIZE LLM call on top of it. Its citations arrive
    already public-formatted ([1]/[1.1] labels + a per-source
    Bibliography, from OSHA's own format_answer_sheet() call); that gets
    unwound back to raw [Source: file — section] tags via
    expand_answer_sheet_for_summary() before writing to disk, so
    RECONCILE's citation-carry-forward contract (raw tags in, one global
    Bibliography out) is unchanged by any of this.
    Returns: filepath of the summary MD written to disk.
    """
    from src.harness.answer_sheet_contract import expand_answer_sheet_for_summary
    from src.harness.terminal_router import register

    ((label, files),) = source.items()
    lane_channel = register(f"LANE-{label}")
    lane_channel.print(f"{len(files)} file(s)")

    osha_answer = _call_osha(query, files, config, channel, session_dir, label)
    summary = expand_answer_sheet_for_summary(osha_answer)

    out_dir = session_dir / "lane"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^\w\-]", "_", label)
    out_path = out_dir / f"{safe_label}_summary.md"
    out_path.write_text(summary, encoding="utf-8")
    lane_channel.print(f"summary written: {out_path}")
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

    _router.start_spinner("LANE-RECONCILE")
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
    tool the model itself decides to call. call_bunragman's own OSHA
    call is real; see module docstring for what used to sit alongside
    it (BunNavHarness, a Bunragman-level summarize call) and why it's
    gone now.
    """
    from src.harness.terminal_router import register

    lane_channel = register("LANE")
    lane_channel.print(f"sekei: {query[:100]}")

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

    lane_channel.print(
        f"Discovered {len(raw_dirs)} raw dir(s): {', '.join(raw_dirs)}"
    )

    # GROUP — real agentic tool-calling loop (ask_user is a tool the
    # model calls itself, not a fixed y/modify/redo menu)
    sources = _run_group_loop(raw_dirs, query, config, lane_channel)

    if sources is None:
        return "**Cancelled by user.**"
    if not sources:
        return "**No sources found.**"

    # CALLAGENT — fan out, one Bunragman-agent call per source
    lane_channel.print(f"Fanning out to {len(sources)} lane(s)")
    summary_paths: list[str] = []
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {
            pool.submit(
                call_bunragman, {label: files}, query, config, lane_channel,
                session_dir,
            ): label
            for label, files in sources.items()
        }
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                summary_paths.append(fut.result())
            except Exception as e:
                lane_channel.print(f"{label} agent failed: {e}")

    if not summary_paths:
        return "**No sources found.**"

    # RECONCILE — real LLM call
    lane_channel.print(f"Reconciling {len(summary_paths)} summary(ies)")
    answer = _reconcile(query, summary_paths, config)
    lane_channel.print(f"Complete — {len(answer)} chars")

    return answer
