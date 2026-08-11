# Focused OSHA Output Spec — Scope

## In scope

1. Define the Markdown instructions that teach each independent mini-OSHA to produce a self-contained summary for the master LLM.
2. Define the mini-OSHA output contract, including machine-readable footnote markers and a bibliography at the end of each OSHA block.
3. Specify that the decomposer has already split the work and each mini-OSHA receives exactly one semantic or numerical query.
4. Account for the fact that a mini-OSHA does not know the larger parent query, sibling branches, or broader PSOAS objective.
5. Provide a sample-derived semantic-output fixture for iterative prompt testing.

## Out of scope

- Redesigning the broader OSHA system
- Retrieval or ranking changes
- PMS2 or unrelated harness behavior
- Implementing code changes
- Defining the master LLM's broader reasoning behavior beyond consuming the mini-OSHA contract
- Defining whether `var_ans` receives raw Markdown or Python-parsed structured data
- Implementing or specifying `var_ans`, database, or orchestrator integration
