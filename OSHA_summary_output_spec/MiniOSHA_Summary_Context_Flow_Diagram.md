# Research Answer Sheet → Persistent Context Flow

```mermaid
flowchart TD
    USER["User question in current conversation turn"]

    subgraph UPSTREAM["Upstream / current-turn research generation — outside this summarization project"]
        DECOMP["Question decomposition\n3–7 subquestions"]
        RETR["BM25 retrieval\none call per subquestion\nup to 5 hits; global cap 20"]
        SYNTH["One research synthesizer call"]
        CURRENT["One synthesized answer sheet\ndisplayed in the current turn\n(ephemeral working output)"]
        USER --> DECOMP --> RETR --> SYNTH --> CURRENT
    end

    subgraph THIS_PROJECT["This project: one answer-sheet summary"]
        INPUT["Input: one answer sheet\nupstream [Source: filename — section] references"]
        SUMMARY["Answer-sheet summarization + local [1] citations\nusing one Ssummary allocation\nno filepath lookup, decomposition, retrieval, or synthesis"]
        OUTPUT["Output: one self-contained summary block\nwith metadata, citations, and bibliography"]
        INPUT --> SUMMARY --> OUTPUT
    end

    CURRENT --> INPUT

    subgraph DOWNSTREAM["Downstream persistence and later context use — outside this project"]
        STORE["Persistent storage / context store"]
        RETRIEVE["Later-turn context retrieval"]
        LATER["Retrieved summary supplied as context\nin a later conversation turn"]
        STORE --> RETRIEVE --> LATER
    end

    OUTPUT --> STORE

    RETRIEVAL["Content retrieval / source acquisition\nhandled by the upstream research pipeline"]
    RETRIEVAL -.-> RETR

    classDef current fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef project fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef external fill:#f5f5f5,stroke:#616161,color:#212121
    classDef boundary fill:#fff3e0,stroke:#ef6c00,color:#e65100

    class USER,CURRENT current
    class INPUT,SUMMARY,OUTPUT project
    class DECOMP,RETR,SYNTH,STORE,RETRIEVE,LATER external
    class RETRIEVAL boundary
```

## Boundary represented

The upstream research pipeline decomposes the main query into 3–7 subquestions,
retrieves and deduplicates source chunks, and uses one synthesizer call to
produce one answer sheet containing `[Source: filename — section]` references.
This project receives that answer sheet, assigns local numeric citation labels,
and returns one compact summary block. It does not receive or resolve
filepaths. The answer sheet remains available in the current conversation turn.
The summary block is the artifact sent to persistent storage for retrieval as
context in later conversation turns.

Source acquisition/content retrieval, persistent storage, and later context
retrieval are shown as connected systems but are not defined by this diagram's
summarization project.
