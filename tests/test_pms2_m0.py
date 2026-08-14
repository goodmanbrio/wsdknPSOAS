"""M0 integration + unit tests for PMS2 ingest pipeline.

Runs 00_Ingest + 01_Chunk on real data/files_raw/, verifies all outputs.
Both scripts are incremental — re-running on already-processed data is instant.

Requires: data/files_raw/ populated with source files.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INGESTED_DIR = _PROJECT_ROOT / "data" / "files_ingested"
_INDEX_DIR = _PROJECT_ROOT / "data" / "index"
_RAW_DIR = _PROJECT_ROOT / "data" / "files_raw"


# ── Import digit-prefixed modules via importlib ───────────────────────

def _import_script(name: str, filepath: Path):
    spec = importlib.util.spec_from_file_location(name, str(filepath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ingest_mod = _import_script(
    "ingest_00", _PROJECT_ROOT / "src" / "scripts" / "PMS2" / "00_Ingest.py"
)
_chunk_mod = _import_script(
    "chunk_01", _PROJECT_ROOT / "src" / "scripts" / "PMS2" / "01_Chunk.py"
)


# ── Lazy docstore loader (expensive, load once) ──────────────────────

@pytest.fixture(scope="session")
def docstore():
    from llama_index.core.storage.docstore import SimpleDocumentStore
    return SimpleDocumentStore.from_persist_path(
        str(_INDEX_DIR / "docstore.json")
    )


@pytest.fixture(scope="session")
def fpi():
    return json.loads((_INDEX_DIR / "file_path_index.json").read_text())


@pytest.fixture(scope="session")
def manifest():
    return json.loads((_INGESTED_DIR / "manifest.json").read_text())


# =====================================================================
# Integration: run pipeline, verify outputs
# =====================================================================

@pytest.fixture(autouse=True, scope="session")
def run_pipeline():
    """Run both scripts once. Incremental = instant if already done."""
    if not _RAW_DIR.exists() or not any(_RAW_DIR.rglob("*")):
        pytest.skip("data/files_raw/ not populated")
    _ingest_mod.run_ingest()
    _chunk_mod.run_chunk()


# -- 00_Ingest ---------------------------------------------------------

class TestIngestOutputs:
    def test_manifest_exists_nonempty(self, manifest):
        assert len(manifest) > 0

    def test_manifest_values_are_valid_ext(self, manifest):
        for md_path, ext in manifest.items():
            assert md_path.endswith(".md"), f"Key not .md: {md_path}"
            assert ext in ("pdf", "docx"), f"Bad ext '{ext}' for {md_path}"

    def test_convert_hashes_exist(self):
        path = _INGESTED_DIR / "convert_hashes.json"
        assert path.exists()
        hashes = json.loads(path.read_text())
        assert len(hashes) > 0

    def test_md_files_exist_for_all_manifest_entries(self, manifest):
        for md_path in manifest:
            assert (_INGESTED_DIR / md_path).exists(), f"Missing .md: {md_path}"

    def test_rerun_skips_unchanged(self, capsys):
        """Incremental: second run converts nothing."""
        _ingest_mod.run_ingest()
        out = capsys.readouterr().out
        assert "0 converted" in out
        assert "unchanged" in out


# -- 01_Chunk ----------------------------------------------------------

class TestChunkOutputs:
    def test_docstore_exists(self):
        assert (_INDEX_DIR / "docstore.json").exists()
        # Non-empty (file should be >1KB with real data)
        assert (_INDEX_DIR / "docstore.json").stat().st_size > 1000

    def test_fpi_exists(self):
        assert (_INDEX_DIR / "file_path_index.json").exists()

    def test_file_hashes_exist(self):
        assert (_INDEX_DIR / "file_hashes.json").exists()
        hashes = json.loads((_INDEX_DIR / "file_hashes.json").read_text())
        assert len(hashes) > 0

    def test_rerun_skips_unchanged(self, capsys):
        _chunk_mod.run_chunk()
        out = capsys.readouterr().out
        assert "up to date" in out


# -- FPI structure -----------------------------------------------------

class TestFpiStructure:
    def test_covers_all_manifest_files(self, fpi, manifest):
        for md_path in manifest:
            assert md_path in fpi, f"Missing in FPI: {md_path}"

    def test_entries_have_required_fields(self, fpi):
        for fp, entry in fpi.items():
            assert isinstance(entry["node_ids"], list)
            assert len(entry["node_ids"]) > 0, f"Empty node_ids: {fp}"
            assert isinstance(entry["filetype"], str)
            assert entry["filetype"] != ""

    def test_filetypes_match_manifest(self, fpi, manifest):
        for fp, entry in fpi.items():
            if fp in manifest:
                assert entry["filetype"] == manifest[fp], (
                    f"{fp}: FPI={entry['filetype']}, manifest={manifest[fp]}"
                )

    def test_keys_are_relative_paths(self, fpi):
        for fp in fpi:
            assert not fp.startswith("/"), f"Absolute path in FPI: {fp}"


# -- Overlap injection -------------------------------------------------

class TestOverlapInjection:
    def _get_multi_chunk_file(self, fpi):
        """Find a file with 3+ chunks for overlap testing."""
        for fp, entry in fpi.items():
            if len(entry["node_ids"]) >= 3:
                return fp, entry
        pytest.skip("No file with 3+ chunks")

    def test_first_chunk_no_front_context(self, docstore, fpi):
        fp, entry = self._get_multi_chunk_file(fpi)
        node = docstore.get_document(entry["node_ids"][0])
        assert not node.text.startswith("--- context ---")

    def test_first_chunk_has_back_context(self, docstore, fpi):
        fp, entry = self._get_multi_chunk_file(fpi)
        node = docstore.get_document(entry["node_ids"][0])
        assert "--- context ---" in node.text

    def test_middle_chunk_both_sides(self, docstore, fpi):
        fp, entry = self._get_multi_chunk_file(fpi)
        node = docstore.get_document(entry["node_ids"][1])
        assert node.text.count("--- context ---") == 2

    def test_last_chunk_no_back_context(self, docstore, fpi):
        fp, entry = self._get_multi_chunk_file(fpi)
        node = docstore.get_document(entry["node_ids"][-1])
        parts = node.text.split("--- end context ---")
        # After the last end-context marker, no new context marker
        assert "--- context ---" not in parts[-1]

    def test_context_length_max_1000(self, docstore, fpi):
        fp, entry = self._get_multi_chunk_file(fpi)
        node = docstore.get_document(entry["node_ids"][1])
        if node.text.startswith("--- context ---"):
            front = node.text.split("--- end context ---")[0]
            front = front.replace("--- context ---\n", "")
            assert len(front.strip()) <= 1000, (
                f"Front context {len(front.strip())} chars > 1000"
            )


# -- Metadata ----------------------------------------------------------

class TestChunkMetadata:
    def test_required_fields_present(self, docstore, fpi):
        checked = 0
        for fp, entry in fpi.items():
            for nid in entry["node_ids"][:2]:
                node = docstore.get_document(nid)
                m = node.metadata
                for field in ("file_path", "file_name", "chunk_index",
                              "chunk_type", "filetype"):
                    assert field in m, f"Missing {field} on {nid}"
                checked += 1
            if checked >= 6:
                break
        assert checked >= 6

    def test_file_path_is_relative(self, docstore, fpi):
        for fp, entry in fpi.items():
            nid = entry["node_ids"][0]
            node = docstore.get_document(nid)
            assert not node.metadata["file_path"].startswith("/")

    def test_file_name_is_basename(self, docstore, fpi):
        for fp, entry in fpi.items():
            nid = entry["node_ids"][0]
            node = docstore.get_document(nid)
            assert "/" not in node.metadata["file_name"]

    def test_chunk_type_valid(self, docstore, fpi):
        for fp, entry in fpi.items():
            for nid in entry["node_ids"]:
                node = docstore.get_document(nid)
                assert node.metadata["chunk_type"] in ("text", "table")

    def test_no_pms1_legacy_fields(self, docstore, fpi):
        for fp, entry in fpi.items():
            nid = entry["node_ids"][0]
            node = docstore.get_document(nid)
            assert "dir_implied_firm" not in node.metadata
            assert "dir_implied_sector" not in node.metadata

    def test_chunk_index_sequential_per_file(self, docstore, fpi):
        for fp, entry in list(fpi.items())[:5]:
            indices = []
            for nid in entry["node_ids"]:
                node = docstore.get_document(nid)
                indices.append(node.metadata["chunk_index"])
            expected = list(range(len(indices)))
            assert sorted(indices) == expected, (
                f"Non-sequential chunk_index for {fp}: {indices}"
            )


# =====================================================================
# Unit: _build_fpi
# =====================================================================

class TestBuildFpi:
    def test_fresh_build(self):
        from llama_index.core.schema import TextNode
        nodes = [
            TextNode(text="a", metadata={"file_path": "A/x.md",
                                         "filetype": "pdf"}),
            TextNode(text="b", metadata={"file_path": "A/x.md",
                                         "filetype": "pdf"}),
            TextNode(text="c", metadata={"file_path": "B/y.md",
                                         "filetype": "docx"}),
        ]
        fpi = _chunk_mod._build_fpi(nodes, {}, set())
        assert len(fpi) == 2
        assert len(fpi["A/x.md"]["node_ids"]) == 2
        assert fpi["A/x.md"]["filetype"] == "pdf"
        assert len(fpi["B/y.md"]["node_ids"]) == 1

    def test_unchanged_preserved(self):
        old = {"A/x.md": {"node_ids": ["n1", "n2"], "filetype": "pdf"}}
        fpi = _chunk_mod._build_fpi([], old, {"A/x.md"})
        assert fpi["A/x.md"] == old["A/x.md"]

    def test_removed_excluded(self):
        old = {
            "A/x.md": {"node_ids": ["n1"], "filetype": "pdf"},
            "B/y.md": {"node_ids": ["n2"], "filetype": "docx"},
        }
        # B removed: not in unchanged, no new nodes
        fpi = _chunk_mod._build_fpi([], old, {"A/x.md"})
        assert "A/x.md" in fpi
        assert "B/y.md" not in fpi

    def test_mixed(self):
        from llama_index.core.schema import TextNode
        old = {"A/x.md": {"node_ids": ["n1"], "filetype": "pdf"}}
        new_nodes = [
            TextNode(text="c", metadata={"file_path": "B/y.md",
                                         "filetype": "docx"}),
        ]
        fpi = _chunk_mod._build_fpi(new_nodes, old, {"A/x.md"})
        assert len(fpi) == 2
        assert fpi["A/x.md"]["node_ids"] == ["n1"]  # old preserved
        assert len(fpi["B/y.md"]["node_ids"]) == 1   # new added


# =====================================================================
# Unit: _md_rel_path (00_Ingest)
# =====================================================================

class TestMdRelPath:
    def test_pdf(self):
        assert _ingest_mod._md_rel_path("LITE/Company/results.pdf") == \
            "LITE/Company/results.md"

    def test_docx(self):
        assert _ingest_mod._md_rel_path("Innolight/write-up.docx") == \
            "Innolight/write-up.md"

    def test_nested(self):
        assert _ingest_mod._md_rel_path("0 Optical/Reports/r.pdf") == \
            "0 Optical/Reports/r.md"
