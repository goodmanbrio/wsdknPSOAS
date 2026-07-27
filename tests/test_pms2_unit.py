"""T0 unit tests for PMS2. Pure Python, no LLM, no data files.

Tests prove each module's deterministic logic before any integration.
See Spec 17a § T0 for canonical test vectors.
"""

import sys
import threading
from copy import deepcopy
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.scripts.PMS2.stencil_safe_math import _safe_math_eval
from src.scripts.PMS2.stencil_topo import (
    _assign_structure,
    _parse_formula_refs,
    _topo_sort_rows,
)
from src.scripts.PMS2.pms2 import _expand_periods
from src.scripts.PMS2.chunk_overlap import _inject_overlap
from src.scripts.PMS2.validator_loop import _handle_submit_verdicts
from src.scripts.PMS2.merge_compute import (
    _compute_formula_cells,
    _fill_ans_stencil,
    _merge_jobs_into_work,
    _persist_phase2,
    run_phase2,
)
from src.scripts.PMS2.mapper import _walk_dirs


# =====================================================================
# T0a: stencil_safe_math.py
# =====================================================================

class TestSafeMathEval:
    def test_add(self):
        assert _safe_math_eval("2+3") == 5

    def test_div(self):
        assert _safe_math_eval("10/4") == 2.5

    def test_parens(self):
        assert _safe_math_eval("(1+2)*3") == 9

    def test_float_div(self):
        assert _safe_math_eval("1.0/1.0") == 1.0

    def test_unary_minus(self):
        assert _safe_math_eval("-5+3") == -2

    def test_reject_import(self):
        with pytest.raises(ValueError):
            _safe_math_eval("__import__('os')")

    def test_reject_exponent(self):
        with pytest.raises(ValueError):
            _safe_math_eval("2**3")

    def test_unparseable_becomes_valueerror(self):
        with pytest.raises(ValueError):
            _safe_math_eval("@#$garbage")

    def test_zero_division(self):
        with pytest.raises(ZeroDivisionError):
            _safe_math_eval("1/0")


# =====================================================================
# T0b: stencil_topo.py -> _parse_formula_refs
# =====================================================================

class TestParseFormulaRefs:
    def test_two_refs(self):
        assert _parse_formula_refs("{Market Cap}+{Net Debt}") == [
            "Market Cap", "Net Debt",
        ]

    def test_division(self):
        assert _parse_formula_refs("{EV}/{EBITDA}") == ["EV", "EBITDA"]

    def test_pe_ratio(self):
        assert _parse_formula_refs("{Share Price}/{EPS}") == [
            "Share Price", "EPS",
        ]

    def test_cross_column_duplicates(self):
        result = _parse_formula_refs(
            "({Laser Rev}-{Laser Rev}[-1])/{Laser Rev}[-1]"
        )
        assert result == ["Laser Rev", "Laser Rev", "Laser Rev"]

    def test_empty_string(self):
        assert _parse_formula_refs("") == []

    def test_ampersand_in_name(self):
        assert _parse_formula_refs("{Op Income}+{D&A}") == [
            "Op Income", "D&A",
        ]


# =====================================================================
# T0c: stencil_topo.py -> _topo_sort_rows
# =====================================================================

class TestTopoSortRows:
    def test_three_level_chain(self):
        """EV/EBITDA (depth 2) depends on EV and EBITDA (depth 1),
        which depend on retrieves (depth 0)."""
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
        assert names.index("EV/EBITDA") > names.index("EV")
        assert names.index("EV/EBITDA") > names.index("EBITDA")
        assert names.index("EV") > names.index("Market Cap")
        assert names.index("EV") > names.index("Net Debt")
        assert names.index("EBITDA") > names.index("Op Income")

    def test_circular_dep(self):
        circ = [
            {"metric": "EV", "firm": "LITE", "type": "compute",
             "formula": "{EV/EBITDA}", "unit": "USD",
             "timeframe": "annual"},
            {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
             "formula": "{EV}", "unit": "float",
             "timeframe": "annual"},
        ]
        with pytest.raises(ValueError, match="Circular"):
            _topo_sort_rows(circ, ["LITE"])

    def test_unknown_ref(self):
        bad_ref = [
            {"metric": "P/E", "firm": "LITE", "type": "compute",
             "formula": "{Share Price}/{Earnings Per Share}",
             "unit": "float", "timeframe": "annual"},
            {"metric": "Share Price", "firm": "LITE", "type": "retrieve",
             "unit": "USD", "timeframe": "annual"},
        ]
        with pytest.raises(ValueError, match="unknown metric"):
            _topo_sort_rows(bad_ref, ["LITE"])

    def test_duplicate_metric(self):
        dupes = [
            {"metric": "Market Cap", "firm": "LITE", "type": "retrieve",
             "unit": "USD", "timeframe": "annual"},
            {"metric": "Market Cap", "firm": "LITE", "type": "retrieve",
             "unit": "USD", "timeframe": "annual"},
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            _topo_sort_rows(dupes, ["LITE"])

    def test_compute_missing_formula(self):
        no_formula = [
            {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
             "unit": "float", "timeframe": "annual"},
        ]
        with pytest.raises(ValueError, match="no formula"):
            _topo_sort_rows(no_formula, ["LITE"])


# =====================================================================
# T0d: stencil_topo.py -> _assign_structure
# =====================================================================

def _make_canonical_raw_rows():
    """Build the canonical 2-firm test stencil (11 rows per firm).

    Per firm:
      depth 0 retrieve: MktCap, NetDebt, OpInc, D&A, SharePrice, EPS, Laser Rev
      depth 1 compute: EV, EBITDA, P/E
      depth 2 compute: EV/EBITDA
      ans rows: EV/EBITDA, P/E, Laser Rev
    """
    def _firm_block(firm, unit):
        return [
            # Deliberately unordered to prove topo sort
            {"metric": "EV/EBITDA", "firm": firm, "type": "compute",
             "formula": "{EV}/{EBITDA}", "unit": "float",
             "timeframe": "annual", "ans": True},
            {"metric": "P/E", "firm": firm, "type": "compute",
             "formula": "{Share Price}/{EPS}", "unit": "float",
             "timeframe": "annual", "ans": True},
            {"metric": "EV", "firm": firm, "type": "compute",
             "formula": "{Market Cap}+{Net Debt}", "unit": unit,
             "timeframe": "annual"},
            {"metric": "EBITDA", "firm": firm, "type": "compute",
             "formula": "{Op Income}+{D&A}", "unit": unit,
             "timeframe": "annual"},
            {"metric": "D&A", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "Market Cap", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "Net Debt", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "Op Income", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "Share Price", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "EPS", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual"},
            {"metric": "Laser Rev", "firm": firm, "type": "retrieve",
             "unit": unit, "timeframe": "annual", "ans": True},
        ]
    return _firm_block("LITE", "USD") + _firm_block("Innolight", "CNY")


class TestAssignStructure:
    def test_canonical_two_firm(self):
        """Full 2-firm walkthrough: 11 rows/firm, 3 periods."""
        raw_rows = _make_canonical_raw_rows()
        sorted_rows = _topo_sort_rows(raw_rows, ["LITE", "Innolight"])
        periods_3 = ["FY2025", "FY2026", "FY2027"]
        work, ans, jobs = _assign_structure(
            sorted_rows, periods_3, ["LITE", "Innolight"]
        )

        # Work stencil structure
        # (See 17b_PMS2noDiscretion.md: 11 per firm, not 10)
        assert len(work["rows"]) == 22
        assert work["col_letters"] == ["A", "B", "C"]
        assert all(v is None for v in work["values"].values())
        assert len(work["values"]) == 66  # 22 rows x 3 periods

        # Rn rewrite: LITE EV/EBITDA refs LITE EV + LITE EBITDA
        lite_eveb = next(
            r for r in work["rows"].values()
            if r["metric"] == "EV/EBITDA" and r["firm"] == "LITE"
        )
        assert "R" in lite_eveb["formula"]
        assert "{" not in lite_eveb["formula"]

        # 3-level chain ordering
        lite_rows = {
            v["metric"]: int(k) for k, v in work["rows"].items()
            if v["firm"] == "LITE"
        }
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
        assert "feeds_rows" not in lite_lr

        # Ans stencil
        assert set(ans["metrics"]) == {"EV/EBITDA", "P/E", "Laser Rev"}
        assert "LITE" in ans["row_mapping"]
        assert "Innolight" in ans["row_mapping"]

        # Job stencils: disjoint cell IDs
        assert len(jobs) == 2
        lite_cells = set(jobs[0]["values"].keys())
        inno_cells = set(jobs[1]["values"].keys())
        assert lite_cells & inno_cells == set()

        # Cross-firm formula isolation
        inno_eveb = next(
            r for r in work["rows"].values()
            if r["metric"] == "EV/EBITDA" and r["firm"] == "Innolight"
        )
        assert lite_eveb["formula"] != inno_eveb["formula"]

    def test_invalid_unit(self):
        bad_unit_rows = [
            {"metric": "X", "firm": "A", "type": "retrieve",
             "unit": "FAKE", "timeframe": "annual"},
        ]
        with pytest.raises(ValueError, match="not in"):
            _assign_structure(bad_unit_rows, ["FY2025"], ["A"])


# =====================================================================
# T0e: pms2.py -> _expand_periods
# =====================================================================

class TestExpandPeriods:
    def test_annual_passthrough(self):
        assert _expand_periods(
            ["FY2025", "FY2026"], "annual"
        ) == ["FY2025", "FY2026"]

    def test_quarterly_expansion(self):
        assert _expand_periods(["FY2025"], "quarterly") == [
            "Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025",
        ]

    def test_half_expansion(self):
        assert _expand_periods(["FY2025"], "half") == [
            "H1FY2025", "H2FY2025",
        ]

    def test_mixed_already_expanded(self):
        assert _expand_periods(
            ["FY2025", "Q3FY2026"], "quarterly"
        ) == [
            "Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025", "Q3FY2026",
        ]

    def test_dedup(self):
        assert _expand_periods(
            ["FY2025", "Q1FY2025"], "quarterly"
        ) == ["Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025"]

    def test_annual_with_quarter_prefix_raises(self):
        with pytest.raises(ValueError, match="annual"):
            _expand_periods(["Q1FY2025"], "annual")


# =====================================================================
# T0f: chunk_overlap.py -> _inject_overlap
# =====================================================================

class MockNode:
    def __init__(self, text, metadata):
        self.text = text
        self.metadata = metadata


def _make_mock_nodes(file_path, texts):
    return [
        MockNode(t, {"file_path": file_path, "chunk_index": i})
        for i, t in enumerate(texts)
    ]


class TestInjectOverlap:
    def test_three_chunks_same_file(self):
        nodes = _make_mock_nodes("fileA.md", ["A" * 500, "B" * 500, "C" * 500])
        result = _inject_overlap(nodes)

        # First chunk: no front context, has back context
        assert not result[0].text.startswith("--- context ---")
        assert "--- context ---" in result[0].text

        # Middle chunk: both front and back
        assert result[1].text.count("--- context ---") == 2

        # Last chunk: has front, no back
        assert result[2].text.startswith("--- context ---")
        last_end = result[2].text.rfind("--- end context ---")
        after_last = result[2].text[last_end + len("--- end context ---"):]
        assert "--- context ---" not in after_last

    def test_cross_file_isolation(self):
        nodes_multi = (
            _make_mock_nodes("f1.md", ["X" * 500])
            + _make_mock_nodes("f2.md", ["Y" * 500])
        )
        result = _inject_overlap(nodes_multi)
        assert "Y" not in result[0].text
        assert "X" not in result[1].text

    def test_max_1000_chars_per_side(self):
        big_nodes = _make_mock_nodes("big.md", ["Z" * 2000, "W" * 500])
        result = _inject_overlap(big_nodes)
        back_ctx = (
            result[0].text
            .split("--- context ---")[-1]
            .split("--- end context ---")[0]
        )
        assert len(back_ctx.strip()) <= 1000


# =====================================================================
# T0g: denom_reconcile.py + validator_loop.py
# =====================================================================

class TestHandleSubmitVerdicts:
    def setup_method(self):
        self.lock = threading.Lock()

    def test_denom_normalize_bn(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 8.2, "denom": "bn", "unit": "USD"},
        ]}
        outcomes, status = _handle_submit_verdicts(
            params, stencil, self.lock, "node_123"
        )
        assert abs(stencil["values"]["A1"] - 8_200_000_000) < 1
        assert stencil["sources"]["A1"] == "node_123"
        assert outcomes[0][1] == "written"

    def test_unit_mismatch_rejected(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 55, "denom": "bn", "unit": "CNY"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_456"
        )
        assert stencil["values"]["A1"] is None
        assert outcomes[0][1] == "rejected"

    def test_compare_and_swap_skipped(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": 3.42},
            "sources": {"A1": "old"},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 3.50, "denom": "units", "unit": "USD"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_789"
        )
        assert stencil["values"]["A1"] == 3.42
        assert outcomes[0][1] == "skipped"

    def test_rmb_alias(self):
        stencil = {
            "rows": {"1": {"unit": "CNY"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 2.1, "denom": "bn", "unit": "RMB"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_aaa"
        )
        assert stencil["values"]["A1"] == 2_100_000_000

    def test_percent_denom(self):
        stencil = {
            "rows": {"1": {"unit": "float"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 32.8, "denom": "%", "unit": "float"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_bbb"
        )
        assert abs(stencil["values"]["A1"] - 0.328) < 1e-9

    def test_bps_denom(self):
        stencil = {
            "rows": {"1": {"unit": "float"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 150, "denom": "bps", "unit": "float"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_bps"
        )
        assert abs(stencil["values"]["A1"] - 0.015) < 1e-9

    def test_reject_action(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "reject",
             "reason": "segment vs consolidated ambiguity"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_ccc"
        )
        assert outcomes[0][1] == "rejected"

    def test_unknown_denom_rejected(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "A1", "action": "write",
             "value": 100, "denom": "unknown", "unit": "USD"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_unk"
        )
        assert stencil["values"]["A1"] is None
        assert outcomes[0][1] == "rejected"

    def test_phantom_cell_id_rejected(self):
        stencil = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        params = {"verdicts": [
            {"cell_id": "Z99", "action": "write",
             "value": 100, "denom": "units", "unit": "USD"},
        ]}
        outcomes, _ = _handle_submit_verdicts(
            params, stencil, self.lock, "node_phantom"
        )
        assert outcomes[0][1] == "rejected"

    def test_concurrent_compare_and_swap(self):
        stencil_race = {
            "rows": {"1": {"unit": "USD"}},
            "values": {"A1": None},
            "sources": {},
        }
        race_lock = threading.Lock()
        race_results = [None, None]

        def _race_writer(idx, value, node):
            p = {"verdicts": [
                {"cell_id": "A1", "action": "write",
                 "value": value, "denom": "units", "unit": "USD"},
            ]}
            race_results[idx] = _handle_submit_verdicts(
                p, stencil_race, race_lock, node
            )

        t1 = threading.Thread(target=_race_writer, args=(0, 100, "n1"))
        t2 = threading.Thread(target=_race_writer, args=(1, 200, "n2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert stencil_race["values"]["A1"] in (100, 200)
        outcomes_r = [race_results[i][0][0][1] for i in range(2)]
        assert "written" in outcomes_r
        assert "skipped" in outcomes_r


# =====================================================================
# T0h: merge_compute.py -> _merge_jobs_into_work
# =====================================================================

class TestMergeJobsIntoWork:
    def test_merge_two_firms(self):
        work = {"values": {"A1": None, "A11": None}, "sources": {}}
        job_lite = {
            "values": {"A1": 8_200_000_000},
            "sources": {"A1": "node_10k"},
        }
        job_inno = {
            "values": {"A11": 55_000_000_000},
            "sources": {"A11": "node_ar"},
        }
        _merge_jobs_into_work(work, [job_lite, job_inno])
        assert work["values"]["A1"] == 8_200_000_000
        assert work["values"]["A11"] == 55_000_000_000
        assert work["sources"]["A1"] == "node_10k"

    def test_null_values_not_copied(self):
        work = {
            "values": {"A1": 8_200_000_000},
            "sources": {"A1": "node_10k"},
        }
        job_null = {"values": {"A1": None}, "sources": {}}
        _merge_jobs_into_work(work, [job_null])
        assert work["values"]["A1"] == 8_200_000_000


# =====================================================================
# T0i: merge_compute.py -> _compute_formula_cells
# =====================================================================

class TestComputeFormulaCells:
    def test_three_level_chain(self):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Market Cap"},
                "2": {"type": "retrieve", "metric": "Net Debt"},
                "3": {"type": "retrieve", "metric": "Op Income"},
                "4": {"type": "retrieve", "metric": "D&A"},
                "5": {"type": "compute", "metric": "EV",
                      "formula": "R1+R2"},
                "6": {"type": "compute", "metric": "EBITDA",
                      "formula": "R3+R4"},
                "7": {"type": "compute", "metric": "EV/EBITDA",
                      "formula": "R5/R6"},
            },
            "col_letters": ["A"],
            "values": {
                "A1": 8_200_000_000,
                "A2": 1_500_000_000,
                "A3": 800_000_000,
                "A4": 200_000_000,
                "A5": None,
                "A6": None,
                "A7": None,
            },
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A5"] == 9_700_000_000
        assert work["values"]["A6"] == 1_000_000_000
        assert work["values"]["A7"] == 9.7
        assert work["sources"]["A7"] == "formula:R5/R6"

    def test_null_propagation(self):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Market Cap"},
                "2": {"type": "retrieve", "metric": "Net Debt"},
                "3": {"type": "retrieve", "metric": "Op Income"},
                "4": {"type": "retrieve", "metric": "D&A"},
                "5": {"type": "compute", "metric": "EV",
                      "formula": "R1+R2"},
                "6": {"type": "compute", "metric": "EBITDA",
                      "formula": "R3+R4"},
                "7": {"type": "compute", "metric": "EV/EBITDA",
                      "formula": "R5/R6"},
            },
            "col_letters": ["A"],
            "values": {
                "A1": 8_200_000_000,
                "A2": None,  # NetDebt unknown
                "A3": 800_000_000,
                "A4": 200_000_000,
                "A5": None,
                "A6": None,
                "A7": None,
            },
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A5"] is None  # can't compute EV
        assert work["values"]["A7"] is None  # can't compute EV/EBITDA

    def test_leng_direct_wins(self):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Market Cap"},
                "2": {"type": "retrieve", "metric": "Net Debt"},
                "3": {"type": "retrieve", "metric": "Op Income"},
                "4": {"type": "retrieve", "metric": "D&A"},
                "5": {"type": "compute", "metric": "EV",
                      "formula": "R1+R2"},
                "6": {"type": "compute", "metric": "EBITDA",
                      "formula": "R3+R4"},
                "7": {"type": "compute", "metric": "EV/EBITDA",
                      "formula": "R5/R6"},
            },
            "col_letters": ["A"],
            "values": {
                "A1": 8_200_000_000,
                "A2": 1_500_000_000,
                "A3": 800_000_000,
                "A4": 200_000_000,
                "A5": None,
                "A6": None,
                "A7": 10.2,  # Leng found directly
            },
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A7"] == 10.2  # unchanged

    def test_cross_column_ref(self):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Share Price"},
                "2": {"type": "compute", "metric": "Price YoY",
                      "formula": "(R1-R1[-1])/R1[-1]"},
            },
            "col_letters": ["A", "B"],
            "values": {"A1": 52.0, "B1": 68.0, "A2": None, "B2": None},
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A2"] is None  # no col left of A
        assert abs(work["values"]["B2"] - 0.3077) < 0.001

    def test_zero_division_no_crash(self):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Revenue",
                      "firm": "X"},
                "2": {"type": "compute", "metric": "GM",
                      "formula": "R1/R1", "firm": "X"},
            },
            "col_letters": ["A"],
            "values": {"A1": 0, "A2": None},
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A2"] is None

    def test_divergence_warning(self, capsys):
        work = {
            "rows": {
                "1": {"type": "retrieve", "metric": "Market Cap",
                      "firm": "LITE"},
                "2": {"type": "retrieve", "metric": "Net Debt",
                      "firm": "LITE"},
                "3": {"type": "retrieve", "metric": "Op Income",
                      "firm": "LITE"},
                "4": {"type": "retrieve", "metric": "D&A",
                      "firm": "LITE"},
                "5": {"type": "compute", "metric": "EV",
                      "formula": "R1+R2", "firm": "LITE"},
                "6": {"type": "compute", "metric": "EBITDA",
                      "formula": "R3+R4", "firm": "LITE"},
                "7": {"type": "compute", "metric": "EV/EBITDA",
                      "formula": "R5/R6", "firm": "LITE"},
            },
            "col_letters": ["A"],
            "values": {
                "A1": 8_200_000_000,
                "A2": 1_500_000_000,
                "A3": 800_000_000,
                "A4": 200_000_000,
                "A5": None,
                "A6": None,
                "A7": 32.8,  # denom miss: 32.8 stored instead of 0.328
            },
            "sources": {},
        }
        _compute_formula_cells(work)
        assert work["values"]["A7"] == 32.8  # Leng-direct wins
        captured = capsys.readouterr()
        assert "divergence" in captured.out.lower() or "⚠" in captured.out


# =====================================================================
# T0j: mapper.py -> _walk_dirs
# =====================================================================

class TestWalkDirs:
    def test_walk_dirs(self, tmp_path):
        (tmp_path / "LITE" / "Company").mkdir(parents=True)
        (tmp_path / "LITE" / "Company" / "results.md").write_text("x")
        (tmp_path / "LITE" / ".hidden").write_text("x")
        (tmp_path / "LITE" / "manifest.json").write_text("{}")
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
        assert not any(".hidden" in p for p in paths)
        assert not any(".json" in p for p in paths)
        assert all("filetype" in f for f in files)

    def test_unknown_filetype(self, tmp_path):
        (tmp_path / "LITE" / "Company").mkdir(parents=True)
        (tmp_path / "LITE" / "Company" / "mystery.md").write_text("x")

        files = _walk_dirs(["LITE/"], tmp_path, {})
        mystery = next(f for f in files if "mystery" in f["path"])
        assert mystery["filetype"] == "unknown"


# =====================================================================
# M1: sekei_loop.py -> _handle_finalize (deterministic, no LLM)
# =====================================================================

from src.scripts.PMS2.sekei_loop import _handle_finalize


class _StubChannel:
    """Minimal ToolChannel stub for testing."""
    def __init__(self):
        self.messages = []
    def print(self, msg, **kw):
        self.messages.append(msg)
    def input(self, question, **kw):
        return "y"


class TestHandleFinalize:
    def test_happy_path_two_firm(self, tmp_path):
        """Full 22-row stencil: 2 firms × 11 rows, 3 periods."""
        raw_rows = _make_canonical_raw_rows()
        params = {
            "firms": ["LITE", "Innolight"],
            "periods": ["FY2025", "FY2026", "FY2027"],
            "rows": raw_rows,
        }
        ch = _StubChannel()
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE", "Innolight"],
            expanded_periods=["FY2025", "FY2026", "FY2027"],
            file_inventories={},
            session_dir=tmp_path,
            channel=ch,
        )

        assert data is not None, f"Expected success, got error: {status}"
        work, ans, jobs, file_inv = data

        # Structural checks
        assert len(work["rows"]) == 22
        assert work["col_letters"] == ["A", "B", "C"]
        assert len(work["values"]) == 66
        assert all(v is None for v in work["values"].values())
        assert work["sources"] == {}

        # Ans stencil
        assert set(ans["metrics"]) == {"EV/EBITDA", "P/E", "Laser Rev"}
        assert "LITE" in ans["row_mapping"]
        assert "Innolight" in ans["row_mapping"]

        # Job stencils
        assert len(jobs) == 2
        assert jobs[0]["firm"] == "LITE"
        assert jobs[1]["firm"] == "Innolight"
        lite_cells = set(jobs[0]["values"].keys())
        inno_cells = set(jobs[1]["values"].keys())
        assert lite_cells & inno_cells == set()

        # Topo order: retrieve < depth-1 compute < depth-2 compute
        lite_rows = {
            v["metric"]: int(k)
            for k, v in work["rows"].items()
            if v["firm"] == "LITE"
        }
        assert lite_rows["EV/EBITDA"] > lite_rows["EV"]
        assert lite_rows["EV"] > lite_rows["Market Cap"]
        assert lite_rows["EV/EBITDA"] > lite_rows["EBITDA"]
        assert lite_rows["EBITDA"] > lite_rows["Op Income"]

        # feeds_rows chain
        lite_mc_num = str(lite_rows["Market Cap"])
        assert lite_rows["EV"] in work["rows"][lite_mc_num]["feeds_rows"]
        lite_ev_num = str(lite_rows["EV"])
        assert lite_rows["EV/EBITDA"] in work["rows"][lite_ev_num]["feeds_rows"]

        # Laser Rev: ans=True, no feeds_rows
        lite_lr_num = str(lite_rows["Laser Rev"])
        assert work["rows"][lite_lr_num].get("ans") is True
        assert "feeds_rows" not in work["rows"][lite_lr_num]

        # Formula rewrite
        lite_eveb = next(
            r for r in work["rows"].values()
            if r["metric"] == "EV/EBITDA" and r["firm"] == "LITE"
        )
        assert "R" in lite_eveb["formula"]
        assert "{" not in lite_eveb["formula"]

        # Cross-firm formula isolation
        inno_eveb = next(
            r for r in work["rows"].values()
            if r["metric"] == "EV/EBITDA" and r["firm"] == "Innolight"
        )
        assert lite_eveb["formula"] != inno_eveb["formula"]

        # Disk persistence
        stencil_path = tmp_path / "pms2" / "work_stencil.json"
        assert stencil_path.exists()
        import json
        disk_work = json.loads(stencil_path.read_text())
        assert len(disk_work["rows"]) == 22

        # file_inventories empty (M1 stub)
        assert file_inv == {}

    def test_extra_firm_rejected(self, tmp_path):
        params = {
            "firms": ["LITE", "FAKE_CORP"],
            "periods": ["FY2025"],
            "rows": [{"metric": "Rev", "firm": "LITE",
                       "type": "retrieve", "unit": "USD",
                       "timeframe": "annual"}],
        }
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE"],
            expanded_periods=["FY2025"],
            file_inventories={},
            session_dir=tmp_path,
            channel=_StubChannel(),
        )
        assert data is None
        assert "FAKE_CORP" in status

    def test_extra_period_rejected(self, tmp_path):
        params = {
            "firms": ["LITE"],
            "periods": ["FY2025", "FY2099"],
            "rows": [{"metric": "Rev", "firm": "LITE",
                       "type": "retrieve", "unit": "USD",
                       "timeframe": "annual"}],
        }
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE"],
            expanded_periods=["FY2025"],
            file_inventories={},
            session_dir=tmp_path,
            channel=_StubChannel(),
        )
        assert data is None
        assert "FY2099" in status

    def test_period_reordering_canonical(self, tmp_path):
        """Sekei submits periods in wrong order → canonical order preserved."""
        params = {
            "firms": ["LITE"],
            "periods": ["FY2027", "FY2025"],  # wrong order
            "rows": [{"metric": "Rev", "firm": "LITE",
                       "type": "retrieve", "unit": "USD",
                       "timeframe": "annual", "ans": True}],
        }
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE"],
            expanded_periods=["FY2025", "FY2026", "FY2027"],
            file_inventories={},
            session_dir=tmp_path,
            channel=_StubChannel(),
        )
        assert data is not None
        work = data[0]
        # Canonical order: FY2025 before FY2027
        assert work["periods"] == ["FY2025", "FY2027"]
        assert work["col_letters"] == ["A", "B"]

    def test_circular_dep_error(self, tmp_path):
        params = {
            "firms": ["LITE"],
            "periods": ["FY2025"],
            "rows": [
                {"metric": "EV", "firm": "LITE", "type": "compute",
                 "formula": "{EV/EBITDA}", "unit": "USD",
                 "timeframe": "annual"},
                {"metric": "EV/EBITDA", "firm": "LITE", "type": "compute",
                 "formula": "{EV}", "unit": "float",
                 "timeframe": "annual"},
            ],
        }
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE"],
            expanded_periods=["FY2025"],
            file_inventories={},
            session_dir=tmp_path,
            channel=_StubChannel(),
        )
        assert data is None
        assert "Circular" in status

    def test_missing_firm_warning(self, tmp_path):
        """Sekei drops a firm → warning printed, not error."""
        params = {
            "firms": ["LITE"],  # missing Innolight
            "periods": ["FY2025"],
            "rows": [{"metric": "Rev", "firm": "LITE",
                       "type": "retrieve", "unit": "USD",
                       "timeframe": "annual", "ans": True}],
        }
        ch = _StubChannel()
        data, status = _handle_finalize(
            params,
            pipeline_firms=["LITE", "Innolight"],
            expanded_periods=["FY2025"],
            file_inventories={},
            session_dir=tmp_path,
            channel=ch,
        )
        assert data is not None  # not an error
        assert any("Innolight" in m for m in ch.messages)  # warning printed


# =====================================================================
# M5: Phase 2 — _fill_ans_stencil, _persist_phase2, run_phase2
# =====================================================================

def _build_phase2_fixtures():
    """Build work/ans/job stencils with simulated Leng-filled values.

    Uses the canonical 2-firm stencil (22 rows × 3 periods).
    Fills retrieve cells with plausible values for LITE (USD)
    and Innolight (CNY). Compute cells left null — Phase 2 fills them.
    """
    raw_rows = _make_canonical_raw_rows()
    sorted_rows = _topo_sort_rows(raw_rows, ["LITE", "Innolight"])
    periods = ["FY2025", "FY2026", "FY2027"]
    work, ans, jobs = _assign_structure(
        sorted_rows, periods, ["LITE", "Innolight"]
    )

    # Map metric names -> row numbers per firm for convenience
    lite_rows = {
        v["metric"]: int(k)
        for k, v in work["rows"].items()
        if v["firm"] == "LITE"
    }
    inno_rows = {
        v["metric"]: int(k)
        for k, v in work["rows"].items()
        if v["firm"] == "Innolight"
    }

    # Simulate Leng fills on job stencils (retrieve cells only)
    lite_job = next(j for j in jobs if j["firm"] == "LITE")
    inno_job = next(j for j in jobs if j["firm"] == "Innolight")

    # LITE retrieve values (USD)
    lite_fills = {
        "Market Cap": [8_200_000_000, 9_000_000_000, 9_500_000_000],
        "Net Debt": [1_500_000_000, 1_400_000_000, 1_300_000_000],
        "Op Income": [800_000_000, 900_000_000, 1_000_000_000],
        "D&A": [200_000_000, 210_000_000, 220_000_000],
        "Share Price": [52.0, 60.0, 68.0],
        "EPS": [3.42, 4.10, 4.50],
        "Laser Rev": [2_100_000_000, 2_400_000_000, 2_700_000_000],
    }
    for metric, vals in lite_fills.items():
        rn = lite_rows[metric]
        for i, col in enumerate(["A", "B", "C"]):
            lite_job["values"][f"{col}{rn}"] = vals[i]
            lite_job["sources"][f"{col}{rn}"] = f"node_lite_{metric}"

    # Innolight retrieve values (CNY)
    inno_fills = {
        "Market Cap": [55_000_000_000, 60_000_000_000, 65_000_000_000],
        "Net Debt": [5_000_000_000, 4_500_000_000, 4_000_000_000],
        "Op Income": [3_000_000_000, 3_500_000_000, 4_000_000_000],
        "D&A": [500_000_000, 550_000_000, 600_000_000],
        "Share Price": [120.0, 135.0, 150.0],
        "EPS": [8.5, 9.2, 10.0],
        "Laser Rev": [12_000_000_000, 14_000_000_000, 16_000_000_000],
    }
    for metric, vals in inno_fills.items():
        rn = inno_rows[metric]
        for i, col in enumerate(["A", "B", "C"]):
            inno_job["values"][f"{col}{rn}"] = vals[i]
            inno_job["sources"][f"{col}{rn}"] = f"node_inno_{metric}"

    # Add status field (as run_dispatcher would)
    lite_job["status"] = "complete"
    inno_job["status"] = "complete"

    return work, ans, jobs, lite_rows, inno_rows


class TestFillAnsStencil:
    def test_fills_ans_cells(self):
        """Ans stencil populated from work stencil after merge+compute."""
        work, ans, jobs, lite_rows, inno_rows = _build_phase2_fixtures()

        # Merge job values into work stencil
        _merge_jobs_into_work(work, jobs)
        _compute_formula_cells(work)

        result = _fill_ans_stencil(work, ans)

        # Both firms present in filled
        assert "LITE" in result["filled"]
        assert "Innolight" in result["filled"]

        # Ans metrics: EV/EBITDA, P/E, Laser Rev
        # LITE EV/EBITDA should be computed (non-null)
        eveb_rn = lite_rows["EV/EBITDA"]
        assert result["filled"]["LITE"][f"A{eveb_rn}"] is not None

        # LITE Laser Rev should be direct retrieve (non-null)
        lr_rn = lite_rows["Laser Rev"]
        assert result["filled"]["LITE"][f"A{lr_rn}"] == 2_100_000_000

        # Innolight P/E should be computed
        pe_rn = inno_rows["P/E"]
        assert result["filled"]["Innolight"][f"A{pe_rn}"] is not None

    def test_null_propagation_in_ans(self):
        """Unfilled retrieve -> compute stays null -> ans shows null."""
        raw_rows = _make_canonical_raw_rows()
        sorted_rows = _topo_sort_rows(raw_rows, ["LITE", "Innolight"])
        work, ans, jobs = _assign_structure(
            sorted_rows, ["FY2025"], ["LITE", "Innolight"]
        )
        # Don't fill anything — all null
        _merge_jobs_into_work(work, jobs)
        _compute_formula_cells(work)
        result = _fill_ans_stencil(work, ans)

        # All ans cells should be None
        for firm_data in result["filled"].values():
            assert all(v is None for v in firm_data.values())


class TestPersistPhase2:
    def test_writes_files(self, tmp_path):
        work, ans, jobs, _, _ = _build_phase2_fixtures()
        _merge_jobs_into_work(work, jobs)
        _compute_formula_cells(work)
        _fill_ans_stencil(work, ans)

        _persist_phase2(tmp_path, work, ans)

        work_path = tmp_path / "pms2" / "work_stencil.json"
        ans_path = tmp_path / "pms2" / "ans_stencil.json"
        assert work_path.exists()
        assert ans_path.exists()

        import json
        loaded_work = json.loads(work_path.read_text())
        loaded_ans = json.loads(ans_path.read_text())

        # Work stencil has filled values
        assert any(v is not None for v in loaded_work["values"].values())
        # Ans stencil has filled dict
        assert "LITE" in loaded_ans["filled"]

    def test_creates_pms2_subdir(self, tmp_path):
        """pms2/ subdir created if it doesn't exist."""
        work = {"firms": [], "periods": [], "col_letters": [],
                "rows": {}, "values": {}, "sources": {}}
        ans = {"metrics": [], "periods": [], "firms": [],
               "col_letters": [], "row_mapping": {}, "filled": {}}
        _persist_phase2(tmp_path, work, ans)
        assert (tmp_path / "pms2").is_dir()


class TestRunPhase2:
    def test_full_phase2_canonical(self, tmp_path):
        """Full Phase 2 on canonical 2-firm stencil."""
        work, ans, jobs, lite_rows, inno_rows = _build_phase2_fixtures()
        ch = _StubChannel()

        display = run_phase2(
            work_stencil=work,
            ans_stencil=ans,
            completed_jobs=jobs,
            session_dir=tmp_path,
            channel=ch,
        )

        # --- Merge assertions (T0h regression) ---
        # Work stencil values populated from job stencils
        assert work["values"][f"A{lite_rows['Market Cap']}"] == 8_200_000_000
        assert work["values"][f"A{inno_rows['Market Cap']}"] == 55_000_000_000
        # Sources copied
        assert f"A{lite_rows['Market Cap']}" in work["sources"]

        # --- Compute assertions (T0i regression) ---
        # EV = MktCap + NetDebt
        ev_rn = lite_rows["EV"]
        assert work["values"][f"A{ev_rn}"] == 9_700_000_000  # 8.2 + 1.5
        # EBITDA = OpInc + D&A
        ebitda_rn = lite_rows["EBITDA"]
        assert work["values"][f"A{ebitda_rn}"] == 1_000_000_000  # 800 + 200
        # EV/EBITDA = EV/EBITDA
        eveb_rn = lite_rows["EV/EBITDA"]
        assert work["values"][f"A{eveb_rn}"] == 9.7  # 9.7 / 1.0
        # P/E = SharePrice/EPS
        pe_rn = lite_rows["P/E"]
        assert abs(work["values"][f"A{pe_rn}"] - 52.0 / 3.42) < 0.001
        # Formula sources
        assert work["sources"][f"A{eveb_rn}"].startswith("formula:")
        # Null deps -> null (none here, but verify no crash)

        # --- Cross-firm isolation ---
        inno_ev_rn = inno_rows["EV"]
        assert work["values"][f"A{inno_ev_rn}"] == 60_000_000_000  # 55+5

        # --- Ans stencil assertions ---
        assert "LITE" in ans["filled"]
        assert "Innolight" in ans["filled"]
        assert set(ans["metrics"]) == {"EV/EBITDA", "P/E", "Laser Rev"}

        # --- Display stencil assertions ---
        assert len(display) == 2  # one per firm
        lite_disp = next(d for d in display if d["firm"] == "LITE")
        inno_disp = next(d for d in display if d["firm"] == "Innolight")

        # Only ans rows in display
        lite_metrics = {r["metric"] for r in lite_disp["rows"]}
        assert lite_metrics == {"EV/EBITDA", "P/E", "Laser Rev"}

        # Values are absolute (not None for filled cells)
        eveb_row = next(r for r in lite_disp["rows"]
                        if r["metric"] == "EV/EBITDA")
        assert eveb_row["values"][0] == 9.7
        assert eveb_row["unit"] == "float"

        lr_row = next(r for r in lite_disp["rows"]
                      if r["metric"] == "Laser Rev")
        assert lr_row["values"][0] == 2_100_000_000

        # Periods preserved
        assert lite_disp["periods"] == ["FY2025", "FY2026", "FY2027"]

        # --- Persistence assertions ---
        assert (tmp_path / "pms2" / "work_stencil.json").exists()
        assert (tmp_path / "pms2" / "ans_stencil.json").exists()

    def test_partial_fill(self, tmp_path):
        """One dispatcher crashed — only surviving firm merges."""
        work, ans, jobs, lite_rows, inno_rows = _build_phase2_fixtures()

        # Only pass LITE job (simulate Innolight dispatcher crash)
        lite_job = next(j for j in jobs if j["firm"] == "LITE")

        display = run_phase2(
            work_stencil=work,
            ans_stencil=ans,
            completed_jobs=[lite_job],
            session_dir=tmp_path,
        )

        # LITE filled
        assert work["values"][f"A{lite_rows['Market Cap']}"] == 8_200_000_000
        # Innolight still all null
        assert work["values"][f"A{inno_rows['Market Cap']}"] is None
        # Innolight compute also null
        assert work["values"][f"A{inno_rows['EV']}"] is None

        # Display stencils still have both firms (structure intact)
        assert len(display) == 2
        inno_disp = next(d for d in display if d["firm"] == "Innolight")
        assert all(v is None for r in inno_disp["rows"] for v in r["values"])

    def test_empty_jobs(self, tmp_path):
        """All dispatchers crashed — empty completed_jobs."""
        work, ans, jobs, _, _ = _build_phase2_fixtures()

        display = run_phase2(
            work_stencil=work,
            ans_stencil=ans,
            completed_jobs=[],
            session_dir=tmp_path,
        )

        # Everything null
        assert all(v is None for v in work["values"].values())
        assert len(display) == 2
        assert all(v is None for d in display
                   for r in d["rows"] for v in r["values"])

    def test_leng_direct_on_compute_cell(self, tmp_path):
        """Leng found EV/EBITDA=10.2 directly from analyst note.

        Merge puts 10.2 into the compute cell. Formula would give
        9.7. Leng-direct wins — formula does NOT overwrite.
        Divergence warning fires (9.7 vs 10.2 = ~5%).
        """
        work, ans, jobs, lite_rows, inno_rows = _build_phase2_fixtures()
        ch = _StubChannel()

        # Simulate: Leng found EV/EBITDA directly for LITE FY2025
        lite_job = next(j for j in jobs if j["firm"] == "LITE")
        eveb_rn = lite_rows["EV/EBITDA"]
        lite_job["values"][f"A{eveb_rn}"] = 10.2
        lite_job["sources"][f"A{eveb_rn}"] = "node_analyst_note"

        display = run_phase2(
            work_stencil=work,
            ans_stencil=ans,
            completed_jobs=jobs,
            session_dir=tmp_path,
            channel=ch,
        )

        # Leng-direct wins: 10.2, not formula's 9.7
        assert work["values"][f"A{eveb_rn}"] == 10.2
        # Source preserved from Leng, not overwritten by formula
        assert work["sources"][f"A{eveb_rn}"] == "node_analyst_note"

        # Divergence warning fired (9.7 vs 10.2 ≈ 5%)
        divergence_msgs = [m for m in ch.messages if "divergence" in m.lower()]
        assert len(divergence_msgs) >= 1
        assert "EV/EBITDA" in divergence_msgs[0]

        # Display stencil reflects Leng-direct value
        lite_disp = next(d for d in display if d["firm"] == "LITE")
        eveb_row = next(r for r in lite_disp["rows"]
                        if r["metric"] == "EV/EBITDA")
        assert eveb_row["values"][0] == 10.2

    def test_denom_miss_divergence(self, tmp_path):
        """Leng stored 32.8 (denom miss — should be 0.328 for a ratio).

        Formula gives 0.328. Divergence = ~99x. Warning must fire.
        Leng-direct still wins (analyst catches from warning).
        """
        # Minimal 1-firm stencil: GM = GP/Rev
        raw_rows = [
            {"metric": "Revenue", "firm": "X", "type": "retrieve",
             "unit": "USD", "timeframe": "annual"},
            {"metric": "GP", "firm": "X", "type": "retrieve",
             "unit": "USD", "timeframe": "annual"},
            {"metric": "GM", "firm": "X", "type": "compute",
             "formula": "{GP}/{Revenue}", "unit": "float",
             "timeframe": "annual", "ans": True},
        ]
        sorted_rows = _topo_sort_rows(raw_rows, ["X"])
        work, ans, jobs = _assign_structure(sorted_rows, ["FY2025"], ["X"])
        ch = _StubChannel()

        x_rows = {
            v["metric"]: int(k)
            for k, v in work["rows"].items()
        }
        job = jobs[0]
        # Fill retrieves
        job["values"][f"A{x_rows['Revenue']}"] = 1_000_000_000
        job["values"][f"A{x_rows['GP']}"] = 328_000_000
        # Leng found GM "32.8" raw — denom miss (should be 0.328)
        gm_rn = x_rows["GM"]
        job["values"][f"A{gm_rn}"] = 32.8
        job["sources"][f"A{gm_rn}"] = "node_denom_miss"

        display = run_phase2(
            work_stencil=work, ans_stencil=ans,
            completed_jobs=[job], session_dir=tmp_path, channel=ch,
        )

        # Leng-direct wins
        assert work["values"][f"A{gm_rn}"] == 32.8
        # Divergence warning fires (formula=0.328, Leng=32.8 → ~9900%)
        divergence_msgs = [m for m in ch.messages if "divergence" in m.lower()]
        assert len(divergence_msgs) >= 1

    def test_pms2_pipeline_imports_run_phase2(self):
        """Smoke test: pms2.py successfully imports run_phase2."""
        from src.scripts.PMS2.pms2 import run_pms2_pipeline
        # If import fails, test fails. No need to call it.
        assert callable(run_pms2_pipeline)
