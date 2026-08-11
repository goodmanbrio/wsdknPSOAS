# Execution Discretion — 2026-08-11

## Design decisions

- Phase 1 begins as a standalone implementation inside
  `OSHA_summary_output_spec/`.
- The implementation is scaffolded before adding contract behavior, fixtures,
  or live model calls.
- Upstream integration with
  `wagasyanohimitunaLAG-osha` remains deferred until the deterministic
  contract tests pass.
- The deterministic renderer receives an injected token_counter; it does
  not select or invent the production tokenizer while Ssummary accounting
  remains an external input.
- Budget-pruning candidates use a local priority value where lower numbers are
  retained before higher numbers; this is an implementation mechanism for the
  approved summary-priority rule.
- The deterministic renderer owns the exact insufficient-evidence and invalid
  identifier block shapes; these paths do not call the LLM.
- Bibliography-order validation treats ordinary source entries as one ordered
  group followed by derived entries in creation order.
- The input-boundary test treats the public summarize_answer_sheet signature as
  the contract guard against accidental filepath, chunk, or retrieval inputs.
- Citation assignment stores full upstream source-reference strings as opaque
  keys; repeated identical references reuse the first local label and produce
  one bibliography entry.
- The first model-facing implementation is provider-neutral prompt
  construction; no model provider or live API call is selected by this
  execution portion.
- The selected live provider is DeepSeek through its OpenAI-compatible chat
  completions interface. The runtime model name remains explicit through
  DEEPSEEK_SUMMARY_MODEL rather than being hard-coded to Pro or Flash.
- A standalone CLI is the first executable harness. It accepts the main query,
  an optional answer-sheet Markdown path, and Ssummary; it does not perform
  storage or upstream pipeline integration.
- Architecture B was selected for the live model boundary: DeepSeek emits
  only a draft summary. The deterministic Python wrapper emits the final
  `OSHA_ID`, `OSHA_SUMMARY_TYPE`, section headings, local numeric labels, and
  bibliography.
- The draft boundary may remove a harmless outer Markdown fence, an optional
  `## Answer Summary` heading, and a model-generated `## Bibliography` tail.
  It rejects drafts that attempt to provide final OSHA metadata.
- Draft citations use the exact upstream `[Source: filename — section]` text.
  If one bracket contains multiple references separated by `; Source: `, the
  wrapper splits them into atomic references, replaces them with separate
  first-use local numeric labels, reuses labels for repeated references, and
  creates the bibliography.
- The initial upstream handoff will preserve the raw synthesizer answer for
  the current-turn response while creating a separate stored-context summary.
- The initial upstream handoff will supply `Ssummary` through a fixed `4000`
  integration constant. A configurable upstream field and dynamic budget
  derivation are deferred.
- The initial upstream handoff retries summary generation once with the same
  budget. A source-bearing answer sheet paired with an entirely uncited model
  draft is a failed attempt. If the retry is also entirely uncited, the bridge
  uses the last draft but attaches every exact answer-sheet source reference to
  its Bibliography without inventing claim-level inline labels. Other failures
  preserve the raw answer for the current turn and record the summary failure.
- If the provider returns empty summary content on both attempts, the bridge
  stores a structured `SUMMARY_UNAVAILABLE` artifact with query metadata and
  `[Source: unavailable]`; it does not omit the summary artifact.
- A successful summary is stored both as a separate in-memory registry handle
  and as a separate session Markdown file; raw-answer storage remains intact.
- A provider response truncated inside an upstream citation is treated as
  lower-priority incomplete detail: the wrapper drops that incomplete claim
  before citation assignment so malformed source text is not stored.
- Every parent and child bibliography item is rendered as its own Markdown
  paragraph, with one blank line between items for visual readability in
  editors such as VS Code. The renderer also expands a flattened parent/child
  group if an upstream formatter places several labels on one line.
- Approved citation-format change: preserve each full upstream filename once as
  a parent bibliography entry; group its sections beneath it as indented
  child labels such as `[1.1]` and `[1.2]`. Inline claims use the child label,
  and child lines contain only display section text without repeating
  `[Source: ...]`. Recognizable company/entity prefixes such as
  `Lumentum Holdings Inc. (LITE) >` are removed from child display text,
  including repeated semicolon-separated occurrences.
  Child indentation uses four non-breaking spaces (`U+00A0`) rather than four
  ASCII spaces, preventing VS Code from rendering the child group as a gray
  monospace code block.

## Contract authority

- Current authority: `07_CompleteFocusedOSHAOutputSpec.md`
- Execution workflow: `/Users/derickfeng/Desktop/DerickWorkingDir/wsnSpecsutekisa/references/Execution.md`
