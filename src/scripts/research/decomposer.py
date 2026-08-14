"""decomposer.py — LLM query decomposition for open-ended questions.

Splits a complex research question into 3-7 focused sub-questions, each
with keyword search terms. Uses a cheap deterministic LLM (DeepSeek V4
Flash, temp=0) via structured_complete.

Usage:
    from src.scripts.research.decomposer import decompose_question

    sub_qs = decompose_question(
        "What is the bull case for LITE?",
        config,
    )
    # → [SubQuestion(question="...", keywords=[...]), ...]
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import Config
from src.harness.sysprompts import load_sysprompt


@dataclass
class SubQuestion:
    """A single decomposed sub-question with keyword search terms."""

    question: str
    keywords: list[str]


# JSON schema for structured_complete — the LLM must return this shape.
DECOMPOSITION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The focused sub-question",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-5 keyword search terms for this sub-question",
                    },
                },
                "required": ["question", "keywords"],
            },
            "minItems": 3,
            "maxItems": 7,
        },
    },
    "required": ["sub_questions"],
}


def decompose_question(question: str, config: Config) -> list[SubQuestion]:
    """Decompose an open-ended research question into sub-questions.

    Parameters
    ----------
    question : str
        The user's original open-ended question.
    config : Config
        PSOAS configuration (used to construct the decomposer LLM).

    Returns
    -------
    list[SubQuestion]
        3-7 sub-questions, each with keyword search terms.
    """
    from src.scripts.llm import get_research_decomposer_llm

    backend = get_research_decomposer_llm(config)
    sysprompt = load_sysprompt(
        "research_decomposer", config.research_decomposer_profile
    )

    prompt = (
        f"Decompose this research question into focused sub-questions "
        f"with keyword search terms:\n\n{question}"
    )

    result = backend.structured_complete(
        prompt=prompt,
        schema=DECOMPOSITION_SCHEMA,
        system_prompt=sysprompt,
        label="research-decompose",
    )

    return [
        SubQuestion(
            question=sq["question"],
            keywords=sq["keywords"],
        )
        for sq in result["sub_questions"]
    ]
