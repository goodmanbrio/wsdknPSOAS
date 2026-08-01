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
`data/index/` stayed byte-identical to its pre-port state right through the
test run — which is what let the HARD RULE be verified by direct inspection
(D7). The pickle was then built by the first real `run_research` call during
acceptance, as failure mode 9 predicts: 5,793,873 bytes, matching the spec's
predicted 5.8 MB. It is gitignored and regenerates from `docstore.json`.

## D7: The spec's HARD-RULE verification method is unsound; used a stronger one

The unit-test section says: "If `git status` shows `data/index/bm25_index.pkl`
after a test run, a test broke this rule."

That check can never fire. `.gitignore:2` ignores `data/` wholesale, so
`git check-ignore -v data/index/bm25_index.pkl` reports the file ignored and
`git status` stays silent whether or not a test wrote it. A run that
violated the HARD RULE would look identical to one that respected it.

Decision: verified by `ls data/index/` immediately after the test run
instead. It listed only `docstore.json`, `file_hashes.json`, and
`file_path_index.json` — no pickle — so the rule genuinely held, on
stronger evidence than the spec asked for.

(The 5.8 MB `bm25_index.pkl` that exists now was written later, by the real
`run_research` call during acceptance. That is the intended cold-start
build described in failure mode 9 and step 7, not a test violation.
Step 7's "Gitignore it" instruction is already satisfied by the blanket
`data/` rule — no `.gitignore` edit was needed or made.)

**Follow-on, and the one real bug I introduced.** My first cut of U6 asserted
`not (data/index/bm25_index.pkl).exists()`. That passed pre-acceptance and
then went red the moment the acceptance run legitimately built the pickle —
it was testing *absence*, when the HARD RULE is about *authorship*. An
existence check cannot tell production's artifact from a test violation.

Replaced with an `autouse` fixture, `enforce_hard_rule`, that snapshots
`(mtime_ns, size)` for every file in `data/index/` before each test and
asserts the set is unchanged after. This is strictly better than what the
spec asked for on three counts: it guards *every* test rather than U6 alone,
it catches modification and deletion rather than only creation, and it is
insensitive to whether the harness has ever been run. Confirmed to actually
fire by a throwaway test that writes a probe file into `data/index/`.

If this spec is ever re-run, the `git status` line should be replaced with
that fixture.

## D8: Failure mode 12 observed at acceptance — reported, deliberately not fixed

The spec says to eyeball the `[Source:]` lines at acceptance step 3 and say
so if every citation names the same file. Measured on the real answer
(`research_What_is_LITEs_competitive_outlook.md`, 10,639 bytes,
23 citations):

| Document | Citations |
|---|---|
| `20251125_Mizuho_Securities_LITE_..._AI_Optical_Revolution_Initiate_at_O.md` | 16 |
| `Margins Running Ahead, Multi-Quarter Revenue Growth ...` | 5 |
| `20251205_JP_Morgan_CIEN_Hardware_-_Networking-_Raising_Forecast_for_Optical_-CIE.md` | 2 |
| `20260202_JP_Morgan_COHR_..._F2Q26_Optical_Preview-_Solid_Fund.md` | (appears in compound cites) |

Not the worst case the spec measured (that was 5/5 from one document at
`top_k=5` on a single query). Six sub-questions × `top_k=5` filled the
20-chunk cap from roughly four documents. But it is still lopsided: **16 of
23 citations, ~70%, come from the single Mizuho initiation report.**

Decision: reported, not fixed. The spec is explicit that this is
retrieval quality rather than a port defect, that the osha branch behaves
identically, and that both candidate mitigations (raise
`research_bm25_top_k`, or add a per-`file_path` cap in the dedup loop at
`retriever.py:102-105`) are post-port changes. Changing either mid-build
would have altered a verbatim-copied file and invalidated the port.

Side observation for whoever picks this up: one cited "file name" is
`Margins Running Ahead, Multi-Quarter Revenue Growth Inline or Better vs.
Bullish Expectations` — a document title, not a filename. That suggests
some docstore nodes carry a title in `file_name`. U1 only asserts the field
is *present*, not that it looks like a path. Harmless for the demo,
confusing in citations.

## D9: Acceptance REPL driven via piped stdin, not an interactive terminal

The acceptance criterion says "launch the REPL." This session has no
interactive TTY, so the REPL was driven by piping the follow-up question
and `exit` into `python src/psoas.py "<first query>"`, with the first
query passed as argv (a supported entry mode per `psoas.py`'s own docstring).

Same code path, same `run_harness`, same agent loop — only the source of
stdin differs. Recorded because the transcript shows no typed input.
