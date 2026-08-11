# Mini-OSHA Output Block — Approved Skeleton

Status: APPROVED STRUCTURE 20260807

The decomposer has already split the work. Each mini-OSHA returns one
self-contained block for `var_ans` and answers exactly one assigned semantic or
numerical query.

```markdown
[[OSHA_ID:<osha_id>]]
[[SUBQUERY_ID:<subquery_id>]]
[[ASSIGNED_SSQ:<assigned_ssq>]]

## Semantic Answer

- **<topic>:** <one complete atomic semantic finding, with relevant context> [[OSHA_CITE:<source_id>]]

## Numeric Answer

| Category | Metric | Value | Unit | Period / comparison | Qualifiers | Citations |
|---|---|---:|---|---|---|---|
| <category> | <metric> | <value> | <unit> | <period/comparison> | <qualifiers> | [[OSHA_CITE:<source_id>]] |

## Bibliography

[[OSHA_BIB:<source_id>|<filepath>]]
```

## Structural rules

- The metadata markers appear at the beginning of the block.
- Each semantic bullet is one complete, atomic finding; do not split a paragraph mechanically into bullets.
- Numeric facts and quantified relationships belong in the numeric table.
- Citation markers appear beside the semantic bullet or numeric row they support.
- The bibliography is always present and appears last.
- A literal `|` in a filepath is encoded as `%7C` so the bibliography separator remains unambiguous.
- If there are no numeric findings, `answer.numeric` is `[]`; the output contract still preserves the Numeric Answer section.
- A semantic query may include quantified supporting facts in the numeric table; this does not create a second query.
- A numerical query may include a concise semantic interpretation when supported by the evidence; it still has only one research objective.

## Approved content rules

- Mini-OSHAs may calculate derived numbers.
- Every derived number receives a new citation marker.
- The derived citation must identify the earlier citation IDs used as inputs.
- Derived citations use `[[OSHA_DERIVED:<derived_id>|FROM:<source_ids>|FORMULA:<expression>]]` rather than a filepath bibliography entry.
- A finding containing both qualitative and numeric information is split: the qualitative claim goes in `Semantic Answer`, and the numeric value goes in `Numeric Answer`.
- When approaching the `Smini` limit, prioritize the direct answer and highest-value findings; omit lower-priority details rather than exceeding the limit.

If the assigned query has no supported evidence, preserve the block structure
and emit:

```text
[[OSHA_STATUS:INSUFFICIENT_EVIDENCE]]
[[OSHA_BIB:INSUFFICIENT_EVIDENCE|NO_FILEPATH]]
```

## Approved derived-citation behavior

A derived number uses a new citation ID and records the prior source IDs and
formula through the `OSHA_DERIVED` grammar. It does not require a filepath.
