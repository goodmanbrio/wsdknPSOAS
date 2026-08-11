# Interim Summary-Budget Decision

## Decision

All mini-OSHAs in a single user turn receive an equal summary limit.

Each conversation turn contains exactly one main user query. The query
hierarchy for that turn is:

```text
main user query → subqueries → sub-subqueries → mini-OSHAs
```

The mini-OSHA count is the total number of sub-subqueries created after the
decomposer splits the main user query into individual semantic or numerical
queries. Each mini-OSHA handles exactly one such query and does not decompose
it further.

For this interim design, `var_ans` refers both to the master LLM stage and to
the database answer/source record produced by that stage.

The symbolic equation remains unresolved until the maximum mini-OSHA count and research-token constant are supplied.

## Equation

Let:

- `Q` = the main user question, currently estimated at approximately 26 tokens for a 20-word question
- `m` = number of first-level subqueries
- `k_i` = number of sub-subqueries under first-level subquery `i`
- `n` = total number of sub-subqueries/mini-OSHAs, `n = Σ k_i`
- `C` = constant total research-token budget per turn
- `Smini` = summary-token limit for each mini-OSHA/sub-subquery

With 8 turns under a 500,000-token total budget:

```text
Tturn < 500,000 / 8
Tturn < 62,500 tokens

C + (n × Smini) + Q < 62,500

Smini < (62,500 − C − Q) / n
Smini < (62,474 − C) / n    [using Q ≈ 26 tokens]
```

For a uniform hierarchy where there are `m` first-level subqueries and `k`
sub-subqueries under each one:

```text
n = m × k
Smini < (62,474 − C) / (m × k)
```

Thus the total summary allowance is divided first across the first-level
subqueries and then across the sub-subqueries/mini-OSHAs under them. The
formula assumes one mini-OSHA handles exactly one sub-subquery.

The combined mini-OSHA summaries are sent to `var_ans`. `var_ans` then stores
the answer and source information in the database and sends the result to the
orchestrator.

The assignment remains one query even when its answer has both semantic and
numeric representation. For example, a semantic query may produce quantified
supporting facts in the numeric table; those facts do not create a second
mini-OSHA objective.

## Open inputs

- Maximum `n` per user turn
- Research constant `C`
- Whether the token budget includes the final `var_ans` answer and database/control serialization
- Token reserve for system prompts, control markers, and other overhead
- Conversion from the token limit to a character limit, if the Markdown contract must specify characters
