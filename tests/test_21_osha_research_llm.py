"""Spec 21 LLM tests — one per LLM call site in the changed system.

Created:        2026-08-01
Updated:        2026-08-01
Surroundings:   2026-08-01

L0-L3. Four API calls total, one per test. THESE COST MONEY.

One test per LLM call site — decomposer, synthesizer, orchestrator — each
in isolation, so a failure names the node. No end-to-end test: an E2E
failure tells you the pipeline broke, not which stage, and the retrieval
leg is already covered deterministically by U6.

  L0  decomposer    decomposer.py:83   structured_complete
  L1  synthesizer   synthesizer.py:83  complete
  L2  orchestrator  call_with_tools with TOOL_DEFINITIONS -- qualitative
  L3  orchestrator  call_with_tools with TOOL_DEFINITIONS -- quantitative

Preconditions:
  - DEEPSEEK_API_KEY (from .env). L2/L3 additionally need whatever provider
    orchestrator_profile resolves to.
  - tests/test_21_osha_research.py green first.

Run: python -m pytest tests/test_21_osha_research_llm.py -v
"""

import os
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# The key lives in .env, not the shell. llm.py calls load_dotenv at import
# (llm.py:33), so import it BEFORE the skipif gate reads the environment --
# otherwise the whole module skips on a machine that is correctly configured.
import src.scripts.llm  # noqa: E402  (import for its load_dotenv side effect)

pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set (checked after llm.py's load_dotenv)",
)

QUALITATIVE = "What is LITE's competitive outlook?"
QUANTITATIVE = "LITE revenue FY2025"


@pytest.fixture
def config():
    from src.scripts.config import Config
    return Config()


# ── L0: decomposer ────────────────────────────────────────────────────

def test_l0_decompose_question_returns_sub_questions(config):
    """L0: decompose_question returns 3-7 populated SubQuestions.

    minItems/maxItems in DECOMPOSITION_SCHEMA are hints the DeepSeek backend
    does not hard-enforce (F6a), so the count assertion tests the model, not
    the schema.
    """
    from src.scripts.research.decomposer import SubQuestion, decompose_question

    subs = decompose_question(QUALITATIVE, config)

    assert isinstance(subs, list)
    assert all(isinstance(s, SubQuestion) for s in subs)
    assert 3 <= len(subs) <= 7, (
        f"expected 3-7 sub-questions, got {len(subs)} — schema minItems/maxItems "
        f"are hints the backend does not enforce, so this is a model result"
    )

    for s in subs:
        assert s.question.strip(), "empty sub-question text"
        assert isinstance(s.keywords, list)
        assert len(s.keywords) > 0, f"no keywords for {s.question!r}"
        assert all(k.strip() for k in s.keywords), f"blank keyword in {s.keywords}"


# ── L1: synthesizer ───────────────────────────────────────────────────

def test_l1_synthesize_answer_returns_cited_markdown(config):
    """L1: synthesize_answer over 3 hand-built chunks emits a [Source: cite.

    Assert the PREFIX only — F8's sysprompt mandates
    [Source: {file_name} — {section}] but the em-dash is model-discretionary.
    """
    from src.scripts.research.retriever import RetrievedChunk

    chunks = [
        RetrievedChunk(
            node_id="n0",
            text=(
                "Lumentum (LITE) datacom transceiver revenue grew sharply in "
                "fiscal 2026 on hyperscaler demand for 800G and 1.6T optics. "
                "Management cited capacity expansion as the gating factor."
            ),
            score=11.05,
            file_path="LITE/mizuho_lite.md",
            file_name="mizuho_lite.md",
            section="Datacom outlook",
            fiscal_year="FY2026",
        ),
        RetrievedChunk(
            node_id="n1",
            text=(
                "Competition in optical components remains intense. Coherent "
                "and Innolight are both expanding 1.6T capacity, which may "
                "compress Lumentum's pricing power into fiscal 2027."
            ),
            score=10.82,
            file_path="LITE/competitive.md",
            file_name="competitive.md",
            section="Competitive landscape",
            fiscal_year="FY2026",
        ),
        RetrievedChunk(
            node_id="n2",
            text=(
                "Lumentum's telecom segment continues to lag datacom, with "
                "flat year-over-year revenue and ongoing inventory digestion "
                "among carrier customers."
            ),
            score=10.64,
            file_path="LITE/telecom.md",
            file_name="telecom.md",
            section="Telecom segment",
            fiscal_year="FY2026",
        ),
    ]

    answer = synthesize(chunks, config)

    assert isinstance(answer, str)
    assert answer.strip(), "synthesizer returned empty text"
    assert "[Source:" in answer, (
        f"no [Source: citation in the synthesized answer. F8's sysprompt "
        f"mandates the citation format. Got:\n{answer[:500]}"
    )
    # It must not have taken the empty-chunks early-return path.
    assert not answer.startswith("**No relevant "), (
        "synthesizer returned its empty-chunks sentinel despite 3 chunks"
    )


def synthesize(chunks, config):
    from src.scripts.research.synthesizer import synthesize_answer
    return synthesize_answer(QUALITATIVE, chunks, config)


# ── L2 / L3: orchestrator routing ─────────────────────────────────────

def _route(user_text: str):
    """Ask the real orchestrator, with the real sysprompt and TOOL_DEFINITIONS,
    which tool it picks. Returns the list of tool names it selected."""
    from src.harness.system_prompt import TOOL_DEFINITIONS
    from src.harness.sysprompts import load_sysprompt
    from src.scripts.config import Config
    from src.scripts.llm import get_orchestrator_llm

    config = Config()
    backend = get_orchestrator_llm(config)
    system_prompt = load_sysprompt("orchestrator", config.orchestrator_profile)

    response = backend.call_with_tools(
        messages=[{"role": "user", "content": user_text}],
        system_prompt=system_prompt,
        tools=TOOL_DEFINITIONS,
    )
    return [tc.name for tc in response.tool_calls], response


def test_l2_orchestrator_routes_qualitative_to_run_research():
    """L2: a qualitative question routes to run_research.

    THE HIGHEST-VALUE TEST IN THIS SPEC. Everything else is copied code that
    already runs on the osha branch; F9's routing guidance is new, is prose
    in a sysprompt, and is the only thing standing between a qualitative
    question and run_pms2 attempting metric extraction on it (failure mode 8).
    """
    names, response = _route(QUALITATIVE)

    assert names, (
        f"orchestrator called no tool at all for a qualitative question; "
        f"stop_reason={response.stop_reason}, text={response.text[:300]!r}"
    )
    assert "run_research" in names, (
        f"failure mode 8: qualitative question routed to {names} instead of "
        f"run_research. The fault is F9's sysprompt wording, not the module."
    )
    assert "run_pms2" not in names, (
        f"orchestrator also called run_pms2 for a qualitative question: {names}"
    )


def test_l3_orchestrator_still_routes_quantitative_to_run_pms2():
    """L3: a quantitative question still routes to run_pms2.

    The regression half of L2 — F9 adds text that could pull structured
    requests toward run_research as easily as the reverse. Mirrors
    acceptance step 4.
    """
    names, response = _route(QUANTITATIVE)

    assert names, (
        f"orchestrator called no tool at all for a quantitative question; "
        f"stop_reason={response.stop_reason}, text={response.text[:300]!r}"
    )
    assert "run_pms2" in names, (
        f"regression: quantitative question routed to {names} instead of "
        f"run_pms2. F9's routing prose pulled a structured request into the "
        f"research path."
    )
    assert "run_research" not in names, (
        f"orchestrator also called run_research for a quantitative question: {names}"
    )
