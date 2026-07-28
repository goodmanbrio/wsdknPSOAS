# Spec 20c: Kyodana KAISEN — Divergence Reconciliation + Stencil Revision Loop

## Frontmatter
- Write date: 20260728
- Update date:
- Codebase last changed date: 20260728
- Implemented Y/N: N
- When compute-row divergence exceeds threshold, the pipeline
  currently logs and discards the signal rather than routing it
  to a resolution mechanism — this spec addresses that. When
  Sekei's stencil design is discovered mid-extraction to be
  wrong (wrong metric names, wrong decompositions), no feedback
  path exists back to Sekei to revise the schema while
  preserving already-extracted values — this spec addresses
  that too. Problems 1, 3, 4 (Leng quality, FiscalCalResolver,
  charting alignment) remain in Spec 20b.

## Problem space

### Problem 2: Divergence without reconciliation

#### Problem definition

Phase 2 (`merge_compute.py:104-122`) detects when a
compute row has BOTH a Leng-direct value and a formula-
computed value that diverge >2%. It emits a warning:

```
⚠ B8 (EV LITE): Leng-direct=94792000000,
  formula R1+R2=17442000000, divergence=438%
```

Then keeps the Leng-direct value and moves on.

This is a diagnostic, not a resolution. The 438%
divergence means one of three things:

1. **Helper rows are wrong** — MktCap (R1) or NetDebt
   (R2) were extracted from wrong context (temporal
   mismatch, wrong report). Formula result is garbage.
   Leng-direct EV is probably right.

2. **Leng-direct is wrong** — Leng extracted an EV
   value from the wrong period, wrong firm, or wrong
   context (e.g. EV from a different scenario/case).
   Formula result from correct helpers is right.

3. **Both are wrong** — helpers and direct all extracted
   from different temporal contexts. Neither value is
   trustworthy.

Currently NO mechanism to:

- **Inspect why** — the warning doesn't trace which
  source wrote which helper. The user sees a number
  but has no forensic path to debug it.
- **Pick a winner** — always keeps Leng-direct,
  regardless of which has better provenance.
- **Re-extract** — if helpers are suspect, no way to
  re-dispatch with tighter constraints.
- **Flag to user** — the warning prints to terminal
  but the orchestrator LLM and the user never get
  structured feedback about which values are suspect.

#### Coupling with Problem 1

Divergence is CAUSED by Problem 1. If Leng extracted
MktCap correctly (from the right temporal context),
the formula EV would match the Leng-direct EV. The
divergence is a SIGNAL that something upstream went
wrong. Currently that signal is wasted — logged and
ignored.

#### Points of failure

1. **`merge_compute.py:104-122`** — divergence check
   is fire-and-forget. Prints warning, takes no action
   beyond keeping Leng-direct. No structured output
   of divergence data for downstream consumption.

2. **No divergence-to-source tracing** — `work_stencil
   ["sources"]` maps cell_id → node_id. To understand
   WHY R1 and R8 diverge, you need to trace both
   R1's source (Mizuho chunk) and R8's source
   (Jefferies chunk) and compare their temporal
   contexts. This is possible with existing data but
   no code path does it.

3. **No user-facing divergence report** — the display
   stencil (`_serialize_to_display_stencils`) shows
   final values only. No asterisk, no footnote, no
   confidence indicator. User sees "EV = 94.8B" and
   has no idea it contradicts the formula by 438%.

4. **No feedback to dispatcher** — if Phase 2 detects
   divergence, it can't tell the dispatcher "re-extract
   R1 MktCap with tighter temporal constraints." The
   pipeline is strictly sequential: Phase 1 finishes
   entirely, then Phase 2 runs. No loop-back.

---

### Problem 5: Stencil misdesign discovered mid-extraction — no feedback path

#### Problem definition

Sekei designs the stencil BEFORE any extraction happens.
It guesses metric names, decompositions, and formulas
from the query and domain knowledge. But the actual
documents might not match Sekei's assumptions:

- Sekei creates "Laser Revenue" but the firm reports
  "Photonics Solutions Revenue"
- Sekei creates "Net Debt" as a retrieve row but the
  firm only reports "Total Debt" and "Cash" separately
  (should be two retrieves + a compute)
- Sekei creates "EBITDA" but the firm only reports
  "Adjusted EBITDA"
- Sekei creates a segment breakdown that doesn't match
  the firm's actual segment reporting structure

The information that the stencil is wrong EXISTS in the
pipeline — it surfaces during extraction — but has no
path back to Sekei.

#### Where the signal surfaces and where it dies

**Leng sees the correct metric name but can't use it.**

Leng receives cell descriptions like:
```
A3 = Laser Revenue, FY2025, annual, type=retrieve, unit=USD
```

And a chunk containing:
```
Photonics Solutions Revenue: $1,247M
```

Leng can't map "Photonics Solutions Revenue" to cell A3
"Laser Revenue" — they don't match. So it returns
`{cells: {}}`. The chunk text containing the ACTUAL
metric name is discarded. Leng had the answer but
couldn't express "I found something relevant under a
different name."

**Validator sees mismatches but can only reject.**

If Leng does extract a value (maybe it fuzzy-matched),
Validator might reject with reason "chunk says
'Photonics Solutions' not 'Laser Revenue'". That reason
string is stored in `this_run_rejections` inside
`leng_caller.py` and reported to BP as a status line.
But BP doesn't parse rejection reasons semantically —
it just sees "4 rejected" and tries different files.

**BP sees zero-fill patterns but not root cause.**

After 2 dispatcher iterations with 0 new cells for R3,
BP knows R3 is unfillable from the files it tried. Its
`searched_files` dict shows which files were attempted.
But BP doesn't know WHY — was the metric not in those
files at all? Was it under a different name? Was it in
a different denomination? BP can call `ask_user` to
escalate, or `report_exhausted` to give up. It CANNOT
say "rename R3 from 'Laser Revenue' to 'Photonics
Solutions Revenue' and retry."

**Dispatcher sees dry_run_count but not root cause.**

The dispatcher loop (`dispatcher.py:345`) counts
consecutive zero-fill iterations. At `_MAX_DRY_RUNS`
it asks the user "2 iterations found nothing. Still
missing: R3 Laser Rev. continue? [y/n]". The user
sees the symptom (R3 unfilled) but not the cause
(wrong metric name). If user says continue, it burns
more tokens searching for a metric that will never
match.

#### Information flow gap

```
Chunk text: "Photonics Solutions Revenue: $1,247M"
    ↓ Leng reads it
    ↓ can't match to "Laser Revenue"
    ↓ returns {cells: {}}          ← SIGNAL LOST HERE
    ↓
Validator: never fires (no hit)    ← NO SIGNAL
    ↓
LengCaller: "0 hits from 40 chunks" ← AGGREGATE ONLY
    ↓
BP: "searched 3 files, 0 hits for R3" ← NO WHY
    ↓
Dispatcher: dry_run_count++          ← NO WHY
    ↓
User: "still missing R3. continue?" ← NO DIAGNOSIS
```

At every stage, the signal degrades from specific
("the metric is called something else") to aggregate
("0 hits") to binary ("dry run? y/n").

Even if the user realizes the problem and tells the
dispatcher "it's called Photonics Solutions Revenue",
there's no mechanism to:
- Edit the stencil row (rename metric)
- Preserve already-filled cells for other rows
- Re-run extraction for only the renamed row
- Propagate the edit to the work stencil and
  downstream formulas

The pipeline is one-shot: Sekei designs → extract →
merge → done. No revision loop.

#### Frequency

This is NOT a rare edge case for segment-level metrics.
Company segment names are idiosyncratic:
- "Cloud & Networking" vs "Datacom" vs "AI Infrastructure"
- "Consumer Electronics" vs "Mobile Products" vs "iPhone"
- "Automotive" vs "eMobility" vs "Electric Vehicles"

Sekei guesses segment names from domain knowledge and
the user's query. For well-known companies (Apple
reports "iPhone Revenue"), it usually gets it right.
For less-covered companies or firms that recently
renamed segments, it guesses wrong. Multi-firm
queries amplify this — segment naming conventions
differ across firms even in the same industry.

Annual metrics (Revenue, EBITDA, EPS) are usually
standardized names and hit fine. Segment breakdowns,
non-standard ratios, and regional reporting
conventions are where stencil misdesign fires.

#### What needs to be preserved

When the stencil needs revision, some rows are already
correctly filled. A 12-row stencil might have 8 rows
filled and 4 rows wrong. The filled rows represent
real extracted+validated data that cost API tokens.

If the stencil is re-designed from scratch, those 8
rows' values need to survive. They're currently keyed
by positional cell IDs (`A3`, `B7`) which are tightly
coupled to row numbering — any structural change
(add/delete/reorder rows) invalidates the cell IDs.

This is a data preservation problem nested inside a
schema migration problem: the "schema" (stencil
structure) needs to change while "data" (extracted
values) needs to be remapped to the new schema.

---

### Coupling between Problems 2 and 5

```
Problem 5 (stencil misdesign)
    ↓ wrong metric names produce zero hits
    ↓ Leng/Validator see the right data but can't match it
    ↓ information dies at extraction layer, no path to Sekei
    ↓ dry_run_count triggers but cause is opaque
    ↓ unfilled cells flow into Phase 2 as gaps

Problem 2 (no reconciliation)
    ↓ compute rows with wrong helper values produce divergence
    ↓ divergence is logged, not diagnosed
    ↓ no source-tracing to identify which helper cell is bad
    ↓ no re-dispatch possible even if bad cell is identified
```

**How they interact:** Problem 5 produces unfilled or
wrong-named rows. When those rows are helpers in a
compute formula (e.g. MktCap as helper for EV), Phase 2
sees divergence (Problem 2). The divergence IS the
symptom of the misdesign — but Problem 2's mechanism
has no way to ask "is this compute row diverging because
its helpers have wrong names upstream?" Both problems
share the same root gap: no feedback path from Phase 2
or extraction back to Sekei.

A revision loop that could re-name stencil rows (Problem
5 fix) would also need divergence signals to tell it
WHICH rows to re-name (Problem 2 fix). They are
co-dependent — solving one without the other leaves
the feedback loop incomplete.

**Reference to Problems 1, 3, 4:** Problems 1 (Leng
sysprompt quality), 3 (FiscalCalResolver), and 4
(charting alignment) are addressed in Spec 20b. Problem
1 is upstream cause of many divergence events but is a
separate fix surface. Problem 3 bad FY dates also
produce divergence but through a different mechanism
(temporal context wrong, not metric name wrong).

---

## Outcome imagination
- Target UX
- What was desired by user

## Solution space
- Idea of solving
- Type: breaking, patch, config, etc
- Points of chg
  - Graph (pipeline) design affected (node existential chg)
  - Downstream nodes affected (input/output contract)

## Graph Change

## Hence File by file Change

## Failure modes

## Unit tests

## LLM unit tests

## Execution
