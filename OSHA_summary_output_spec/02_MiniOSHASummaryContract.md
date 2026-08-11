# Interim Mini-OSHA Summary Contract

Status: APPROVED schema direction (20260807)

## Confirmed required contents

Each mini-OSHA summary must include:

1. The one assigned, already-decomposed semantic or numerical sub-subquery (`assigned_ssq`)
2. An answer representation for that one query, split into semantic and numerical content when supported (`answer.semantic`, `answer.numeric`)
3. Citation markers linked to a bibliography
4. A unique mini-OSHA identifier (`osha_id`)
5. The parent subquery identifier (`subquery_id`)

## Context boundary

The decomposer has already split the work. Each mini-OSHA receives only one
assigned semantic or numerical query. It must produce a self-contained summary
without assuming access to the main user query,
parent subqueries, sibling mini-OSHAs, or the broader PSOAS objective.

The mini-OSHA must not decompose `assigned_ssq`, answer a sibling query, or
expand the assignment into a broader research objective. Semantic and numeric
sections describe the same one-query answer; they are not separate tasks.

## Sample-derived output shape

The uploaded samples use section headings, individual findings, and financial
or operating metrics. The mini-OSHA output should preserve that information
architecture while separating qualitative and numerical content.

### Semantic answer

`answer.semantic` is a list of structured, atomic bullet findings. Each bullet
must contain a complete piece of information rather than being a paragraph
arbitrarily split across bullets.

Recommended internal shape:

```text
semantic[]:
  topic
  statement
  qualifiers_or_context
  citations[]
```

### Numeric answer

`answer.numeric` is a table. Each row represents one metric or quantified
relationship and should preserve the context visible in the samples.

Required table fields:

```text
category | metric | value | unit | period_or_comparison | qualifiers | citations
```

Examples of values that belong in this table include revenue, operating
profit, margins, growth rates, cost proportions, percentages, valuation
multiples, and quantified scenario effects.

### Citations and bibliography

Citation markers are attached to the relevant semantic bullet or numeric row.
Each mini-OSHA emits hierarchical citation IDs defined in
`03_CitationIDGrammar.md`. These IDs remain the approved identifiers for this
focused design; cross-block citation remapping is out of scope.

The mini-OSHA bibliography remains mandatory at the end of every mini-OSHA
block and maps each ordinary source ID to a filepath. Derived citations record
their source-ID dependencies and formula instead of a filepath.

Approved decisions:

- Citation markers are inline with the semantic bullet or numeric row they support.
- The bibliography is always present at the end of the block.
- If there are no numeric findings, the machine-readable numeric collection is `[]`.
- Derived calculations use a new citation ID with prior source IDs and a formula.

The complete Markdown block shape is maintained in
`04_MiniOSHAOutputBlock.md`; the generation rules are maintained in
`05_MiniOSHAInstruction.md`.

## Sample-derived semantic fixture

Use this fixture when testing whether a model follows the contract:

```text
Evidence image:
OSHA_summary_output_spec/sample images/520890b2-3258-457d-a059-2fafd1e86b95.jpeg

Query type: semantic
assigned_ssq: What is the company's AI-server-related business exposure?
```

The desired semantic breakdown is atomic: one bullet explains the AI-server
exposure and another explains the capacity response. The quantified support is
kept in the numeric table rather than repeated as a paragraph inside the
semantic bullets:

```markdown
## Semantic Answer

- **AI-server exposure:** The company’s high-layer substrate drill business is directly tied to AI-server substrate demand and is a material overseas-sales channel. [[OSHA_CITE:A1a1a]]
- **Capacity response:** The company is adding AI-dedicated drill production capacity to support this business exposure. [[OSHA_CITE:A1a1a]]

## Numeric Answer

| Category | Metric | Value | Unit | Period / comparison | Qualifiers | Citations |
|---|---|---:|---|---|---|---|
| Business exposure | AI-server high-layer substrate drill share of overseas sales | 61 | % | H1 FY2026 | — | [[OSHA_CITE:A1a1a]] |
| Capital expenditure | New AI-dedicated drill production lines | 3.2 | JPY billion | H1 2026 | — | [[OSHA_CITE:A1a1a]] |
```

The complete expected block and the iterative DeepSeek prompt-adjustment
protocol are maintained in `07_CompleteFocusedOSHAOutputSpec.md`.
