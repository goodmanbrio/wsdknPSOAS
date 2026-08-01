"""Spec 21 unit tests — run_research port from the osha branch.

Created:        2026-08-01
Updated:        2026-08-01
Surroundings:   2026-08-01

U0-U10. All offline — no API key, no network, no LLM call. The whole file
must be green before any test in test_21_osha_research_llm.py runs.

The port is mostly verbatim-copied code that already runs on the osha
branch, so these do not re-test it. They cover what is genuinely new:
PSOAS's own corpus matching the shape the copied retriever assumes, the
seams where hand-written code calls copied code, the empty-retrieval
interception, and the two edits that leave no runtime trace if skipped.

HARD RULE: no test may write to data/index/. Every test that builds an
index builds under tmp_path. U6 needs the real corpus, so live_corpus_config
copies docstore.json out to tmp_path first.

Run: python -m pytest tests/test_21_osha_research.py -v
"""

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_docstore(tmp_path):
    """Synthetic llama-index docstore — 3 nodes, each >=10 tokens.

    _build() reads via SimpleDocumentStore.from_persist_path (bm25_index.py:137)
    and drops any chunk whose text has <10 whitespace tokens (bm25_index.py:151),
    so fixture text must clear that bar.
    """
    from llama_index.core.schema import TextNode
    from llama_index.core.storage.docstore import SimpleDocumentStore

    nodes = [
        TextNode(
            id_=f"n{i}",
            text=(f"Lumentum LITE optical revenue outlook datacom transceivers "
                  f"grew in fiscal year twenty twenty six segment {i}"),
            metadata={
                "file_name": f"doc{i}.md", "file_path": f"LITE/doc{i}.md",
                "section": f"Section {i}", "chunk_type": "text",
                "chunk_index": str(i), "fiscal_year": "FY2026",
            },
        )
        for i in range(3)
    ]
    ds = SimpleDocumentStore()
    ds.add_documents(nodes)
    path = tmp_path / "docstore.json"
    ds.persist(str(path))
    return path


@pytest.fixture
def live_corpus_config(tmp_path):
    """Config pointed at a throwaway copy of the REAL docstore.

    U6 must retrieve against real documents, but the HARD RULE forbids
    building an index inside data/index/. Copy out, repoint, build there.
    """
    import shutil
    from dataclasses import replace
    from src.scripts.config import Config

    config = Config()
    shutil.copy(config.pms2_index_dir / "docstore.json", tmp_path / "docstore.json")
    return replace(config, pms2_index_dir=tmp_path)


@pytest.fixture(autouse=True)
def reset_research_singleton():
    """retriever.py:38 caches _retriever process-globally. U6 populates it;
    without a reset, test order changes outcomes."""
    import src.scripts.research.retriever as r
    r._retriever = None
    yield
    r._retriever = None


@pytest.fixture(autouse=True)
def enforce_hard_rule():
    """HARD RULE: no test may write to data/index/.

    Snapshots the live index dir before each test and asserts no test
    created, deleted, or modified anything in it.

    Note this asserts the file was not *written by this test* — NOT that
    bm25_index.pkl is absent. It is a legitimate build artifact: the first
    real run_research call writes it (failure mode 9), so after any live use
    of the harness it exists, gitignored, and that is correct. An
    existence-only check would conflate production's artifact with a test
    violation.

    The spec proposes checking `git status` instead; that cannot work here,
    since .gitignore:2 ignores data/ wholesale and the pickle would never
    show up either way. This is the working equivalent.
    """
    from src.scripts.config import Config

    index_dir = Config().pms2_index_dir

    def snapshot():
        return {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in index_dir.iterdir() if p.is_file()
        }

    before = snapshot()
    yield
    after = snapshot()

    assert after == before, (
        f"HARD RULE violated: a test wrote to {index_dir}.\n"
        f"  created:  {sorted(set(after) - set(before))}\n"
        f"  deleted:  {sorted(set(before) - set(after))}\n"
        f"  modified: {sorted(k for k in set(before) & set(after) if before[k] != after[k])}\n"
        f"Build indexes under tmp_path instead. Delete any stray artifact; "
        f"do not gitignore your way out."
    )


# ── The corpus — the copied module was written against a different one ──

class TestCorpus:
    """U0-U1: PSOAS's own docstore matches the shape the retriever assumes."""

    def test_u0_index_dir_exists_with_docstore(self):
        """U0: config.pms2_index_dir exists, is a dir, contains docstore.json.

        Locks the F2b dependency — the one pre-existing config field the
        whole retriever hangs off. Read-only.
        """
        from src.scripts.config import Config

        config = Config()
        index_dir = config.pms2_index_dir

        assert index_dir.exists(), f"pms2_index_dir does not exist: {index_dir}"
        assert index_dir.is_dir(), f"pms2_index_dir is not a directory: {index_dir}"

        docstore = index_dir / "docstore.json"
        assert docstore.exists(), (
            f"docstore.json not found at {docstore}. retriever.py:57 raises "
            f"FileNotFoundError on exactly this condition."
        )

    def test_u1_every_node_has_all_six_metadata_fields(self):
        """U1: every docstore node carries all 6 fields the retriever reads.

        Locks F6c. The failure this catches is silent: reads are
        .get(key, default), so a missing section or file_name produces blank
        citations in every answer without raising anything.

        Read-only — json.load and assert, never persist back.
        """
        from src.scripts.config import Config

        config = Config()
        with open(config.pms2_index_dir / "docstore.json", encoding="utf-8") as f:
            raw = json.load(f)

        nodes = raw["docstore/data"]
        assert len(nodes) > 0, "docstore has no nodes"

        required = {
            "file_name", "file_path", "section",
            "chunk_type", "chunk_index", "fiscal_year",
        }

        missing: dict[str, int] = {}
        for node_id, entry in nodes.items():
            meta = entry["__data__"].get("metadata", {})
            for key in required - set(meta.keys()):
                missing[key] = missing.get(key, 0) + 1

        assert not missing, (
            f"{len(nodes)} nodes; metadata fields missing on some nodes: "
            f"{missing}. bm25_index.py:158-163 reads these via .get(), so "
            f"this degrades citations silently rather than raising."
        )

    def test_u6_real_retrieval_returns_populated_chunks(self, live_corpus_config):
        """U6: real retrieval over the real corpus, built under tmp_path.

        Hardcoded sub-questions, so no LLM. The only test proving the
        corpus can actually answer a question.
        """
        from src.scripts.config import Config
        from src.scripts.research.decomposer import SubQuestion
        from src.scripts.research.retriever import (
            RetrievedChunk,
            retrieve_for_sub_questions,
        )

        sub_questions = [
            SubQuestion(
                question="LITE optical revenue outlook",
                keywords=["lumentum", "lite", "optical", "revenue"],
            )
        ]

        chunks = retrieve_for_sub_questions(sub_questions, live_corpus_config)

        assert isinstance(chunks, list)
        assert len(chunks) > 0, (
            "real corpus returned zero chunks for an obviously in-corpus "
            "query — retrieval cannot answer any question"
        )
        assert all(isinstance(c, RetrievedChunk) for c in chunks)
        assert len(chunks) <= live_corpus_config.research_max_chunks

        for c in chunks:
            assert c.file_name, f"empty file_name on {c.node_id} — blank citation"
            assert c.section, f"empty section on {c.node_id} — blank citation"
            assert c.text.strip(), f"empty text on {c.node_id}"
            assert c.score > 0.0

        # The build landed under tmp_path. That it did NOT touch the live
        # data/index/ is enforced for every test by enforce_hard_rule.
        assert (live_corpus_config.pms2_index_dir / "bm25_index.pkl").exists()
        assert live_corpus_config.pms2_index_dir != Config().pms2_index_dir


# ── Index cache invalidation — where this class of code rots ──────────

class TestIndexCache:
    """U2-U3: staleness detection and corruption recovery."""

    def test_u2_docstore_change_invalidates_cache(self, tmp_path, tmp_docstore):
        """U2: rewriting the docstore changes its SHA-256, so _load() fails.

        Without this, an ingest that adds documents leaves research
        silently answering from the old corpus. Exercises _build() and the
        pickle persist as its precondition.
        """
        from llama_index.core.schema import TextNode
        from llama_index.core.storage.docstore import SimpleDocumentStore
        from src.scripts.research.bm25_index import BM25Retriever

        # Build over the 3-node docstore, under tmp_path.
        r1 = BM25Retriever(tmp_path, tmp_docstore)
        r1.ensure_ready()
        assert len(r1._node_ids) == 3

        pkl = tmp_path / "bm25_index.pkl"
        assert pkl.exists(), "_build() did not persist the pickle"

        # A fresh retriever over the UNCHANGED docstore loads from cache.
        r2 = BM25Retriever(tmp_path, tmp_docstore)
        assert r2._load() is True, "unchanged docstore should load from cache"

        # Rewrite the docstore with a 4th node — SHA-256 changes.
        ds = SimpleDocumentStore.from_persist_path(str(tmp_docstore))
        ds.add_documents([
            TextNode(
                id_="n3",
                text=("Coherent COHR datacom transceiver revenue outlook grew "
                      "in fiscal year twenty twenty six segment three"),
                metadata={
                    "file_name": "doc3.md", "file_path": "COHR/doc3.md",
                    "section": "Section 3", "chunk_type": "text",
                    "chunk_index": "3", "fiscal_year": "FY2026",
                },
            )
        ])
        ds.persist(str(tmp_docstore))

        r3 = BM25Retriever(tmp_path, tmp_docstore)
        assert r3._load() is False, (
            "docstore changed but _load() accepted the stale pickle — "
            "research would answer from the old corpus (bm25_index.py:209)"
        )

        # And a rebuild picks up the new node.
        r3.ensure_ready()
        assert len(r3._node_ids) == 4

    def test_u3_corrupt_pickle_triggers_rebuild(self, tmp_path, tmp_docstore):
        """U3: a truncated pickle makes _load() return False, not crash.

        Locks failure mode 6 — bm25_index.py:228 catches
        (KeyError, EOFError, pickle.UnpicklingError, ImportError).
        """
        from src.scripts.research.bm25_index import BM25Retriever

        r1 = BM25Retriever(tmp_path, tmp_docstore)
        r1.ensure_ready()

        pkl = tmp_path / "bm25_index.pkl"
        assert pkl.exists()

        # Truncate to 10 bytes.
        with open(pkl, "wb") as f:
            f.write(b"0123456789")
        assert pkl.stat().st_size == 10

        r2 = BM25Retriever(tmp_path, tmp_docstore)
        assert r2._load() is False, "corrupt pickle should fail to load, not raise"

        # Self-healing: ensure_ready rebuilds rather than propagating.
        r2.ensure_ready()
        assert len(r2._node_ids) == 3
        assert pkl.stat().st_size > 10, "rebuild did not overwrite the corrupt pickle"


# ── Seams — hand-written code calling copied code ─────────────────────

class TestSeams:
    """U4-U5: the contracts between hand-written and copied code."""

    def test_u4_pipeline_signature(self):
        """U4: run_research_pipeline's signature is what _exec_research calls.

        Locks the F6a contract that F5 invokes by keyword — the only such
        call site.
        """
        import inspect
        from src.scripts.research import run_research_pipeline

        params = inspect.signature(run_research_pipeline).parameters
        assert list(params.keys()) == [
            "question", "config", "session_dir", "channel", "debug_dir",
        ], f"signature drifted: {list(params.keys())}"

        assert params["debug_dir"].default is None
        for name in ("question", "config", "session_dir", "channel"):
            assert params[name].default is inspect.Parameter.empty, (
                f"{name} gained a default — _exec_research passes it positionally "
                f"by keyword and would not notice"
            )

    def test_u5_dispatch_matches_tool_definitions(self):
        """U5: every advertised tool has a handler and vice versa.

        Catches F4 landing without F5 or vice versa, which makes the
        orchestrator call a tool that resolves to "unknown tool" — a
        confusing failure rather than an obvious one. The commented-out
        run_pms1 entries in both files must stay commented for this to hold.
        """
        from src.harness.execute_tool import _dispatch
        from src.harness.system_prompt import TOOL_DEFINITIONS

        dispatch_names = set(_dispatch.keys())
        tool_names = {d["name"] for d in TOOL_DEFINITIONS}

        assert dispatch_names == tool_names, (
            f"advertised but unhandled: {tool_names - dispatch_names}; "
            f"handled but unadvertised: {dispatch_names - tool_names}"
        )
        assert "run_research" in dispatch_names


# ── Empty-retrieval interception (F5) — the only behavioural divergence ──

class TestEmptyInterception:
    """U9-U10: the hand-written divergence from the osha branch."""

    def test_u9_empty_retrieval_returns_bummer_no_handle_no_file(
        self, tmp_path, monkeypatch
    ):
        """U9: an empty-retrieval sentinel becomes BUMMER, stores nothing.

        Locks failure mode 10.
        """
        import src.harness.execute_tool as et
        import src.scripts.research as research
        from src.harness.opaque_registry import registry

        sentinel = (
            "**No relevant chunks found.** The BM25 index did not match any "
            "documents for the decomposed sub-questions. Try a more specific "
            "question, or ingest additional documents."
        )

        def fake_pipeline(question, config, session_dir, channel, debug_dir=None):
            return sentinel

        monkeypatch.setattr(research, "run_research_pipeline", fake_pipeline)
        monkeypatch.setattr(et, "_session_dir", tmp_path)
        monkeypatch.setattr(et, "_config", None)
        monkeypatch.setattr(et, "_debug_dir", None)

        before = registry.list_vars()

        result = et._exec_research({"question": "x"})

        assert result.startswith("BUMMER retrieval empty"), (
            f"empty retrieval was not intercepted; got: {result[:120]!r}"
        )
        assert "Research complete" not in result
        assert sentinel in result, "the original sentinel should still be shown"

        assert not list(tmp_path.glob("research_*.md")), (
            "a file was written for an empty retrieval"
        )
        assert registry.list_vars() == before, (
            "a handle was stored for an empty retrieval"
        )

    def test_u10_module_literals_still_match_the_prefix(self):
        """U10: the copied module's sentinels still start with the constant.

        The guard on the fragile seam: a re-copy that reworded either
        sentinel would silently un-fix failure mode 10, and nothing else
        would notice. If this fails, fix the constant, not the test.
        """
        from src.harness.execute_tool import _RESEARCH_EMPTY_PREFIX

        research_dir = _project_root / "src" / "scripts" / "research"

        for name in ("__init__.py", "synthesizer.py"):
            source = (research_dir / name).read_text(encoding="utf-8")
            assert _RESEARCH_EMPTY_PREFIX in source, (
                f"{name} no longer contains {_RESEARCH_EMPTY_PREFIX!r}. The "
                f"empty-retrieval interception in _exec_research is now dead "
                f"code and empty results will be presented as answers."
            )


# ── Edits that leave no runtime trace ─────────────────────────────────

class TestSilentEdits:
    """U7-U8: the two edits nothing at runtime would miss."""

    def test_u7_requirements_declares_rank_bm25(self):
        """U7: requirements.txt has a rank-bm25 line (locks F1).

        rank_bm25 is already installed in this env and nothing at runtime
        reads requirements.txt, so the demo passes whether or not F1 was
        applied. The breakage lands on a fresh clone, weeks later.
        """
        text = (_project_root / "requirements.txt").read_text(encoding="utf-8")
        lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert any("rank-bm25" in ln or "rank_bm25" in ln for ln in lines), (
            "requirements.txt does not declare rank-bm25; bm25_index.py:126 "
            "imports it and a fresh clone would ImportError"
        )

    def test_u8_orchestrator_sysprompt_has_routing_guidance(self):
        """U8: the orchestrator sysprompt mentions run_research and routing.

        Locks F9. Otherwise only L2/L3 cover it, and they are
        nondeterministic — a missing sysprompt edit plus a lucky model pick
        is a green test over a latent bug.
        """
        path = (
            _project_root / "sysprompts" / "orchestrator"
            / "deepseek_v4pro_orchestrator.md"
        )
        text = path.read_text(encoding="utf-8")

        assert "run_research" in text, "sysprompt never mentions run_research"
        assert "When to use run_research" in text, (
            "sysprompt has no run_research vs run_pms2 routing section — "
            "failure mode 8 is unmitigated"
        )

        # The osha run_pms2 signature must not have leaked in with it.
        assert "## PMS2 extraction parameters" not in text, (
            "this looks like the osha branch's orchestrator sysprompt, which "
            "describes run_pms2 with firms/periods/granularity params that "
            "PSOAS's run_pms2 does not accept"
        )
