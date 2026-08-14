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
    file_scope: list[str] | None = None,
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
    file_scope : list[str] | None
        Optional list of file paths (absolute, or relative to
        data/files_ingested/). When provided, retrieval is hard-scoped to
        chunks from only those files. None = search the whole corpus.

    Returns
    -------
    str
        Markdown-formatted answer with source citations.
    """
    from src.scripts.research.decomposer import decompose_question
    from src.scripts.research.retriever import retrieve_for_sub_questions
    from src.scripts.research.synthesizer import synthesize_answer

    # Step 1: Decompose
    try:
        sub_questions = decompose_question(question, config)
    except Exception as exc:
        channel.print(f"Decomposition failed: {exc}")
        raise

    if not sub_questions:
        return "**Decomposition produced no sub-questions.** Try rephrasing the question."

    # Step 2: Retrieve
    try:
        chunks = retrieve_for_sub_questions(
            sub_questions, config, file_scope=file_scope
        )
    except FileNotFoundError as exc:
        channel.print(f"Retrieval failed: {exc}")
        return (
            f"**Cannot search documents.** {exc}\n\n"
            f"Run `00_Ingest.py` and `01_Chunk.py` to build the document index."
        )
    except Exception as exc:
        channel.print(f"Retrieval failed: {exc}")
        raise

    if file_scope:
        from src.scripts.research.retriever import (
            _get_retriever,
            normalize_file_paths,
        )

        allowed = normalize_file_paths(file_scope, config)
        if not allowed:
            channel.print(
                "Warning: none of the provided file paths "
                "resolve under the ingested-data root."
            )
        elif not (allowed & _get_retriever(config).known_file_paths()):
            channel.print(
                "Warning: none of the provided file paths match "
                "any ingested document."
            )

    if not chunks:
        return (
            "**No relevant chunks found.** The BM25 index did not match any "
            "documents for the decomposed sub-questions. Try a more specific "
            "question, or ingest additional documents."
        )

    # Step 3: Synthesize
    channel.print(f"Synthesizing answer from {len(chunks)} chunks...")
    try:
        answer = synthesize_answer(question, chunks, config)
    except Exception as exc:
        channel.print(f"Synthesis failed: {exc}")
        raise

    channel.print(f"Complete — {len(answer)} chars")

    return answer
