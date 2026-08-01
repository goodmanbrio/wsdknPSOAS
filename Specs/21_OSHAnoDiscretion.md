# Spec 21 (OSHA run_research port) Discretionary Decisions Log

Created:        2026-08-01
Updated:        2026-08-01
Surroundings:   2026-08-01

Decisions made during implementation that spec 21 did not fully
specify. Only design choices, not execution mechanics.

Spec 21 was unusually tight — every file change carried a verified line
anchor and every anchor still held at execution time (HEAD 84c0290). The
list below is therefore short, and nothing here changes the port's
behaviour as specified.

---

## D1: `pip install -r requirements.txt` not run

Step 1 says apply F1 then `pip install -r requirements.txt`, noting it is
"locally a no-op."

`rank_bm25` is already importable in this env
(`/opt/anaconda3/lib/python3.11/site-packages/rank_bm25.py`), and the
sandbox this session runs under does not allow reaching PyPI. A full
`pip install -r` would either no-op or fail on network, and it would touch
every other pinned dep for no reason.

Decision: apply the `requirements.txt` edit, verify `import rank_bm25`
directly, skip the install. The edit's entire purpose is the fresh-clone
case, which U7 locks. Nothing about the local run depends on it.

## D2: Placement of the `run_research` tool dict in `TOOL_DEFINITIONS`

F4's prose says "insert before `run_pteca` (between the commented PMS1
block and the PTECA block)"; F4's mermaid shows `run_research` directly
after `run_pms2`. Those are two different slots — the commented-out PMS1
block sits between them.

Decision: followed the prose, not the diagram. `run_research` sits after
the commented PMS1 block and before the `# 07_tool_pteca` comment. The
diagram is a logical ordering (it omits the commented block entirely), so
there is no real conflict; ordering has no functional effect either way,
since the LLM sees a JSON array of tool schemas.

## D3: `re` used for the filename slug is the module-level import

F5 notes `re` is "already imported at line 15 -- verified." It is, and
`_exec_research` uses that module-level import rather than a local one.
Recording it only because the handler otherwise imports everything it
needs lazily inside the function body (`run_research_pipeline`).

## D4: The LLM test skipif gate must import `llm.py` before reading the env

Spec's LLM-test preconditions say to gate the module with
`pytestmark = pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), ...)`.

Taken literally that gate is wrong for this repo. `DEEPSEEK_API_KEY` is
not in the shell environment — it lives in `.env`, and the thing that
loads it is `load_dotenv(...)` at `llm.py:33`, which only runs when
`src.scripts.llm` is imported. A bare `os.getenv` at module scope reads
the environment *before* any such import, so the entire LLM module would
skip on a correctly configured machine, silently.

Decision: `import src.scripts.llm` immediately above the `pytestmark`
line, purely for its `load_dotenv` side effect, then gate as specified.
Comment in the test file explains why the import is not dead code so a
future tidy-up does not remove it.

This is a test-harness correction, not a spec divergence — the intent
("skip when there is no key") is preserved; only the mechanism changed.

## D5: L0 is flaky — 1 of 5 observed decomposer runs returned a single sub-question

L0 asserts `decompose_question` returns 3-7 `SubQuestion`s. On the first
run it returned **1**:

    "What do management's latest earnings calls and analyst reports
     indicate about LITE's competitive outlook and forward guidance?"
     (4 keywords)

Four further calls with the identical question returned 6, 6, 5, and 6.
L0 passes on re-run.

Assessment: model variance, not a port defect. The spec already flags
(F6a, and L0's own wording) that `minItems`/`maxItems` in
`DECOMPOSITION_SCHEMA` are hints the DeepSeek backend does not hard-enforce,
so the count assertion tests the model. `temp=0` does not make DeepSeek
fully deterministic. The copied module and the copied F7 sysprompt are
byte-identical to the osha branch, which has the same exposure.

Decision: left the assertion at 3-7 rather than loosening it to `>=1`. A
1-sub-question decomposition is a genuine retrieval-quality degradation —
with `research_bm25_top_k=5`, one sub-question caps retrieval at 5 chunks
instead of the intended 20 — so the test *should* go red when it happens.
Loosening it would hide the thing worth knowing.

Flagged in the spec ledger next to L0. Not fixed here: any fix is a change
to F7's sysprompt prose or a retry loop in the copied `decomposer.py`, and
both are post-port changes to verbatim-copied files.

## D6: Step 7 (index warm) skipped

Spec marks it OPTIONAL and measures the saving at ~1s. Skipped, so
`data/index/bm25_index.pkl` is not committed and not present; the first
real `run_research` call builds it. This keeps `data/index/` byte-identical
to its pre-port state, which is also what the tests' HARD RULE check
asserts against.

## D7: Acceptance REPL driven via piped stdin, not an interactive terminal

The acceptance criterion says "launch the REPL." This session has no
interactive TTY, so the REPL was driven by piping the follow-up question
and `exit` into `python src/psoas.py "<first query>"`, with the first
query passed as argv (a supported entry mode per `psoas.py`'s own docstring).

Same code path, same `run_harness`, same agent loop — only the source of
stdin differs. Recorded because the transcript shows no typed input.
