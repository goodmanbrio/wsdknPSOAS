"""Provider-neutral prompt construction for one answer-sheet summary."""

from __future__ import annotations


SUMMARY_INSTRUCTION = """\
You summarize one final answer sheet produced by one research synthesizer call.
Do not decompose the main query, call retrieval, call the synthesizer again,
generate a new answer sheet, or create separate mini-OSHA branches.

You receive exactly one synthesized answer sheet, the supplied main-user-query
identifier, and Ssummary. The answer sheet contains upstream references in the
form [Source: filename — section]. Do not resolve paths, retrieve files,
inspect raw chunks, or invent a source reference. Treat the main user query as
context for the summary, not as permission to access broader context.

Stay within Ssummary. Preserve information in this order when shortening:
direct answer or conclusion; highest-value supporting facts; important numeric
facts and context; lower-priority detail.
If the budget becomes tight, omit lower-priority detail rather than beginning
an unfinished final sentence. End the final bullet or paragraph completely.

Return only the draft summary content. Do not emit OSHA_ID,
OSHA_SUMMARY_TYPE, OSHA_STATUS, local numeric citation labels, a Bibliography
section, or a final stored-context block. The Python wrapper creates those
fields deterministically.

For every supported summarized claim, append the exact upstream source
reference text already present in the answer sheet:
[Source: filename — section]
Copy the complete filename and complete section verbatim. Never abbreviate
either part with an ellipsis (`...`) or substitute a shortened source name.
If an upstream claim has no such reference, preserve the claim without a
citation and do not invent one.
Place every citation on the same line as the claim it supports, at the end of
the bullet or paragraph. Never emit citation-only lines.

When comparable source statements disagree, preserve the answer sheet's
selected statement. If a new selection is unavoidable, compare data period
first and publication date second when available. Emit only the selected
result.

Return concise Markdown bullets or paragraphs only. Do not add an
introduction, explanation, trailing commentary, Markdown code fences, metadata,
or headings named OSHA_ID, OSHA_SUMMARY_TYPE, OSHA_STATUS, or Bibliography.
"""


def build_summary_prompt(
    main_user_query: str,
    answer_sheet_markdown: str | None,
    s_summary: int,
) -> str:
    """Build the provider-neutral prompt for one summary-stage call."""
    answer_sheet = (
        answer_sheet_markdown
        if answer_sheet_markdown is not None
        else "[ANSWER SHEET UNAVAILABLE]"
    )
    return (
        f"{SUMMARY_INSTRUCTION}\n\n"
        "BEGIN SUMMARY INPUT\n"
        f"MAIN USER QUERY:\n{main_user_query}\n\n"
        f"SSUMMARY TOKEN BUDGET:\n{s_summary}\n\n"
        "ANSWER SHEET MARKDOWN:\n"
        f"{answer_sheet}\n"
        "END SUMMARY INPUT"
    )
