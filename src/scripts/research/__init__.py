"""research — Open-ended question answering via query decomposition.

Pipeline: decompose → retrieve (BM25) → synthesize.

Entry point:
    run_research_pipeline(question, config, session_dir, channel, debug_dir)
    → str (markdown answer with citations)

Called by the orchestrator's run_research tool handler.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.harness.terminal_router import ToolChannel


def run_research_pipeline(
    question: str,
    config: Config,
    session_dir: Path,
    channel: ToolChannel,
    debug_dir: Path | None = None,
) -> str:
    """Full research pipeline: decompose → retrieve → synthesize.

    Parameters
    ----------
    question : str
        The user's open-ended research question.
    config : Config
        PSOAS configuration.
    session_dir : Path
        Session directory for writing output.
    channel : ToolChannel
        Terminal channel for progress output.
    debug_dir : Path | None
        Debug trace directory (optional).

    Returns
    -------
    str
        Markdown-formatted answer with source citations.
    """
    from src.scripts.research.decomposer import decompose_question
    from src.scripts.research.retriever import retrieve_for_sub_questions
    from src.scripts.research.synthesizer import synthesize_answer

    channel.print(f"[RESEARCH] Decomposing: {question[:100]}")

    # Step 1: Decompose
    try:
        sub_questions = decompose_question(question, config)
    except Exception as exc:
        channel.print(f"[RESEARCH] Decomposition failed: {exc}")
        raise

    if not sub_questions:
        return "**Decomposition produced no sub-questions.** Try rephrasing the question."

    channel.print(
        f"[RESEARCH] Decomposed into {len(sub_questions)} sub-questions:"
    )
    for i, sq in enumerate(sub_questions):
        channel.print(f"  {i + 1}. {sq.question}")
        channel.print(f"     Keywords: {', '.join(sq.keywords)}")

    # Step 2: Retrieve
    try:
        chunks = retrieve_for_sub_questions(sub_questions, config)
    except FileNotFoundError as exc:
        channel.print(f"[RESEARCH] Retrieval failed: {exc}")
        return (
            f"**Cannot search documents.** {exc}\n\n"
            f"Run `00_Ingest.py` and `01_Chunk.py` to build the document index."
        )
    except Exception as exc:
        channel.print(f"[RESEARCH] Retrieval failed: {exc}")
        raise

    channel.print(
        f"[RESEARCH] Retrieved {len(chunks)} unique chunks "
        f"(cap: {config.research_max_chunks})"
    )

    if not chunks:
        return (
            "**No relevant chunks found.** The BM25 index did not match any "
            "documents for the decomposed sub-questions. Try a more specific "
            "question, or ingest additional documents."
        )

    # Step 3: Synthesize
    channel.print(f"[RESEARCH] Synthesizing answer from {len(chunks)} chunks...")
    try:
        answer = synthesize_answer(question, chunks, config)
    except Exception as exc:
        channel.print(f"[RESEARCH] Synthesis failed: {exc}")
        raise

    channel.print(f"[RESEARCH] Complete — {len(answer)} chars")

    return answer
