"""synthesizer.py — LLM answer synthesis from retrieved chunks.

Takes the original question and retrieved BM25 chunks, builds a prompt
with chunk context blocks, and calls a frontier reasoning LLM to produce
a cited markdown answer.

Usage:
    from src.scripts.research.synthesizer import synthesize_answer

    answer = synthesize_answer(question, chunks, config)
    # → "## Answer\n\nLITE's competitive position is..."
"""

from __future__ import annotations

from src.config import Config
from src.harness.answer_sheet_contract import format_answer_sheet
from src.harness.sysprompts import load_sysprompt


def _strip_brackets(text: str) -> str:
    """Swap [ ] for ( ) so filename/section metadata can never be mistaken
    for citation-tag delimiters once it's echoed inside [Source: ...]."""
    return text.replace("[", "(").replace("]", ")")


def synthesize_answer(
    question: str,
    chunks: list,  # list[RetrievedChunk] — imported lazily
    config: Config,
) -> str:
    """Synthesize a cited answer from retrieved chunks.

    Parameters
    ----------
    question : str
        The user's original open-ended question.
    chunks : list[RetrievedChunk]
        Retrieved chunks with text and metadata, sorted by BM25 score.
    config : Config
        PSOAS configuration.

    Returns
    -------
    str
        Markdown-formatted answer with source citations.
    """ 
    from src.scripts.llm import get_research_synthesizer_llm

    if not chunks:
        return (
            "**No relevant documents found.** The document collection "
            "does not contain information to answer this question. "
            "Try ingesting more documents or rephrasing the question."
        )

    backend = get_research_synthesizer_llm(config)
    sysprompt = load_sysprompt(
        "research_synthesizer", config.research_synthesizer_profile
    )

    # Build chunk context blocks
    chunk_blocks: list[str] = []
    for i, ch in enumerate(chunks):
        # Build a compact source line. file_name/section can carry a literal
        # '[External]' mail-gateway tag (email-ingested sources) — square
        # brackets there would land inside a [Source: ...] citation and
        # break format_answer_sheet()'s regex (see answer_sheet_contract.py),
        # so swap to parens before the model ever sees or echoes them.
        source_parts = [f"**Source:** {_strip_brackets(ch.file_name)}"]
        if ch.section:
            source_parts.append(f"Section: {_strip_brackets(ch.section)}")
        if ch.fiscal_year:
            source_parts.append(f"FY: {ch.fiscal_year}")
        source_line = " | ".join(source_parts)

        block = (
            f"### Chunk {i + 1}\n"
            f"{source_line}\n\n"
            f"{ch.text}\n"
        )
        chunk_blocks.append(block)

    context = "\n---\n".join(chunk_blocks)

    prompt = (
        f"## Research Question\n\n{question}\n\n"
        f"## Retrieved Source Chunks\n\n{context}\n\n"
        f"Synthesize a comprehensive answer to the research question "
        f"based on the retrieved chunks above. Cite sources using "
        f"[Source: filename — section] format."
    )

    answer_draft = backend.complete(
        prompt=prompt,
        system_prompt=sysprompt,
        label="research-synthesize",
    )
    return format_answer_sheet(question, answer_draft)
