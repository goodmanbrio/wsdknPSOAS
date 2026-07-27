# PMS2 Build Guide

Sequenced milestone view into `17_PMS2.md`. Spec stays as
reference; this doc routes you there.

## Build order

```
T0 ──► M0 ──► M2 ──┐
                    ├──► M3a ──► M3b ──► M4 ──► M5 ──► M6
T0 ──► M1 ──────────┘
```

**T0** (unit tests): pure Python, no LLM, no data files. Write
the code AND tests together — tests prove the module before
any integration. Safe to write in one session.

**M0–M2**: can be built in any order (M0 before M2 since mapper
needs `data/files_ingested/`). M1 independent of M0/M2.

**M3a–M6**: strictly sequential. Each builds on the previous.

---

## Cross-milestone patterns

Patterns that recur across milestones. Read once, apply everywhere.

### Agent loop: system prompt vs initial user message

All PMS2 agent loops follow the same pattern: task context
(query, firms, periods, file inventories, etc.) goes into the
**system prompt** via `load_sysprompt()` template vars. The
initial user message is just `"Begin."` This matches the
existing Dailo pattern (pumba.py). System prompt persists
across all `call_with_tools` turns; the initial user message
is ephemeral.

Applies to: Sekei (M1), Mapper (M2), Batch Planner (M3b),
Validator (M4).

### Stub message wording for stubbed tools

When a tool is stubbed for a milestone (e.g. `run_mapper` in
M1, `run_leng_caller` in M3b), the stub's return message
MUST sound like a successful completion, not a failure:

- **Bad**: `"Mapper stubbed."` or `"Not available."` → LLM
  interprets as failure, stalls or refuses to continue.
- **Good**: `"Mapper complete: 0 files catalogued."` or
  `"Leng complete: 0 cells filled."` → LLM proceeds normally.

The LLM should NOT know it's talking to a stub. Neutral
completion language keeps the agent loop flowing.

### Same-turn guard on exploration + ask_user

The spec scaffold (§ "Agent loop pattern") guards terminal
tools against co-occurrence with ask_user or exploration
tools. But **exploration tools also need a same-turn guard
against ask_user.** The scaffold doesn't enforce this — you
must add it per agent.

Why: ask_user blocks on user input. If an exploration tool
fires in the same turn, it runs concurrently and completes
before the user answers. Semantically wrong — the exploration
tool needs the user's answer (e.g. mapper needs sector folder
preference from ask_user before firing).

Code pattern:
```python
elif tc.name in EXPLORATION_TOOLS:
    if has_ask_user:
        return tc, "Error: cannot run in same turn as ask_user.", None
    result = _handle_exploration(tc)
    return tc, result, None
```

Applies to: Sekei run_mapper (M1/M2), Mapper list_dir (M2),
BP override_firm_currency (M3b).

### debug_dir as parameter

Every agent loop flushes a TraceBuffer to disk. The flush
destination (`debug_dir`) must be a parameter, not hardcoded.

- Standalone milestone tests don't have an orchestrator session
  timestamp. Pass a temp dir or `session_dir / "pms2"`.
- M3a+ pipeline passes the orchestrator's
  `tests/debug/{session_ts}/` dir.
- Default: `session_dir / "pms2"` if `debug_dir` is None.

Applies to: all agent loops (Sekei, Mapper, BP, Validator).

---

## T0: Unit Tests (Pure Python)

**Prove:** Core deterministic logic works before any LLM
integration. Every module below is testable with zero API
keys, zero network.

**Code dependency:** `hardcode_dependencies/units.json` must
exist (already in repo). `_assign_structure` reads it at
runtime. This is a checked-in code artifact, not a data file.

**Test file:** `tests/test_pms2_unit.py` (pytest)

**Prereq:** Create `src/scripts/PMS2/__init__.py` (empty)
before any T0 session — imports fail without it.

Build the module AND its tests together per session. The test
IS the acceptance gate — module done when tests pass.

**Internal ordering:** T0a must precede T0d (dry-run syntax
check imports `_safe_math_eval`). T0b/T0c must precede T0d
(`_assign_structure` calls `_topo_sort_rows`). T0e–T0j are
independent of each other and of T0a–T0d.

### T0a: stencil_safe_math.py

**Build:** `src/scripts/PMS2/stencil_safe_math.py`
(`_safe_math_eval`, `_eval_node`)

`_safe_math_eval` must wrap `ast.parse` SyntaxError →
ValueError so callers only catch one type:
```python
try:
    tree = ast.parse(expr, mode="eval")
except SyntaxError as e:
    raise ValueError(f"Unparseable expression: {expr}") from e
```

**Tests:**
```python
# Happy path
assert _safe_math_eval("2+3") == 5
assert _safe_math_eval("10/4") == 2.5
assert _safe_math_eval("(1+2)*3") == 9
assert _safe_math_eval("1.0/1.0") == 1.0       # dry-run substitute
assert _safe_math_eval("-5+3") == -2            # unary minus

# Rejection
with pytest.raises(ValueError):
    _safe_math_eval("__import__('os')")          # no builtins
with pytest.raises(ValueError):
    _safe_math_eval("2**3")                      # no exponent
with pytest.raises(ValueError):
    _safe_math_eval("@#$garbage")                # unparseable → ValueError (not SyntaxError)
with pytest.raises(ZeroDivisionError):
    _safe_math_eval("1/0")
```

### T0b: stencil_topo.py → _parse_formula_refs

**Build:** `_parse_formula_refs` in `src/scripts/PMS2/stencil_topo.py`

**Tests:**
```python
assert _parse_formula_refs("{Market Cap}+{Net Debt}") == ["Market Cap", "Net Debt"]
assert _parse_formula_refs("{EV}/{EBITDA}") == ["EV", "EBITDA"]
assert _parse_formula_refs("{Share Price}/{EPS}") == ["Share Price", "EPS"]
assert _parse_formula_refs("({Laser Rev}-{Laser Rev}[-1])/{Laser Rev}[-1]") == ["Laser Rev", "Laser Rev", "Laser Rev"]
assert _parse_formula_refs("") == []            # retrieve row, no formula
assert _parse_formula_refs("{Op Income}+{D&A}") == ["Op Income", "D&A"]
```

### T0c: stencil_topo.py → _topo_sort_rows

**Build:** `_topo_sort_rows`, `_topo_sort_firm`

**Tests:**
```python
# Happy path: 3-level chain (EV/EBITDA)
# depth 0: MktCap, NetDebt, OpInc, D&A (retrieve)
# depth 1: EV = MktCap+NetDebt, EBITDA = OpInc+D&A (compute)
# depth 2: EV/EBITDA = EV/EBITDA (compute, ans)
rows = [
    {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
     "formula": "{EV}/{EBITDA}", "unit": "float",
     "timeframe": "annual", "ans": True},
    {"metric": "EV", "firm": "LITE", "type": "compute",
     "formula": "{Market Cap}+{Net Debt}", "unit": "USD",
     "timeframe": "annual"},
    {"metric": "EBITDA", "firm": "LITE", "type": "compute",
     "formula": "{Op Income}+{D&A}", "unit": "USD",
     "timeframe": "annual"},
    {"metric": "Market Cap", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
    {"metric": "Net Debt", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
    {"metric": "Op Income", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
    {"metric": "D&A", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
]
sorted_rows = _topo_sort_rows(rows, ["LITE"])
names = [r["metric"] for r in sorted_rows]
# depth 0 before depth 1 before depth 2
assert names.index("EV/EBITDA") > names.index("EV")
assert names.index("EV/EBITDA") > names.index("EBITDA")
assert names.index("EV") > names.index("Market Cap")
assert names.index("EV") > names.index("Net Debt")
assert names.index("EBITDA") > names.index("Op Income")

# Circular dep → ValueError
circ = [
    {"metric": "EV", "firm": "LITE", "type": "compute",
     "formula": "{EV/EBITDA}", "unit": "USD", "timeframe": "annual"},
    {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
     "formula": "{EV}", "unit": "float", "timeframe": "annual"},
]
with pytest.raises(ValueError, match="Circular"):
    _topo_sort_rows(circ, ["LITE"])

# Unknown ref → ValueError
bad_ref = [
    {"metric": "P/E", "firm": "LITE", "type": "compute",
     "formula": "{Share Price}/{Earnings Per Share}", "unit": "float",
     "timeframe": "annual"},
    {"metric": "Share Price", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
]
with pytest.raises(ValueError, match="unknown metric"):
    _topo_sort_rows(bad_ref, ["LITE"])

# Duplicate metric within firm → ValueError
dupes = [
    {"metric": "Market Cap", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
    {"metric": "Market Cap", "firm": "LITE", "type": "retrieve",
     "unit": "USD", "timeframe": "annual"},
]
with pytest.raises(ValueError, match="Duplicate"):
    _topo_sort_rows(dupes, ["LITE"])

# Compute row missing formula → ValueError
no_formula = [
    {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
     "unit": "float", "timeframe": "annual"},
]
with pytest.raises(ValueError, match="no formula"):
    _topo_sort_rows(no_formula, ["LITE"])
```

### T0d: stencil_topo.py → _assign_structure

**Build:** `_assign_structure` (full 2-firm walkthrough)

**Tests** (2 firms × EV/EBITDA chain + P/E + Laser Rev):

The canonical test stencil. Per firm:
- depth 0 retrieve: MktCap, NetDebt, OpInc, D&A, SharePrice, EPS, Laser Rev
- depth 1 compute: EV=MktCap+NetDebt, EBITDA=OpInc+D&A, P/E=SharePrice/EPS
- depth 2 compute: EV/EBITDA=EV/EBITDA
- ans rows: EV/EBITDA, P/E, Laser Rev
- 11 rows per firm × 2 firms = 22 rows total

```python
# raw_rows: 22 rows (LITE USD + Innolight CNY), unordered
# (each firm: 7 retrieve + 4 compute = 11)
sorted_rows = _topo_sort_rows(raw_rows, ["LITE", "Innolight"])
periods_3 = ["FY2025", "FY2026", "FY2027"]
work, ans, jobs = _assign_structure(sorted_rows, periods_3, ["LITE", "Innolight"])

# Work stencil structure
assert len(work["rows"]) == 22                  # 11 per firm
assert work["col_letters"] == ["A", "B", "C"]
assert all(v is None for v in work["values"].values())
assert len(work["values"]) == 66                # 22 rows × 3 periods

# Rn rewrite: LITE EV/EBITDA refs LITE EV + LITE EBITDA
lite_eveb = next(r for r in work["rows"].values()
                 if r["metric"] == "EV/EBITDA" and r["firm"] == "LITE")
assert "R" in lite_eveb["formula"]              # rewritten
assert "{" not in lite_eveb["formula"]           # no raw names

# 3-level chain: EV/EBITDA row_num > EV row_num > MktCap row_num
lite_rows = {v["metric"]: int(k) for k, v in work["rows"].items()
             if v["firm"] == "LITE"}
assert lite_rows["EV/EBITDA"] > lite_rows["EV"]
assert lite_rows["EV"] > lite_rows["Market Cap"]
assert lite_rows["EV/EBITDA"] > lite_rows["EBITDA"]
assert lite_rows["EBITDA"] > lite_rows["Op Income"]

# feeds_rows: MktCap feeds EV (not EV/EBITDA directly)
lite_mc_num = str(lite_rows["Market Cap"])
lite_mc = work["rows"][lite_mc_num]
assert "feeds_rows" in lite_mc
assert lite_rows["EV"] in lite_mc["feeds_rows"]

# EV feeds EV/EBITDA
lite_ev_num = str(lite_rows["EV"])
lite_ev = work["rows"][lite_ev_num]
assert "feeds_rows" in lite_ev
assert lite_rows["EV/EBITDA"] in lite_ev["feeds_rows"]

# Laser Rev is ans + retrieve (direct, no helpers)
lite_lr_num = str(lite_rows["Laser Rev"])
lite_lr = work["rows"][lite_lr_num]
assert lite_lr.get("ans") is True
assert "feeds_rows" not in lite_lr              # no consumers

# Ans stencil
assert set(ans["metrics"]) == {"EV/EBITDA", "P/E", "Laser Rev"}
assert "LITE" in ans["row_mapping"]
assert "Innolight" in ans["row_mapping"]

# Job stencils: disjoint cell IDs
assert len(jobs) == 2
lite_cells = set(jobs[0]["values"].keys())
inno_cells = set(jobs[1]["values"].keys())
assert lite_cells & inno_cells == set()

# Cross-firm formula isolation: Innolight EV/EBITDA refs
# Innolight's rows (different Rn numbers than LITE)
inno_eveb = next(r for r in work["rows"].values()
                 if r["metric"] == "EV/EBITDA" and r["firm"] == "Innolight")
assert lite_eveb["formula"] != inno_eveb["formula"]  # different Rn refs

# Unit validation: invalid unit → ValueError
bad_unit_rows = [{"metric": "X", "firm": "A", "type": "retrieve",
                  "unit": "FAKE", "timeframe": "annual"}]
with pytest.raises(ValueError, match="not in"):
    _assign_structure(bad_unit_rows, ["FY2025"], ["A"])
```

### T0e: pms2.py → _expand_periods

**Build:** `_expand_periods` in `src/scripts/PMS2/pms2.py`

`_expand_periods` must deduplicate output (preserve first
occurrence order). Without this, `["FY2025", "Q1FY2025"]`
with `quarterly` produces duplicate Q1FY2025 → two columns
mapping to same period → stencil corruption. Use
`dict.fromkeys(expanded)` after building the list.

**Tests:**
```python
# Annual passthrough
assert _expand_periods(["FY2025", "FY2026"], "annual") == ["FY2025", "FY2026"]

# Quarterly expansion
assert _expand_periods(["FY2025"], "quarterly") == [
    "Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025"]

# Half expansion
assert _expand_periods(["FY2025"], "half") == ["H1FY2025", "H2FY2025"]

# Mixed: already-expanded pass through
assert _expand_periods(["FY2025", "Q3FY2026"], "quarterly") == [
    "Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025", "Q3FY2026"]

# Dedup: pre-expanded + FY expansion overlap → no duplicate
assert _expand_periods(["FY2025", "Q1FY2025"], "quarterly") == [
    "Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025"]

# Contradiction: Q prefix + annual → ValueError
with pytest.raises(ValueError, match="annual"):
    _expand_periods(["Q1FY2025"], "annual")
```

### T0f: chunk_overlap.py → _inject_overlap

**Build:** `_inject_overlap` in `src/scripts/PMS2/chunk_overlap.py`
(not `01_Chunk.py` — digit-prefixed modules aren't importable;
`01_Chunk.py` imports from `chunk_overlap.py` at M0)

**Tests** (mock nodes — no llama_index import needed):
```python
class MockNode:
    def __init__(self, text, metadata):
        self.text = text
        self.metadata = metadata

def make_mock_nodes(file_path, texts):
    return [MockNode(t, {"file_path": file_path, "chunk_index": i})
            for i, t in enumerate(texts)]

# 3 chunks from same file, 500 chars each
nodes = make_mock_nodes("fileA.md", texts=["A"*500, "B"*500, "C"*500])
result = _inject_overlap(nodes)

# First chunk: no front context, has back context
assert not result[0].text.startswith("--- context ---")
assert "--- context ---" in result[0].text       # has back

# Middle chunk: both front and back
assert result[1].text.count("--- context ---") == 2

# Last chunk: has front, no back
assert result[2].text.startswith("--- context ---")  # has front
last_end = result[2].text.rfind("--- end context ---")
after_last = result[2].text[last_end + len("--- end context ---"):]
assert "--- context ---" not in after_last            # no back

# Cross-file isolation: 2 files, overlap doesn't leak
nodes_multi = make_mock_nodes("f1.md", ["X"*500]) + make_mock_nodes("f2.md", ["Y"*500])
result = _inject_overlap(nodes_multi)
assert "Y" not in result[0].text
assert "X" not in result[1].text

# Max 1000 chars per side
big_nodes = make_mock_nodes("big.md", texts=["Z"*2000, "W"*500])
result = _inject_overlap(big_nodes)
back_ctx = result[0].text.split("--- context ---")[-1].split("--- end context ---")[0]
assert len(back_ctx.strip()) <= 1000
```

### T0g: denom_reconcile.py + validator_loop.py → _handle_submit_verdicts

**Build:** `src/scripts/PMS2/denom_reconcile.py` (constants)
+ `_handle_submit_verdicts` in `src/scripts/PMS2/validator_loop.py`

**Tests:**
```python
import threading

lock = threading.Lock()

# Denom normalize: Market Cap in bn → ×1e9
stencil = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}}
params = {"verdicts": [{"cell_id": "A1", "action": "write",
                         "value": 8.2, "denom": "bn", "unit": "USD"}]}
outcomes, status = _handle_submit_verdicts(params, stencil, lock, "node_123")
assert stencil["values"]["A1"] == 8_200_000_000  # MktCap $8.2bn
assert stencil["sources"]["A1"] == "node_123"
assert outcomes[0][1] == "written"

# Unit mismatch: stencil says USD, Leng found CNY → rejected
stencil2 = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}}
params2 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 55, "denom": "bn", "unit": "CNY"}]}
outcomes2, _ = _handle_submit_verdicts(params2, stencil2, lock, "node_456")
assert stencil2["values"]["A1"] is None         # not written
assert outcomes2[0][1] == "rejected"

# Compare-and-swap: EPS already filled → skipped (first write wins)
stencil3 = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": 3.42}, "sources": {"A1": "old"}}
params3 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 3.50, "denom": "units", "unit": "USD"}]}
outcomes3, _ = _handle_submit_verdicts(params3, stencil3, lock, "node_789")
assert stencil3["values"]["A1"] == 3.42         # unchanged
assert outcomes3[0][1] == "skipped"

# RMB alias → CNY (Innolight net debt in RMB)
stencil4 = {"rows": {"1": {"unit": "CNY"}}, "values": {"A1": None}, "sources": {}}
params4 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 2.1, "denom": "bn", "unit": "RMB"}]}
outcomes4, _ = _handle_submit_verdicts(params4, stencil4, lock, "node_aaa")
assert stencil4["values"]["A1"] == 2_100_000_000  # accepted, alias matched

# % denom: gross margin as percentage
stencil5 = {"rows": {"1": {"unit": "float"}}, "values": {"A1": None}, "sources": {}}
params5 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 32.8, "denom": "%", "unit": "float"}]}
outcomes5, _ = _handle_submit_verdicts(params5, stencil5, lock, "node_bbb")
assert abs(stencil5["values"]["A1"] - 0.328) < 1e-9

# bps denom: yield spread
stencil6 = {"rows": {"1": {"unit": "float"}}, "values": {"A1": None}, "sources": {}}
params6 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 150, "denom": "bps", "unit": "float"}]}
outcomes6, _ = _handle_submit_verdicts(params6, stencil6, lock, "node_bps")
assert abs(stencil6["values"]["A1"] - 0.015) < 1e-9

# Reject action: D&A figure ambiguous (could be segment or consolidated)
params7 = {"verdicts": [{"cell_id": "A1", "action": "reject",
                          "reason": "segment vs consolidated ambiguity"}]}
outcomes7, _ = _handle_submit_verdicts(params7,
    {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}},
    lock, "node_ccc")
assert outcomes7[0][1] == "rejected"

# Unknown denom → rejected (prevents "unknown": 1 in DENOM_FACTORS)
stencil8 = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}}
params8 = {"verdicts": [{"cell_id": "A1", "action": "write",
                          "value": 100, "denom": "unknown", "unit": "USD"}]}
outcomes8, _ = _handle_submit_verdicts(params8, stencil8, lock, "node_unk")
assert stencil8["values"]["A1"] is None
assert outcomes8[0][1] == "rejected"

# Phantom cell_id → rejected (Haiku hallucinated cell ID)
stencil9 = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}}
params9 = {"verdicts": [{"cell_id": "Z99", "action": "write",
                          "value": 100, "denom": "units", "unit": "USD"}]}
outcomes9, _ = _handle_submit_verdicts(params9, stencil9, lock, "node_phantom")
assert outcomes9[0][1] == "rejected"

# Concurrent compare-and-swap: 2 threads race for same cell
stencil_race = {"rows": {"1": {"unit": "USD"}}, "values": {"A1": None}, "sources": {}}
race_lock = threading.Lock()
race_results = [None, None]

def _race_writer(idx, value, node):
    p = {"verdicts": [{"cell_id": "A1", "action": "write",
                        "value": value, "denom": "units", "unit": "USD"}]}
    race_results[idx] = _handle_submit_verdicts(p, stencil_race, race_lock, node)

t1 = threading.Thread(target=_race_writer, args=(0, 100, "n1"))
t2 = threading.Thread(target=_race_writer, args=(1, 200, "n2"))
t1.start(); t2.start(); t1.join(); t2.join()

assert stencil_race["values"]["A1"] in (100, 200)   # one wins
outcomes_r = [race_results[i][0][0][1] for i in range(2)]
assert "written" in outcomes_r
assert "skipped" in outcomes_r
```

### T0h: merge_compute.py → _merge_jobs_into_work

**Build:** `_merge_jobs_into_work` in `src/scripts/PMS2/merge_compute.py`

**Tests:**
```python
# Merge LITE MktCap + Innolight MktCap into work stencil
work = {"values": {"A1": None, "A11": None}, "sources": {}}
job_lite = {"values": {"A1": 8_200_000_000}, "sources": {"A1": "node_10k"}}
job_inno = {"values": {"A11": 55_000_000_000}, "sources": {"A11": "node_ar"}}

_merge_jobs_into_work(work, [job_lite, job_inno])
assert work["values"]["A1"] == 8_200_000_000    # LITE MktCap
assert work["values"]["A11"] == 55_000_000_000  # Innolight MktCap
assert work["sources"]["A1"] == "node_10k"

# Null values in job not copied (no overwrite)
work2 = {"values": {"A1": 8_200_000_000}, "sources": {"A1": "node_10k"}}
job_null = {"values": {"A1": None}, "sources": {}}
_merge_jobs_into_work(work2, [job_null])
assert work2["values"]["A1"] == 8_200_000_000   # unchanged
```

### T0i: merge_compute.py → _compute_formula_cells

**Build:** `_compute_formula_cells`

**Tests:**
```python
# 3-level: EV/EBITDA = (MktCap+NetDebt)/(OpInc+D&A)
work = {
    "rows": {
        "1": {"type": "retrieve", "metric": "Market Cap"},
        "2": {"type": "retrieve", "metric": "Net Debt"},
        "3": {"type": "retrieve", "metric": "Op Income"},
        "4": {"type": "retrieve", "metric": "D&A"},
        "5": {"type": "compute", "metric": "EV", "formula": "R1+R2"},
        "6": {"type": "compute", "metric": "EBITDA", "formula": "R3+R4"},
        "7": {"type": "compute", "metric": "EV/EBITDA", "formula": "R5/R6"},
    },
    "col_letters": ["A"],
    "values": {
        "A1": 8_200_000_000,   # MktCap $8.2bn
        "A2": 1_500_000_000,   # NetDebt $1.5bn
        "A3": 800_000_000,     # OpInc
        "A4": 200_000_000,     # D&A
        "A5": None, "A6": None, "A7": None,
    },
    "sources": {},
}
_compute_formula_cells(work)
assert work["values"]["A5"] == 9_700_000_000    # EV = 8.2+1.5
assert work["values"]["A6"] == 1_000_000_000    # EBITDA = 800+200
assert work["values"]["A7"] == 9.7              # EV/EBITDA = 9.7/1.0
assert work["sources"]["A7"] == "formula:R5/R6"

# Null propagation: NetDebt unknown → EV null → EV/EBITDA null
work2 = deepcopy(work)
work2["values"]["A2"] = None
work2["values"]["A5"] = None
work2["values"]["A7"] = None
_compute_formula_cells(work2)
assert work2["values"]["A5"] is None            # can't compute EV
assert work2["values"]["A7"] is None            # can't compute EV/EBITDA

# Leng-direct wins: EV/EBITDA found literally in analyst note
work3 = deepcopy(work)
work3["values"]["A7"] = 10.2                    # Leng found 10.2x directly
_compute_formula_cells(work3)
assert work3["values"]["A7"] == 10.2            # unchanged, not overwritten

# P/E with cross-column: share price YoY
work4 = {
    "rows": {
        "1": {"type": "retrieve", "metric": "Share Price"},
        "2": {"type": "compute", "metric": "Price YoY",
              "formula": "(R1-R1[-1])/R1[-1]"},
    },
    "col_letters": ["A", "B"],
    "values": {"A1": 52.0, "B1": 68.0, "A2": None, "B2": None},
    "sources": {},
}
_compute_formula_cells(work4)
assert work4["values"]["A2"] is None            # no col left of A
assert abs(work4["values"]["B2"] - 0.3077) < 0.001  # (68-52)/52

# ZeroDivisionError: divisor row = 0 → null + warning, no crash
work5 = {
    "rows": {
        "1": {"type": "retrieve", "metric": "Revenue", "firm": "X"},
        "2": {"type": "compute", "metric": "GM", "formula": "R1/R1",
              "firm": "X"},
    },
    "col_letters": ["A"],
    "values": {"A1": 0, "A2": None},
    "sources": {},
}
_compute_formula_cells(work5)
assert work5["values"]["A2"] is None            # not 0/0 crash

# Divergence warning: Leng-direct vs formula > 2%
# (simulates denom miss: 32.8 stored instead of 0.328)
work6 = deepcopy(work)
work6["values"]["A5"] = None
work6["values"]["A6"] = None
work6["values"]["A7"] = 32.8                    # Leng found "32.8" raw (denom miss)
_compute_formula_cells(work6)
assert work6["values"]["A7"] == 32.8            # Leng-direct wins
# Formula would give 9.7 → divergence = |9.7-32.8|/32.8 = 70%
# Implementation must emit ⚠ warning (verify via capsys or channel mock)
```

### T0j: mapper.py → _walk_dirs

**Build:** `_walk_dirs` in `src/scripts/PMS2/mapper.py`

**Tests** (use `tmp_path` pytest fixture):
```python
def test_walk_dirs(tmp_path):
    # Setup: mimic data/files_ingested/
    (tmp_path / "LITE" / "Company").mkdir(parents=True)
    (tmp_path / "LITE" / "Company" / "results.md").write_text("x")
    (tmp_path / "LITE" / ".hidden").write_text("x")
    (tmp_path / "LITE" / "manifest.json").write_text("{}")  # should skip
    (tmp_path / "0 Optical" / "Reports").mkdir(parents=True)
    (tmp_path / "0 Optical" / "Reports" / "sector.md").write_text("xx")

    manifest = {
        "LITE/Company/results.md": "pdf",
        "0 Optical/Reports/sector.md": "pdf",
    }

    files = _walk_dirs(["LITE/", "0 Optical/"], tmp_path, manifest)
    paths = [f["path"] for f in files]

    assert "LITE/Company/results.md" in paths
    assert "0 Optical/Reports/sector.md" in paths
    assert not any(".hidden" in p for p in paths)   # dotfiles excluded
    assert not any(".json" in p for p in paths)     # json excluded
    assert all("filetype" in f for f in files)

    # Unknown filetype → "unknown"
    (tmp_path / "LITE" / "Company" / "mystery.md").write_text("x")
    files2 = _walk_dirs(["LITE/"], tmp_path, manifest)
    mystery = next(f for f in files2 if "mystery" in f["path"])
    assert mystery["filetype"] == "unknown"
```

---

## M0: Ingest

**Prove:** Two-script pipeline: `00_Ingest.py` converts
`data/files_raw/` → `data/files_ingested/` (docling pdf/docx→md),
`01_Chunk.py` chunks `data/files_ingested/` → `data/index/`
(docstore + file_path_index.json + overlap injection).

No LLM. Pure Python. Must run before everything else — M2
needs `file_path_index.json`, M5 needs the docstore.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "PMS2 Ingest (two scripts)" | Two-script split, what each does |
| "00_Ingest.py" | Docling convert, manifest.json, convert_hashes.json |
| "01_Chunk.py" | Clone-from-PMS1 scope, what changes |
| "Metadata conventions" | `file_name` vs `file_path` vs `filetype` — what goes where |
| "New: overlap injection" | `_inject_overlap` algorithm, demarcation markers |
| "What a chunk looks like after PMS2 ingest" | Expected output (chunk with context markers) |
| "Overlap and attribution" | Context-zone values are valid |
| "Shared data directory" | `data/files_raw/` → `data/files_ingested/` → `data/index/` path layout |

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/00_Ingest.py` | Docling convert: walk `data/files_raw/` for .pdf/.docx, convert → .md in mirrored `data/files_ingested/` paths. Writes `manifest.json` ({md_rel_path: original_ext}) + `convert_hashes.json` (SHA-256 for incremental). |
| `src/scripts/PMS2/01_Chunk.py` | Clone of PMS1 `ingest.py`. Reads .md from `files_ingested/`, chunks → `data/index/`. Hard dep on `manifest.json`. Changes from PMS1: output path, `_inject_overlap()`, `filetype` from manifest, `file_path_index.json`, drop `dir_implied_firm`/`dir_implied_sector`. |

### What changes from PMS1 ingest (01_Chunk only)

1. **Output path**: `data/index/` (not PMS1's index location)
2. **`_inject_overlap()`**: post-chunking pass, ±1k char overlap
   from neighboring chunks of same file, demarcated with
   `--- context ---` / `--- end context ---` markers
3. **`filetype` metadata**: read from `manifest.json` (not inferred
   from filename). Hard crash if manifest missing.
4. **`file_path_index.json`**: after chunking, build
   `{file_path: {node_ids: [...], filetype: "..."}}` and write
   alongside docstore. O(1) lookup for LengCaller
5. **`file_path` metadata**: relative to `data/files_ingested/`
6. **Dropped**: `dir_implied_firm`, `dir_implied_sector` (PMS2
   doesn't use FIRM_SYNONYMS — Mapper walks dirs directly)

7. **SentenceSplitter overlap = 0**: `_inject_overlap` is the
   sole overlap mechanism. SentenceSplitter's built-in overlap
   is redundant and disabled.

Everything else (file readers, table detection, stub header
merging, sub-table splitting, SentenceSplitter, atomic table
chunks) stays the same.

### Test

**Input:** `data/files_raw/` (LITE, Innolight, 0 Optical — pdf/docx)

**00_Ingest assertions:**
- `data/files_ingested/` mirrors `files_raw/` structure with .md files
- `data/files_ingested/manifest.json` exists, maps each .md → original ext
- `data/files_ingested/convert_hashes.json` exists
- Re-run skips unchanged files (hash-based)

**01_Chunk / docstore assertions:**
- `data/index/docstore.json` exists, non-empty
- `data/index/file_path_index.json` exists
- `data/index/file_hashes.json` exists

**file_path_index.json:**
- Keys are relative paths (e.g. `"LITE/Company/Lumentum-results-2026.md"`)
- Each entry has `node_ids` (non-empty list) and `filetype` (string)
- `filetype` matches `manifest.json` values
- Every .md file in `data/files_ingested/` has an entry

**Overlap injection:**
- Pick a chunk, inspect text: `--- context ---` markers present
- Front context is from preceding chunk, back context from following
- First chunk of a file: no front context. Last chunk: no back
- Context length ≤ 1000 chars each side
- Table chunks also get overlap (surrounding text context)

**Metadata:**
- `file_path` is relative to `data/files_ingested/`
- `file_name` is basename only
- `chunk_index` sequential per file
- `chunk_type` correct (`"text"` vs `"table"`)

### Done when

Both scripts run to completion. `manifest.json` correct.
`file_path_index.json` has entries for all files. Spot-check
3+ chunks for correct overlap markers and metadata.

---

## M1: Stenciling

**Prove:** Sekei (LLM) + Python post-processing consistently
produce valid ans/work/job stencils. Firms are **given** — no
guessing, no mapper, no extraction.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Stencil model" | 3 stencil types, flow between them |
| "Row-oriented JSON schema" + "Field reference" | Authoritative JSON format, field semantics |
| "Row-template formula notation" | Rn syntax, how Python rewrites `{MetricName}` |
| "feeds_rows semantics" | Helper-only vs dual-purpose rows, pruning logic |
| "Topo sort + cell ID assignment" | Algorithm + walkthrough. This is the core of M1 |
| "Phase 0: Sekei" — tools, turn flow, Python post-processing | Sekei tool schemas, finalize handler |
| "Agent loop pattern" | Skeleton code shared by all PMS2 agents |
| "LLM configuration" | `pms2_sekei_profile` |
| "File locations" | Where everything goes |

Skip: Mapper, Dispatcher, Batch Planner, LengCaller, Validator,
Phase 2, Trace. Not M1.

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/__init__.py` | Package |
| `src/scripts/PMS2/stencil_topo.py` | `_parse_formula_refs`, `_topo_sort_rows`, `_assign_structure`. Pure Python. No LLM |
| `src/scripts/PMS2/stencil_safe_math.py` | `_safe_math_eval`, `_eval_node`. AST-walked arithmetic eval (no eval()). Used by stencil_topo.py dry-run + merge_compute.py Phase 2 |
| `src/scripts/PMS2/sekei_loop.py` | Agent loop: `ask_user` + `finalize_stencil`. `run_mapper` **stubbed** (returns empty dict). Calls stencil_topo for post-processing |
| `src/scripts/PMS2/hardcode_dependencies/units.json` | Canonical unit set (`USD`, `JPY`, `EUR`, `GBP`, `CNY`, `float`). Already written. |
| `sysprompts/pms2_sekei/{profile}.md` | Stub prompt — sector folder selection, metric disambiguation, helper row proposal, formula syntax instructions. Firms/periods/granularity come from orchestrator (given) |

### Sekei flow for M1

Firms, periods, granularity from orchestrator. Sekei only
does stenciling (metric disambiguation + stencil design):

```
Turn 1: LLM has firms + expanded periods + query
  → ask_user("Clarify: EV = MktCap+NetDebt? EBITDA = OpInc+D&A?
              P/E = SharePrice/EPS? Laser Rev = segment revenue?
              LITE → USD, Innolight → CNY?")

Turn 2: LLM has user answers
  → ask_user("Preview: 22 rows x 3 periods. [list]. Confirm?")

Turn 3: LLM finalizes
  → finalize_stencil({firms, periods, rows})
  → Python: topo sort (3-level chain) → Rn rewrite → cell IDs
  → saved to disk

(error → Sekei sees error msg, revises, re-calls finalize)
```

### Test

**Input:** `firms=["LITE","Innolight"]`,
`periods=["FY2025","FY2026","FY2027"]`, `granularity="annual"`,
`query="EV/EBITDA, P/E, Laser Revenue"`

**Happy path assertions:**
- `work_stencil.json` exists, 22 rows (11 per firm), topo-ordered
  (retrieve before depth-1 compute before depth-2 compute)
- 3-level chain: MktCap/NetDebt → EV → EV/EBITDA. Topo order
  guarantees row(MktCap) < row(EV) < row(EV/EBITDA)
- Job stencils: one per firm, disjoint cell IDs
- `ans_stencil` `row_mapping` has EV/EBITDA + P/E + Laser Rev
- Formulas rewritten: `{Market Cap}+{Net Debt}` → `R1+R2` etc.
- `feeds_rows`: MktCap feeds EV, EV feeds EV/EBITDA (chain)
- Laser Rev: `ans: true`, no `feeds_rows` (direct retrieve, no consumers)
- All `values` null, all `sources` empty
- `col_letters` matches period count

**Error recovery:**
- Inject circular dep (EV depends on EV/EBITDA, EV/EBITDA depends on EV) → ValueError
- Inject unknown metric ref (`{Beta}/{EPS}` where Beta not defined) → ValueError

**Consistency:**
- Run 3+ times. Same query. Stencil structure equivalent
  (within-layer order may vary, Rn refs still correct)

### Done when

Sekei produces valid 3-level stencils on 3+ consecutive runs.
Manual inspection confirms topo order, Rn rewrite, feeds_rows
chain (depth 0 → 1 → 2), ans flags.

---

## M2: Mapping

**Prove:** Sekei can call Mapper with confirmed firms, Mapper
navigates `data/files_ingested/` correctly, Python file walk
+ filetype enrichment work. Firms come from orchestrator.

No stenciling. Independent axis from M1.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Shared data directory" | Directory layout, what lives where |
| "Mapper (Sekei tool: run_mapper)" | Mapper tools, system prompt, turn flow, post-processing |
| "Company directory structure" | How company dirs are organized (Company/, Notes/, Research reports/) |
| "Mapper output" | Expected output format |
| "Agent loop pattern" | Same skeleton as M1 but different tools |
| "LLM configuration" | `pms2_mapper_profile` |

Skip: Stencil model, Dispatcher, Batch Planner, LengCaller,
Validator, Phase 2.

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/mapper.py` | Per-firm Mapper agent loop (`list_dir` + `ask_user` + `report_dirs`). `_walk_dirs` post-processing (os.walk + filetype enrichment). Parallel per-firm via threading. **Same-turn guard**: `list_dir` + `ask_user` same turn → reject `list_dir`. See "Cross-milestone patterns § Same-turn guard." |
| `src/scripts/PMS2/sekei_loop.py` | Add `run_mapper` handler (was stubbed in M1). Sekei now does: ask_user sector folders → run_mapper → exit. **finalize_stencil not called** in M2 test |
| `sysprompts/pms2_sekei/{profile}.md` | Add sector folder selection instructions |
| `sysprompts/pms2_mapper/{profile}.md` | Stub prompt — list_dir strategy, flat root layout (company dirs + 0-prefixed sector dirs), fuzzy firm matching, when to ask_user |

### Sekei flow for M2

Firms from orchestrator (given). Sekei asks about sector
folders, then fires mapper:

```
Turn 1: LLM has firms
  → ask_user("Sector: include 0 Optical/? [y/n]")

Turn 2: User answers
  → run_mapper(firms=["LITE", "Innolight"],
       notes="include 0 Optical/")
     ├── Mapper LITE: list_dir("") → report_dirs(["LITE/"])
     └── Mapper Innolight: list_dir("") → report_dirs(["Innolight/"])
  → Python _walk_dirs per firm
  → return file inventories

(M2 test exits here. No stenciling.)
```

### Dependency: data/files_ingested/

Mapper calls `list_dir` against real directories. Needs actual
content in `data/files_ingested/` (at minimum a couple
of firm folders with files inside — dirs are flat at root,
no Packs/ wrapper).

### Dependency: manifest.json

M0 (Ingest) must be done first. `_walk_dirs` reads
`data/files_ingested/manifest.json` for filetype enrichment.
This file is written by `00_Ingest.py`.
(`file_path_index.json` is read later by LengCaller for
chunk lookup — separate concern.)

### Test

**Input:** `firms=["LITE","Innolight"]`,
`query="EV/EBITDA, P/E, Laser Revenue"`

**Sector folder selection:**
- Sekei asks about relevant 0-prefixed dirs
- ask_user confirmation works

**Mapper navigation:**
- Each Mapper finds correct firm dir at root (LITE/, Innolight/)
- `list_dir("")` visible in output (root listing, then optional subdir inspection)
- `report_dirs` returns reasonable dir selection

**File walk:**
- `_walk_dirs` returns list of files with paths relative to `data/files_ingested/`
- Hidden files (dotfiles) excluded
- Filetype field present (even if "unknown")
- File count is plausible for the firm

**Parallel execution:**
- Both firm mappers run concurrently (check timing or thread logs)

### Done when

Mapper returns valid file inventories for 2+ firms.
`_walk_dirs` output has correct relative paths and filetype
field. Sekei correctly handles sector folder selection.

---

## M3a: Sekei Integration + Pipeline Entry

**Prereqs:** M1 (stenciling) + M2 (mapping) both passing.

**Prove:** Full Sekei flow — sector folders, mapper, stenciling —
composes into one conversation. Mapper results stored as opaque
handles. `pms2.py` entry point works. `execute_tool` branch
wired. No Dispatcher yet.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Architecture" (full pipeline flow) | Phase 0 scope — what Sekei produces |
| "Phase 0: Sekei" — turn flow (full, including run_mapper) | T1–T4: ask_user → run_mapper → preview → finalize |
| "Mapper output" — opaque storage | `registry.store()`, `$var_N` handles |
| "ToolChannel labels" | PMS2-sekei, PMS2-map-{firm} |
| "Trace instrumentation" | Trace set-points for Phase 0 |
| "execute_tool branch" | `_exec_pms2` pattern |

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/pms2.py` | Pipeline entry: `run_pms2_pipeline(firms, query, periods, granularity, session_dir, ...)`. Expands periods via `_expand_periods()`, calls Sekei, builds display stencils via `_serialize_to_display_stencils()`, returns `list[dict]`. **No Dispatcher call yet** — returns after Phase 0 with all-null values. |
| `src/scripts/PMS2/sekei_loop.py` | Wire M1 + M2 together: full Sekei agent loop with all 3 tools live. `run_mapper` handler stores each firm's inventory via `registry.store()` → `file_inventories[firm] = "$var_N"` (handle, not raw data). Full T1–T4 flow. |
| `src/scripts/PMS2/merge_compute.py` | Add `_serialize_to_display_stencils()` — pure function, extracts per-firm ans-only display stencils from work stencil. Pulled forward from M5 because `_exec_pms2` needs `list[dict]` return shape at M3a. M5 adds `run_phase2()` around it. |
| `src/harness/execute_tool.py` | Add `_exec_pms2` function (calls `run_pms2_pipeline`, stores result stencils as opaque handles) + add `"run_pms2": _exec_pms2` to `_dispatch` table. Follows `_exec_pms1` inline pattern. |
| `src/harness/system_prompt.py` | Add `run_pms2` JSON schema to `TOOL_DEFINITIONS`. Without this, orchestrator LLM never sees the tool. |

### Sekei flow for M3a

Full conversation, all tools live:

```
Turn 1: ask_user (sector + metric disambiguation + currency)
Turn 2: run_mapper (has sector answer)
Turn 3: ask_user (preview stencil)
Turn 4: finalize_stencil → Python post-process → done
```

### Test

**Input:** `firms=["LITE","Innolight"]`,
`query="EV/EBITDA, P/E, Laser Revenue"`,
`periods=["FY2025","FY2026","FY2027"]`, `granularity="annual"`

**Sekei composition (M1 + M2 in one conversation):**
- ask_user fires before run_mapper (sequential, not same turn)
- Mapper has sector folder notes from ask_user answer
- finalize_stencil fires after mapper results available
- Full T1–T4 flow completes in one Sekei agent loop

**Stencil (M1 regression):**
- work stencil on disk (Phase 0 snapshot), structurally valid
- Same assertions as M1

**Mapper (M2 regression):**
- File inventories returned, structurally valid
- Same assertions as M2

**Opaque handles:**
- `run_mapper` result stored via `registry.store()`
- Sekei LLM sees `$var_N` handle (not full inventory)

**Pipeline entry:**
- `run_pms2_pipeline` callable from `execute_tool`
- Returns work stencil + job stencils + ans stencil + inventories
- `_expand_periods` fires before Sekei (T0e already tested)
- Artifacts written to `session_dir/pms2/`

**Traces:**
- Sekei trace, Mapper trace written to `tests/debug/`
- ToolChannel labels: PMS2-sekei, PMS2-map-{firm}

### Done when

Full Sekei flow completes: ask_user → mapper → preview →
finalize. Work stencil on disk. `execute_tool` dispatches
`run_pms2` correctly. 2+ consecutive runs without crash.

---

## M3b: Dispatcher + Batch Planner (stubbed extraction)

**Prereqs:** M3a passing.

**Prove:** Dispatcher loop + BP agent loop compose correctly.
`structured_complete` and `web_search` on LLMBackend work.
FiscalCalResolver fires for quarterly. `run_leng_caller`
stubbed — proves control flow, not extraction.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Phase 1: Dispatcher" | State, loop, exhaustion check |
| "Dispatcher state" + "searched_files structure" | DispatcherState dataclass (includes `fiscal_calendar`) |
| "Dispatcher loop" | Python loop code (includes pre-loop FiscalCalResolver) |
| "Fiscal Calendar Resolver" | `_resolve_fiscal_calendar()`, web_search flag, output format |
| "web_search flag on LLMBackend" | `web_search: bool` on `call_with_tools()` + `structured_complete()` |
| "structured_complete()" | Full method spec, AnthropicLLM implementation, parse failure retry |
| "Helper cell pruning" | `_prune_satisfied_helpers` |
| "Batch Planner" — tools, input, LLM reasoning | BP tool schemas, context forwarding |
| "ToolChannel labels" | PMS2-disp-{firm}, PMS2-batch-{firm} |

### Build

| File | What |
|---|---|
| `src/scripts/llm.py` | Add `structured_complete()` to LLMBackend base (NotImplementedError) + AnthropicLLM impl (tool-call shim, parse-failure retry). Add `web_search: bool = False` param to both `call_with_tools()` and `structured_complete()`. AnthropicLLM: `web_search_20250305` tool injection, `server_tool_use`/`web_search_tool_result` block preservation in `_parse_anthropic_response`. Add `get_pms2_fiscal_cal_llm()` factory. |
| `src/scripts/config.py` | Add `pms2_fiscal_cal_profile` config key |
| `src/scripts/PMS2/dispatcher.py` | Full Dispatcher loop. Pre-loop: FiscalCalResolver (non-annual only, `structured_complete(web_search=True)`). Inits `DispatcherState`. Calls `run_batch_planner`. Exhaustion check → `ask_user` → exit. |
| `src/scripts/PMS2/batch_planner.py` | Real LLM agent loop. `run_leng_caller` **stubbed**: returns `"complete"`, writes nothing, marks ALL files as searched (0 found). Exhaustion triggers on iteration 1. **Stub wording**: see "Cross-milestone patterns § Stub message wording." Must say `"Leng complete: 0 cells filled."` not `"stubbed"` — BP LLM stalls on failure-sounding messages. **Same-turn guard**: `override_firm_currency` + `ask_user` same turn → reject `override_firm_currency`. See "Cross-milestone patterns § Same-turn guard." |
| `src/scripts/PMS2/pms2.py` | Wire Dispatcher calls after Sekei. Spawn per-firm Dispatchers in parallel via threading. |
| `sysprompts/pms2_batch_planner/{profile}.md` | Stub prompt — file routing reasoning, plan format |

### Dispatcher flow for M3b

Per firm (parallel). Full loop but no real extraction:

```
Iteration 0:
  1. Check null ans cells → all null (18 cells)
  2. Prune helpers → all active (nothing filled)
  3. Call Batch Planner (real LLM):
     receives: job_stencil, file_inventory, searched_files,
               active_cells, iteration=0
     BP produces plan: [{file, cells}, ...]
     BP calls run_leng_caller(plan) → STUB returns "complete"
     No cells filled. BP exits.
  4. Dry run: 0 new → dry_run_count=1, continue

Iteration 1:
  1-3. Same. Still 0 cells filled.
  4. dry_run_count=2 ≥ _MAX_DRY_RUNS
     → force ask_user → user "n" → user_aborted → EXIT
```

### What's being proven

| Concern | How M3b tests it |
|---|---|
| structured_complete works | FiscalCalResolver fires (quarterly test) |
| web_search flag works | FiscalCalResolver uses `web_search=True` |
| Job stencils in-memory | Dispatcher receives from Sekei, never on disk |
| Context forwarding to BP | BP LLM receives all 5 inputs, prompt renders correctly |
| BP plan production | Valid `[{file, cells}]` plan (even though nothing executes) |
| Dispatcher exhaustion | Loop → BP → stub → dry run → ask_user → exit |
| Parallel dispatchers | Both firms run concurrently |
| Traces | Dispatcher trace, BP trace, FiscalCal trace written |

### Test

**Input:** `firms=["LITE","Innolight"]`,
`query="EV/EBITDA, P/E, Laser Revenue"`,
`periods=["FY2025","FY2026","FY2027"]`, `granularity="annual"`

**FiscalCalResolver (separate test with quarterly):**
- `granularity="quarterly"` → resolver fires per firm
- `fiscal_calendar` is dict or None (network-dependent)
- If dict: entries for each expanded period
- If None: warn, proceed with FY=CY degradation
- `granularity="annual"` → resolver does NOT fire
- Trace entry `PMS2-fiscal-{firm}` written

**Dispatcher state:**
- `DispatcherState` initialized per firm
- `job_stencil` received in-memory from Sekei output
- `file_inventory` resolved from opaque handle
- `searched_files` starts empty
- `stencil_lock` created

**Batch Planner:**
- BP receives all 5 context inputs (check trace)
- BP plan has valid file paths + valid cell IDs
- `run_leng_caller` called → stub returns "complete"

**Dry run safety net:**
- 0 new cells → dry_run_count increments
- 2 consecutive → force ask_user → user aborts
- `status = "user_aborted"` set

**End state:**
- `run_pms2_pipeline` returns display stencils (all null values)
- Display stencils stored as opaque handles in execute_tool

### Done when

Full pipeline: Sekei → Mapper → stenciling → Dispatcher →
BP → stub → exhaustion → user abort. BP produces structurally
valid plan. All traces written. 2+ consecutive runs no crash.

---

## M4: LengCaller + Leng + Validator (real extraction)

**Prereqs:** M3b passing. M0 (docstore exists with real data).

**Prove:** Un-stub `run_leng_caller`. Real chunk fetch from
docstore, real Leng `structured_complete()` calls, real
Validator agent loops, real compare-and-swap writes to job
stencil. Cells get filled. Dispatcher loop terminates on
completion or partial fill + exhaustion.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "LengCaller" | Full flow: chunk fetch → Leng × N → Validator per hit |
| "Validator" | Agent loop (max 2 turns), submit_verdicts handler |
| "submit_verdicts handler" | Unit check, denom normalize, lock, compare-and-swap |
| "denom_reconcile.py" | DENOM_FACTORS, _UNIT_ALIASES |
| "Validator → LengCaller plumbing" | cell_outcomes tuple, crash tolerance |
| "Leng" (structured_complete) | Per-chunk extraction, schema, fiscal_calendar injection |
| "searched_files structure" | Per-file: cells_searched, cells_found |
| "Helper cell pruning" | Now functional — some cells fill, helpers may prune |
| "override_firm_currency" | BP tool for systematic unit mismatch correction |
| "Trace instrumentation" | PMS2-leng-{firm}, PMS2-val-{firm} |

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/leng_caller.py` | Full LengCaller: `file_path_index` lookup → chunk fetch from docstore → fan out Leng × N (all parallel via ThreadPoolExecutor) → each hit spawns Validator immediately → collect cell_outcomes → update searched_files → return "complete". Crash-tolerant: single Validator crash = miss, not pipeline kill. |
| `src/scripts/PMS2/validator_loop.py` | Full Validator agent loop (max 2 turns). `submit_verdicts` terminal tool. `_handle_submit_verdicts` (T0g already tested). No `ask_user`. |
| `src/scripts/PMS2/denom_reconcile.py` | Already built in T0g. Constants only. |
| `src/scripts/PMS2/batch_planner.py` | Un-stub `run_leng_caller`: now calls real `leng_caller.run_leng_caller()`. Add `override_firm_currency` handler. |
| `src/scripts/PMS2/dispatcher.py` | `_prune_satisfied_helpers` now functional (cells actually fill). |
| `sysprompts/pms2_leng/{profile}.md` | Leng prompt — already stubbed. Fill: chunk context, cell descriptions, fiscal calendar, extraction instructions. |
| `sysprompts/pms2_validator/{profile}.md` | Validator prompt — already stubbed. Fill: cross-reference Leng claims against chunk text, verdict reasoning. |

### Leng structured_complete schema

Use `LENG_OUTPUT_SCHEMA` from spec (17_PMS2.md § "Leng output
contract"). **Map, not array.** Top-level `found` flag +
`cells` object keyed by cell_id:

```python
LENG_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": "true if any cell values found in this chunk",
        },
        "cells": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Value as-written in source, NOT scaled"},
                    "denom": {"type": "string", "description": "Denomination: units, k, mn, bn, tn, %, bps, or unknown"},
                    "unit": {"type": "string", "description": "Currency or type: USD, JPY, EUR, GBP, CNY, float"},
                },
                "required": ["value", "denom", "unit"],
            },
            "description": "Map of cell_id to extracted value. Only cells found in this chunk.",
        },
    },
    "required": ["found", "cells"],
}
```

LengCaller spawns Validator only when `found: true` AND
`cells` is non-empty. Hit detection: iterate `cells.items()`
(cell_id → value dict). Miss: `{"found": false, "cells": {}}`.

### Test

**Input:** Same as M3b but now cells should fill.

**Chunk fetch:**
- `file_path_index.json` lookup succeeds for planned files
- Missing file path → skip with warning (stale index test)
- Chunks sorted by `chunk_index`

**Leng extraction:**
- Leng returns `found: true` for cells present in chunk
- Leng returns `found: false` for cells not in chunk
- `structured_complete` parse failure → retry (1 retry)
- All Lengs across all files fire in parallel (timing check)

**Validator:**
- Write verdicts: denom normalized, unit checked, value written
- Reject verdicts: recorded, cell stays null
- Compare-and-swap: second Validator for same cell → skipped
- Validator crash → treated as miss, pipeline continues
- T0g assertions hold in live context

**searched_files:**
- After LengCaller returns, searched_files updated per file
- `cells_searched` matches plan entry
- `cells_found` matches Validator confirmed writes

**Helper pruning (iteration 1+):**
- If Revenue filled but GM not → Revenue stays active (GM still null)
- If GM filled directly by Leng → Revenue pruned (GM satisfied)

**End state:**
- Some cells filled (non-zero, data-dependent)
- Dispatcher exits on completion or exhaustion
- Job stencil has values + sources for filled cells

### Done when

Real extraction fills cells. At least 1 cell written via
Leng → Validator → compare-and-swap. Dispatcher loop
terminates cleanly. `searched_files` populated. 2+ runs.

---

## M5: Phase 2 — Merge + Compute

**Prereqs:** M4 passing (job stencils have filled cells).

**Prove:** Phase 2 merges job values into work stencil,
computes formula cells, extracts ans rows, serializes
display stencils. Pure Python — no LLM.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Phase 2: Merge + Compute" | Full flow: merge → compute → ans → display |
| "Step 1: Merge job values" | `_merge_jobs_into_work` |
| "Step 2: Compute formula cells" | `_compute_formula_cells`, divergence check |
| "Ans stencil" | Structure, `row_mapping`, `filled` |
| "Display stencils" | Per-firm ans-only subset for downstream |

### Build

| File | What |
|---|---|
| `src/scripts/PMS2/merge_compute.py` | `_merge_jobs_into_work` (T0h), `_compute_formula_cells` (T0i), `_extract_ans_stencil`, `run_phase2`. `_serialize_to_display_stencils` already exists from M3a — M5 adds `run_phase2()` which calls merge → compute → ans → serialize → persist as one orchestrated sequence. Persist `work_stencil.json` + `ans_stencil.json` to `session_dir/pms2/`. |
| `src/scripts/PMS2/pms2.py` | Wire Phase 2 after all Dispatchers complete. Call `run_phase2`. Build return string + opaque handles for display stencils. |

### Test

**Input:** M4's output (job stencils with filled cells).

**Merge (T0h regression):**
- Work stencil values populated from job stencils
- Sources copied correctly
- No cross-firm collision (disjoint cell IDs)

**Compute (T0i regression):**
- Formula cells evaluated in ascending row order
- Null deps → cell stays null
- Leng-direct values not overwritten
- Divergence warning printed if Leng-direct vs formula > 2%
- Cross-column refs (Rn[-1]) resolve correctly
- Sources for computed cells: `"formula:R2/R1"` etc.

**Ans stencil:**
- `filled` map populated from work stencil ans rows
- `row_mapping` matches work stencil ans rows per firm
- `metrics` list correct (user-requested only)

**Display stencils:**
- One per firm, ans rows only
- Stored as opaque handles in execute_tool

**Persistence:**
- `session_dir/pms2/work_stencil.json` written
- `session_dir/pms2/ans_stencil.json` written
- Job stencils NOT on disk (ephemeral)

### Done when

Phase 2 completes. Formula cells computed where possible.
Ans stencil has filled values. Display stencils returned.
Work + ans stencils persisted. 2+ runs.

---

## M6: End-to-End

**Prereqs:** M5 passing.

**Prove:** Full pipeline with no stubs, real data, real LLMs,
real extraction. Orchestrator → `run_pms2` → Phase 0 → Phase 1
→ Phase 2 → display stencils. Manual analyst review of output.

### Read (17_PMS2.md)

| Section | Why |
|---|---|
| "Ideal demo" | Expected CLI output, analyst workflow |
| "Input contract" | What orchestrator passes |
| "Output contract" | What `run_pms2_pipeline` returns |
| "Orchestrator sysprompt update" | `run_pms2` tool schema in orchestrator |

### Build

| File | What |
|---|---|
| `sysprompts/orchestrator/{profile}.md` | Add `run_pms2` tool schema, usage examples |
| `src/scripts/PMS2/pms2.py` | Startup staleness check (`_check_index_staleness`) |

### Test

**Demo 1: Annual, 2 firms — valuation multiples + segment**
```
firms=["LITE","Innolight"], query="EV/EBITDA, P/E, Laser Revenue",
periods=["FY2025","FY2026"], granularity="annual"
```
- Full pipeline completes
- 3-level chain computes: MktCap+NetDebt→EV, OpInc+D&A→EBITDA, EV/EBITDA
- P/E computed from SharePrice/EPS
- Laser Rev extracted directly (segment-level retrieve)
- Ans stencil has non-null values for at least some cells
- All traces written to `tests/debug/{session_ts}/`
- Orchestrator can invoke via `run_pms2` tool

**Demo 2: Quarterly, 1 firm (FiscalCalResolver exercised)**
```
firms=["LITE"], query="EPS, Laser Revenue",
periods=["FY2025"], granularity="quarterly"
```
- `_expand_periods` produces Q1-Q4FY2025
- FiscalCalResolver fires, produces calendar or degrades
- Leng receives fiscal_calendar (LITE FY ends June 30)
- Some quarterly cells fill

**Demo 3: Edge cases**
- Firm not in `data/files_ingested/` → Sekei flags, mapper handles
- Formula with cross-column ref (segment rev YoY growth) → first period null
- `override_firm_currency` triggered (Innolight reports in USD not CNY)

### Done when

Full demo runs. Analyst reviews output, confirms values
plausible. Pipeline handles both annual and quarterly.
No crashes on 3+ consecutive runs with varying inputs.
