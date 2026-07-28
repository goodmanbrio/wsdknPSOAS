"""Spec 19 unit tests — period validation, sort key, granularity consistency.

S1 scope: U2-U16.  Pure Python, no LLM, no data files.
S2 scope: U0-U1 (TestAssignStructureGranularity), U17 (TestLoadSyspromptSync).
"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.scripts.PMS2.sekei_loop import (
    _PERIOD_RE,
    _check_period_granularity_consistency,
    _period_sort_key,
)
from src.scripts.PMS2.stencil_topo import _assign_structure, _topo_sort_rows
from src.harness.sysprompts import load_sysprompt


# =====================================================================
# TestPeriodValidation  (U2-U9)
# =====================================================================

class TestPeriodValidation:
    """Tests for _PERIOD_RE regex: ^(Q[1-4]|H[12])?FY[0-9]{4}$"""

    # U2
    def test_valid_annual_periods(self):
        assert _PERIOD_RE.fullmatch("FY2024")
        assert _PERIOD_RE.fullmatch("FY2025")
        assert _PERIOD_RE.fullmatch("FY2030")

    # U3
    def test_valid_quarterly_periods(self):
        assert _PERIOD_RE.fullmatch("Q1FY2025")
        assert _PERIOD_RE.fullmatch("Q4FY2026")
        assert _PERIOD_RE.fullmatch("Q2FY2030")

    # U4
    def test_valid_half_periods(self):
        assert _PERIOD_RE.fullmatch("H1FY2025")
        assert _PERIOD_RE.fullmatch("H2FY2025")
        assert _PERIOD_RE.fullmatch("H1FY2026")

    # U5 — THE ORIGINAL BUG
    def test_reject_3Q_format(self):
        assert _PERIOD_RE.fullmatch("3QFY2025") is None

    # U6
    def test_reject_2digit_year(self):
        assert _PERIOD_RE.fullmatch("FY25") is None
        assert _PERIOD_RE.fullmatch("Q1FY25") is None

    # U7
    def test_reject_Q5(self):
        assert _PERIOD_RE.fullmatch("Q5FY2025") is None
        assert _PERIOD_RE.fullmatch("Q0FY2025") is None

    # U8
    def test_reject_bare_year(self):
        assert _PERIOD_RE.fullmatch("2025") is None

    # U9
    def test_reject_no_FY(self):
        assert _PERIOD_RE.fullmatch("Q32025") is None
        assert _PERIOD_RE.fullmatch("H12025") is None


# =====================================================================
# TestPeriodGranularityConsistency  (U10-U12)
# =====================================================================

class TestPeriodGranularityConsistency:

    # U10
    def test_annual_with_Q_prefix_rejected(self):
        err = _check_period_granularity_consistency(
            ["FY2025", "Q1FY2025"], "annual"
        )
        assert err is not None
        assert "Q1FY2025" in err

    # U11
    def test_quarterly_with_bare_FY_rejected(self):
        err = _check_period_granularity_consistency(
            ["Q1FY2025", "FY2025"], "quarterly"
        )
        assert err is not None
        assert "FY2025" in err

    # U12
    def test_half_with_Q_prefix_rejected(self):
        err = _check_period_granularity_consistency(
            ["H1FY2025", "Q1FY2025"], "half"
        )
        assert err is not None
        assert "Q1FY2025" in err

    def test_annual_all_valid_returns_none(self):
        assert _check_period_granularity_consistency(
            ["FY2024", "FY2025", "FY2026"], "annual"
        ) is None

    def test_quarterly_all_valid_returns_none(self):
        assert _check_period_granularity_consistency(
            ["Q1FY2025", "Q2FY2025", "Q3FY2025"], "quarterly"
        ) is None

    def test_half_all_valid_returns_none(self):
        assert _check_period_granularity_consistency(
            ["H1FY2025", "H2FY2025"], "half"
        ) is None

    def test_unknown_granularity_returns_error(self):
        err = _check_period_granularity_consistency(["FY2025"], "biennial")
        assert err is not None
        assert "biennial" in err


# =====================================================================
# TestPeriodSortKey  (U13-U16)
# =====================================================================

class TestPeriodSortKey:

    # U13
    def test_annual_sort(self):
        unsorted = ["FY2026", "FY2024", "FY2025"]
        result = sorted(unsorted, key=_period_sort_key)
        assert result == ["FY2024", "FY2025", "FY2026"]

    # U14
    def test_quarterly_sort_within_fy(self):
        unsorted = ["Q3FY2025", "Q1FY2025", "Q4FY2025", "Q2FY2025"]
        result = sorted(unsorted, key=_period_sort_key)
        assert result == ["Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025"]

    # U15 — cross-FY quarterly sort
    def test_quarterly_sort_cross_fy(self):
        unsorted = ["Q1FY2026", "Q4FY2025", "Q3FY2025", "Q2FY2026", "Q3FY2026"]
        result = sorted(unsorted, key=_period_sort_key)
        assert result == [
            "Q3FY2025", "Q4FY2025",
            "Q1FY2026", "Q2FY2026", "Q3FY2026",
        ]

    # U16
    def test_half_sort(self):
        unsorted = ["H2FY2025", "H1FY2025", "H1FY2026"]
        result = sorted(unsorted, key=_period_sort_key)
        assert result == ["H1FY2025", "H2FY2025", "H1FY2026"]

    def test_sort_key_values(self):
        assert _period_sort_key("FY2025") == (2025, 0)
        assert _period_sort_key("Q3FY2025") == (2025, 3)
        assert _period_sort_key("H2FY2025") == (2025, 2)
        assert _period_sort_key("H1FY2026") == (2026, 1)

    def test_invalid_period_sorts_last(self):
        result = sorted(["FY2025", "GARBAGE", "FY2024"], key=_period_sort_key)
        assert result[-1] == "GARBAGE"


# =====================================================================
# TestAssignStructureGranularity  (U0-U1)
# =====================================================================

class TestAssignStructureGranularity:

    def _single_row(self, firm, unit, timeframe):
        return [{"metric": "Rev", "firm": firm, "type": "retrieve",
                 "unit": unit, "timeframe": timeframe, "ans": True}]

    # U0
    def test_granularity_stored_in_work_stencil(self):
        rows = self._single_row("LITE", "USD", "quarterly")
        sorted_rows = _topo_sort_rows(rows, ["LITE"])
        work, ans, jobs = _assign_structure(
            sorted_rows, ["Q1FY2025"], ["LITE"], "quarterly"
        )
        assert "granularity" in work
        assert work["granularity"] == "quarterly"

    # U1
    def test_granularity_stored_in_each_job_stencil(self):
        rows = (
            self._single_row("LITE", "USD", "annual")
            + self._single_row("Innolight", "CNY", "annual")
        )
        sorted_rows = _topo_sort_rows(rows, ["LITE", "Innolight"])
        work, ans, jobs = _assign_structure(
            sorted_rows, ["FY2025"], ["LITE", "Innolight"], "annual"
        )
        assert len(jobs) == 2
        assert all("granularity" in j for j in jobs)
        assert all(j["granularity"] == "annual" for j in jobs)


# =====================================================================
# TestLoadSyspromptSync  (U17)
# =====================================================================

class TestLoadSyspromptSync:

    # U17
    def test_sekei_sysprompt_renders_without_error(self):
        """Sekei sysprompt renders with new kwargs (no firms/periods/granularity).

        Verifies:
        - No ValueError from unresolved {{...}} vars
        - No leftover {{ in rendered output
        """
        result = load_sysprompt(
            "pms2_sekei",
            "anthropic_opushighthink",
            query="LITE gross margin FY2025-FY2027 annual",
            top_level_dirs='["LITE/", "0 Optical/"]',
            valid_units='["USD", "CNY", "JPY", "EUR", "GBP", "float"]',
        )
        assert "{{" not in result
        assert len(result) > 100  # non-empty
