## Frontmatter
- Write date: 20260811
- Update date: 20260811
- Codebase last changed date: 20260810 (per `23_OSHAg.md`, unchanged since)
- Implemented: N — imagined design only, nothing built
- Gate: 2 (problem space, scoped to `failSystematicLog.md` #5 only) plus a Gate 1 manual graph of the proposed replacement. No outcome imagination, detailed solution space, file-by-file change, failure modes, or tests yet — out of scope this session.

## Problem space

- **Global chunk cap loses correct chunks under BM25 score competition across unrelated subqueries.** `retrieve_for_sub_questions()` (`retriever.py:68`) pools every subquery's top-`research_bm25_top_k=5` hits (`config.py:90`) into one list, dedups by node_id, sorts by score, and cuts to `research_max_chunks=20` (`config.py:91`) — nothing reserves a slot per subquery, so one subquery's single correct chunk can lose the cut to a different subquery's higher-scoring but less relevant hits. No step anywhere in `run_research_pipeline()` (`research/__init__.py:20`) checks whether a retrieved chunk actually answers its subquery before it competes for a cap slot — selection is pure BM25 score. Evidence: `failSystematicLog.md` #5, confirmed against undamaged source data twice (failLog #8, #10) and escalating to whole-document exclusion three more times (failLog #21, #23, #24).

## Solution space (idea only)

- Idea: replace the pooled-retrieve-and-cap step with one verification agent per subquery. Each agent retrieves, judges whether what it got actually answers its subquery, and only escalates to more retrieval if not — so subqueries stop competing against each other for a shared cap, and nothing is forwarded without a relevance check.
- Type: breaking. Changes `retriever.py`'s contract (chunks out → cited natural-language summaries out) and `synthesizer.py`'s input contract (reads N summaries, not raw chunks).

## Gate 1 — Manual Graph: Imagined Replacement (nothing below is built yet)

Replaces `retrieve_for_sub_questions()` (`retriever.py:68`) and its call site in
`run_research_pipeline()` (`research/__init__.py`, between the decomposer and
synthesizer stages). `decompose_question()` (`decomposer.py:62`) and
`synthesize_answer()` (`synthesizer.py:20`) are cited by file:line because they're
reused as-is (synthesizer's prompt content still needs updating for the new input
shape — see gap note below); every other node here is new, not built.
Judgment throughout is free-text — no forced schema, no structured yes/no. The
give-up path is also free text: an agent that exhausts its budget without an
answer writes a plain sentence like "no chunks retrieved," not a structured
refusal object.

Legend: rectangle = process, oval = start/end, red diamond = decision, blue
parallelogram = hardcode/config resource, orange parallelogram = tool (a
capability the agent calls) — both are resources and both only ever point
into a process/decision, never the reverse. Dashed red = open/undecided.

```mermaid
flowchart TD
    classDef default text-align:left
    classDef decision fill:#d32f2f,stroke:#7f1d1d,color:#fff,text-align:left
    classDef resource fill:#1565c0,stroke:#0d3f73,color:#fff,text-align:left
    classDef tool fill:#fdba74,stroke:#c2410c,color:#111,text-align:left
    classDef gap stroke:#e74c3c,stroke-dasharray:4 3,color:#111,text-align:left
    classDef resourcegap fill:#1565c0,stroke:#e74c3c,stroke-width:2px,stroke-dasharray:4 3,color:#fff,text-align:left

    START(["question"]) --> DQ

    DQ["decompose_question() (process)<br/>REUSED — decomposer.py:62"]
    RES_SQBOUNDS[/"3-7 subquestions<br/>(resource, existing —<br/>DECOMPOSITION_SCHEMA hint,<br/>unenforced, decomposer.py:34)"/] --> DQ
    DQ --> SQ[/"list of subquestions<br/>(data) — unchanged shape"/]

    SQ --> FANOUT["spawn one subquery agent<br/>per subquestion (process)<br/>NEW — replaces retrieve_for_sub_questions()<br/>retriever.py:68"]

    FANOUT --> AGENT["subquery agent (process)<br/>NEW — single agent, one continuous<br/>loop, revisited below at each tool<br/>call: retrieves, judges, escalates,<br/>writes its own summary. Gets<br/>subquestion + original question."]

    TOOL_RETRIEVE[/"BM25 retrieve top-7 (tool)<br/>NEW call shape — index itself<br/>REUSED, bm25_index.py (external)"/] --> AGENT
    RES_TOPK7[/"initial top_k = 7<br/>(resource, NEW hardcode)"/] --> AGENT
    AGENT --> CHUNKS1[/"7 chunks + frontmatter<br/>(file, section) (data)"/]

    CHUNKS1 --> D_ANS1{"answerable from these 7?<br/>(decision — free-text<br/>agent judgment)<br/>NEW"}
    D_ANS1 -->|"yes"| SUM1["write cited summary (process)<br/>NEW"]
    D_ANS1 -->|"no, escalate"| AGENT

    TOOL_SHOWFM[/"show frontmatter-only of<br/>runner-up chunks, no body<br/>text (tool)<br/>NEW"/] --> AGENT
    RES_RUNNERUP10[/"runner-up count = 10<br/>(resource, NEW hardcode, arbitrary)"/] --> AGENT
    AGENT --> FRONTMATTERS[/"10 runner-up frontmatters,<br/>no body text (data)"/]

    FRONTMATTERS --> D_WORTH{"any runner-up worth<br/>loading full text?<br/>(decision — free-text agent<br/>judgment, 'none' is valid)<br/>NEW"}
    D_WORTH -->|"no"| GIVEUP1["write natural-language<br/>give-up summary, e.g.<br/>'no chunks retrieved' (process)<br/>NEW"]
    D_WORTH -->|"yes, escalate"| AGENT

    TOOL_LOADFULL[/"load full text of selected<br/>runner-ups (tool, capped)<br/>NEW"/] --> AGENT
    RES_LOADCAP[/"load-full-text tool cap = 1<br/>use per subquery agent<br/>(resource, NEW hardcode)"/] --> AGENT
    AGENT --> CHUNKS2[/"selected runner-ups,<br/>full text (data)"/]

    CHUNKS2 --> D_ANS2{"answerable now?<br/>(decision — free-text<br/>agent judgment)<br/>NEW"}
    D_ANS2 -->|"yes"| SUM2["write cited summary (process)<br/>NEW"]
    D_ANS2 -->|"no, cap already spent"| GIVEUP2["write natural-language<br/>give-up summary (process)<br/>NEW"]

    SUM1 --> SUMMARIES
    SUM2 --> SUMMARIES
    GIVEUP1 --> SUMMARIES
    GIVEUP2 --> SUMMARIES
    SUMMARIES[/"N natural-language summaries,<br/>each cited or 'no chunks<br/>retrieved' (data) — no global<br/>cap, no cross-subquery pooling"/]

    SUMMARIES --> SA["synthesize_answer() (process)<br/>REUSED call site — synthesizer.py:20<br/>— but reads N summaries now,<br/>not raw chunks"]
    SYSGAP["synthesizer sysprompt —<br/>still assumes chunk input,<br/>content for summary input<br/>undesigned this session<br/>(resource) UNDECIDED"] -.-> SA
    class SYSGAP resourcegap
    SA --> ANSWER(["final answer"])

    JUDGEGAP["subquery agent's judgment<br/>sysprompt — doesn't exist yet<br/>(resource) UNDECIDED"] -.-> D_ANS1
    JUDGEGAP -.-> D_WORTH
    JUDGEGAP -.-> D_ANS2
    class JUDGEGAP resourcegap

    class D_ANS1,D_ANS2,D_WORTH decision
    class RES_SQBOUNDS,RES_TOPK7,RES_RUNNERUP10,RES_LOADCAP resource
    class TOOL_RETRIEVE,TOOL_SHOWFM,TOOL_LOADFULL tool
```

`AGENT` is drawn once but re-entered three times — each re-entry is the same
agent, in the same loop, making its next tool call; the repeated node is the
loop, not three separate processes.

Open items this diagram makes explicit (not resolved yet):
- `SYSGAP` — synthesizer's sysprompt still assumes it's reading raw chunks; the input shape changed to N cited summaries, prompt content not rewritten this session.
- `JUDGEGAP` — the subquery agent's three judgment points have no sysprompt yet; judgment is free-text by decision, but the prompt driving that judgment doesn't exist.
- No `D_SQEMPTY` short-circuit is drawn: unlike the current pipeline (`research/__init__.py:60-61`), every subquery agent always returns a summary — even a give-up is text, not an empty result — so there's nothing left to short-circuit on before the synthesizer.
