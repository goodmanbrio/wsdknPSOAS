"""Input and output contract types for one answer-sheet summary."""

from __future__ import annotations

from .identifier import encode_query_identifier


def build_metadata_header(main_user_query: str) -> str:
    """Build the required two-line metadata header for one summary block."""
    encoded_query = encode_query_identifier(main_user_query)
    return (
        f"[[OSHA_ID:{encoded_query}]]\n"
        "[[OSHA_SUMMARY_TYPE:ANSWER_SHEET]]"
    )
