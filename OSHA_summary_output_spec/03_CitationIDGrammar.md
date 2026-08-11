# Proposed Citation and Query-ID Grammar

Status: APPROVED citation, bibliography, metadata, and derived-citation grammar 20260807

## Identifier hierarchy

Identifiers encode the query tree and the ownership of each source.

```text
Main queries:     A1, A2, A3, ...
Subqueries:       A1a, A1b, A1c, ...
Sub-subqueries:   A1a1, A1a2, A1a3, ...
Mini-OSHA ID:     same as its sub-subquery ID
Sources:          A1a1a, A1a1b, A1a1c, ...
```

For example:

```text
A1       = first main query/turn
A2       = second main query/turn
A1a      = first subquery of A1
A1a1     = first sub-subquery of A1a; also the mini-OSHA ID
A1a1a    = first source cited by mini-OSHA A1a1
A1a1b    = second source cited by mini-OSHA A1a1
```

## Contract mapping

```text
osha_id    = sub-subquery ID, e.g. A1a1
subquery_id = immediate parent subquery ID, e.g. A1a
source_id   = mini-OSHA ID plus a source suffix, e.g. A1a1a
```

The decomposer creates the sub-subquery before mini-OSHA execution. Each
mini-OSHA receives exactly one semantic or numerical query, so its `osha_id`
identifies one atomic assignment rather than a bundle of sibling questions.
The mini-OSHA must not create a second query ID while answering that
assignment.

Every inline citation marker must contain or resolve to the complete source
ID. Every bibliography entry must be keyed by that same source ID.

## Approved inline marker

```text
[[OSHA_CITE:<source_id>]]
```

Examples:

```text
[[OSHA_CITE:A1a1a]]
[[OSHA_CITE:A1a1b]]
```

The marker is placed directly after the semantic bullet or numeric table row
that it supports.

## Bibliography field

Each bibliography entry requires only:

```text
source_id → filepath
```

Source suffixes reset independently inside each mini-OSHA block. For example:

```text
A1a1 → A1a1a, A1a1b, ...
A1b1 → A1b1a, A1b1b, ...
```

## Approved bibliography entry

```text
[[OSHA_BIB:<source_id>|<filepath>]]
```

Example:

```text
[[OSHA_BIB:A1a1a|/path/to/source.md]]
```

The bibliography is mandatory and appears at the end of every mini-OSHA
block. It contains one entry for every source ID used by that block.

The reserved separator `|` inside a filepath must be percent-encoded as
`%7C`. A parser splits on the outer separator, then decodes `%7C` in the
filepath.

## Approved derived-citation entry

Derived numbers use the same ID namespace as ordinary citations but do not
point to a filepath. They record the prior citation IDs used as inputs:

```text
[[OSHA_DERIVED:<derived_id>|FROM:<source_id_1>,<source_id_2>|FORMULA:<expression>]]
```

Example:

```text
[[OSHA_DERIVED:A1a1c|FROM:A1a1a,A1a1b|FORMULA:operating_profit/net_sales]]
```

## Approved mini-OSHA metadata markers

Every block begins with:

```text
[[OSHA_ID:<osha_id>]]
[[SUBQUERY_ID:<subquery_id>]]
[[ASSIGNED_SSQ:<assigned_ssq>]]
```

Sibling and source suffixes continue alphabetically after `z`: `aa`, `ab`,
and so on.
