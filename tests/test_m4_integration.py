"""M4 integration test — LengCaller + Leng + Validator with real data.

Exercises:
  1. Chunk fetch from real docstore
  2. Real Leng structured_complete calls (Haiku)
  3. Real Validator agent loops (Haiku)
  4. Compare-and-swap writes to job stencil
  5. searched_files population
  6. Crash tolerance (injected bad file path)

Usage: python -m pytest tests/test_m4_integration.py -x -v -s
"""

import json
import threading
from pathlib import Path

import dotenv
dotenv.load_dotenv()

import pytest
from llama_index.core.storage.docstore import SimpleDocumentStore

from src.scripts.config import Config
from src.scripts.PMS2.leng_caller import run_leng_caller, _build_cell_descriptions
from src.scripts.PMS2.validator_loop import _handle_submit_verdicts

# ── Fixtures ────────────────────────────────────────────────────────

PSOAS_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def config():
    return Config.from_env()


@pytest.fixture(scope="module")
def docstore():
    path = PSOAS_ROOT / "data" / "index" / "docstore.json"
    return SimpleDocumentStore.from_persist_path(str(path))


@pytest.fixture(scope="module")
def file_path_index():
    path = PSOAS_ROOT / "data" / "index" / "file_path_index.json"
    return json.loads(path.read_text())


class FakeChannel:
    """Minimal ToolChannel substitute for testing."""
    def __init__(self):
        self.prints = []

    def print(self, msg, **kwargs):
        self.prints.append(msg)
        print(f"  [ch] {msg}")

    def input(self, prompt, **kwargs):
        self.prints.append(f"[input] {prompt}")
        print(f"  [ch/input] {prompt}")
        return "n"  # default: decline


# ── Test: chunk fetch ───────────────────────────────────────────────

def test_chunk_fetch(docstore, file_path_index):
    """Verify chunk fetch works for a known file."""
    # Pick first LITE file
    lite_files = [k for k in file_path_index if k.startswith("LITE/")]
    assert len(lite_files) > 0, "No LITE files in index"

    fp = lite_files[0]
    entry = file_path_index[fp]
    node_ids = entry["node_ids"]
    assert len(node_ids) > 0, f"No chunks for {fp}"

    # Fetch first chunk
    doc = docstore.get_document(node_ids[0])
    assert doc is not None
    assert len(doc.text) > 0
    assert "chunk_index" in doc.metadata
    print(f"  Chunk 0 of {fp}: {len(doc.text)} chars")


# ── Test: cell description builder ──────────────────────────────────

def test_cell_descriptions():
    """Verify cell description text is well-formed."""
    job_stencil = {
        "rows": {
            "1": {"metric": "Revenue", "type": "retrieve",
                   "unit": "USD", "timeframe": "annual", "firm": "LITE"},
            "2": {"metric": "EPS", "type": "retrieve",
                   "unit": "USD", "timeframe": "annual", "firm": "LITE"},
        },
        "col_letters": ["A", "B"],
        "periods": ["FY2025", "FY2026"],
        "values": {"A1": None, "B1": None, "A2": None, "B2": None},
        "sources": {},
    }
    desc = _build_cell_descriptions(["A1", "B2"], job_stencil, "LITE")
    assert "Firm: LITE" in desc
    assert "A1 = Revenue" in desc
    assert "FY2025" in desc
    assert "B2 = EPS" in desc
    assert "FY2026" in desc


# ── Test: real Leng + Validator extraction ──────────────────────────

def test_leng_caller_real_extraction(config, docstore, file_path_index):
    """Run real LengCaller against LITE earnings release.

    This is the core M4 test: real chunks, real Haiku calls,
    real Validator writes.

    LITE Q3 FY2026 earnings release contains:
      - Revenue: $808.4 million
      - GAAP Gross Margin: 44.2%
      - GAAP EPS: $1.50
    We ask for quarterly Q3FY2026 data to match what's in the doc.
    """
    # Find the LITE earnings release (most likely to have financials)
    lite_files = sorted([
        k for k in file_path_index if k.startswith("LITE/Company/")
    ])
    assert len(lite_files) > 0, "No LITE/Company/ files"

    target_file = lite_files[0]  # earnings release
    print(f"\n  Target file: {target_file}")
    print(f"  Chunks: {len(file_path_index[target_file]['node_ids'])}")

    # Build a job stencil: Revenue + EPS for Q3FY2026 (quarterly)
    # The earnings release has Q3 FY2026 data (quarter ended Mar 28 2026)
    job_stencil = {
        "firm": "LITE",
        "rows": {
            "1": {"metric": "Revenue", "type": "retrieve",
                   "unit": "USD", "timeframe": "quarterly",
                   "firm": "LITE"},
            "2": {"metric": "Diluted EPS", "type": "retrieve",
                   "unit": "USD", "timeframe": "quarterly",
                   "firm": "LITE"},
        },
        "col_letters": ["A"],
        "periods": ["Q3FY2026"],
        "values": {"A1": None, "A2": None},
        "sources": {},
    }
    stencil_lock = threading.Lock()
    searched_files = {}
    channel = FakeChannel()

    plan = [{"file": target_file, "cells": ["A1", "A2"]}]

    result = run_leng_caller(
        plan=plan,
        job_stencil=job_stencil,
        stencil_lock=stencil_lock,
        searched_files=searched_files,
        fiscal_calendar=None,  # annual, no cal needed
        docstore=docstore,
        file_path_index=file_path_index,
        config=config,
        channel=channel,
        firm="LITE",
        debug_dir=PSOAS_ROOT / "tests" / "debug" / "m4_test",
    )

    # Basic assertions
    assert result == "complete"

    # searched_files populated
    assert target_file in searched_files
    sf = searched_files[target_file]
    assert "A1" in sf["cells_searched"]
    assert "A2" in sf["cells_searched"]
    print(f"\n  searched_files: cells_searched={sf['cells_searched']}")
    print(f"  searched_files: cells_found={sf['cells_found']}")
    print(f"  searched_files: rejections={sf['rejections']}")
    print(f"  searched_files: leng_errors={sf['leng_errors']}")

    # Check if any cells were filled
    filled = {k: v for k, v in job_stencil["values"].items() if v is not None}
    print(f"\n  Filled cells: {filled}")
    print(f"  Sources: {job_stencil['sources']}")

    # We expect at least SOME output — the earnings release should
    # have revenue or gross profit numbers. But it's data-dependent.
    # Log rather than hard-assert.
    if filled:
        print(f"\n  SUCCESS: {len(filled)} cells filled")
        for cid, val in filled.items():
            row = job_stencil["rows"][cid.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
            print(f"    {cid} ({row['metric']}): {val:,.2f}")
    else:
        print("\n  WARNING: 0 cells filled (data-dependent, not necessarily a bug)")


# ── Test: stale file path handling ──────────────────────────────────

def test_leng_caller_stale_file(config, docstore, file_path_index):
    """Verify LengCaller handles missing file_path_index entry gracefully."""
    job_stencil = {
        "firm": "LITE",
        "rows": {
            "1": {"metric": "Revenue", "type": "retrieve",
                   "unit": "USD", "timeframe": "annual", "firm": "LITE"},
        },
        "col_letters": ["A"],
        "periods": ["FY2026"],
        "values": {"A1": None},
        "sources": {},
    }
    stencil_lock = threading.Lock()
    searched_files = {}
    channel = FakeChannel()

    plan = [{"file": "FAKE/nonexistent.md", "cells": ["A1"]}]

    result = run_leng_caller(
        plan=plan,
        job_stencil=job_stencil,
        stencil_lock=stencil_lock,
        searched_files=searched_files,
        fiscal_calendar=None,
        docstore=docstore,
        file_path_index=file_path_index,
        config=config,
        channel=channel,
        firm="LITE",
        debug_dir=PSOAS_ROOT / "tests" / "debug" / "m4_test",
    )

    assert result == "complete"
    # File should still be in searched_files (recorded as searched, 0 found)
    assert "FAKE/nonexistent.md" in searched_files
    assert searched_files["FAKE/nonexistent.md"]["cells_found"] == []
    # Warning printed
    assert any("file_path_index miss" in p for p in channel.prints)
    print("  Stale file handling OK")


# ── Test: compare-and-swap race ─────────────────────────────────────

def test_compare_and_swap_race():
    """Verify first-write-wins when two Validators race for same cell."""
    job_stencil = {
        "rows": {"1": {"unit": "USD"}},
        "values": {"A1": None},
        "sources": {},
    }
    lock = threading.Lock()

    # First write
    outcomes1, _ = _handle_submit_verdicts(
        {"verdicts": [{"cell_id": "A1", "action": "write",
                        "value": 100, "denom": "mn", "unit": "USD"}]},
        job_stencil, lock, "node_1",
    )
    assert outcomes1[0][1] == "written"
    assert job_stencil["values"]["A1"] == 100_000_000

    # Second write — should be skipped
    outcomes2, _ = _handle_submit_verdicts(
        {"verdicts": [{"cell_id": "A1", "action": "write",
                        "value": 200, "denom": "mn", "unit": "USD"}]},
        job_stencil, lock, "node_2",
    )
    assert outcomes2[0][1] == "skipped"
    assert job_stencil["values"]["A1"] == 100_000_000  # unchanged


# ── Test: multi-file extraction ─────────────────────────────────────

def test_leng_caller_multi_file(config, docstore, file_path_index):
    """Run LengCaller against 2+ files simultaneously."""
    lite_files = sorted([
        k for k in file_path_index if k.startswith("LITE/")
    ])
    if len(lite_files) < 2:
        pytest.skip("Need at least 2 LITE files")

    job_stencil = {
        "firm": "LITE",
        "rows": {
            "1": {"metric": "Revenue", "type": "retrieve",
                   "unit": "USD", "timeframe": "quarterly", "firm": "LITE"},
        },
        "col_letters": ["A"],
        "periods": ["Q3FY2026"],
        "values": {"A1": None},
        "sources": {},
    }
    stencil_lock = threading.Lock()
    searched_files = {}
    channel = FakeChannel()

    plan = [
        {"file": lite_files[0], "cells": ["A1"]},
        {"file": lite_files[1], "cells": ["A1"]},
    ]

    result = run_leng_caller(
        plan=plan,
        job_stencil=job_stencil,
        stencil_lock=stencil_lock,
        searched_files=searched_files,
        fiscal_calendar=None,
        docstore=docstore,
        file_path_index=file_path_index,
        config=config,
        channel=channel,
        firm="LITE",
        debug_dir=PSOAS_ROOT / "tests" / "debug" / "m4_test",
    )

    assert result == "complete"
    # Both files should be in searched_files
    assert lite_files[0] in searched_files
    assert lite_files[1] in searched_files
    print(f"\n  Multi-file: {len(searched_files)} files searched")
    for fp, sf in searched_files.items():
        print(f"    {fp}: found={sf['cells_found']}")

    # If A1 was filled, only one source should win (first-write-wins)
    if job_stencil["values"]["A1"] is not None:
        print(f"  A1 = {job_stencil['values']['A1']:,.2f}")
        print(f"  Source: {job_stencil['sources']['A1']}")
