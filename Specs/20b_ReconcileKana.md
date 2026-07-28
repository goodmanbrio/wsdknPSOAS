# Spec 20b: Reconcile かな — Leng Quality + FiscalCal + Chart Alignment

## Frontmatter
- Write date: 20260728
- Update date:
- Codebase last changed date: 20260728
- Implemented Y/N: N
- Three remaining extraction quality problems (Problems 2 and 5
  moved to Spec 20c): (1) Leng sysprompt doesn't teach temporal,
  semantic, or accounting disambiguation — dumb extractions write
  wrong values; (3) FiscalCalResolver guesses FY end dates via
  Haiku + web_search, gets them wrong, shifts all quarterly
  period mappings; (4) charting ignores fiscal calendar
  alignment, misrepresenting temporal relationships in multi-firm
  comparisons.

## Problem space

### Problem 1: Leng extraction quality — dumb sysprompt

#### Problem definition

The Leng sysprompt (`sysprompts/pms2_leng/anthropic_hayasui.md`)
tells Leng what FORMAT to extract in (denom, unit, timeframe
rules) but not HOW TO REASON about ambiguous financial data.
Three classes of extraction error are entirely unaddressed:

**1a. Temporal mismatch — spot values written as fiscal year
metrics.**

Market Cap and Share Price are inherently SPOT values (price
at a point in time). They are not fiscal year aggregates.
The Leng sysprompt says nothing about this. When a Mizuho
report (dated Nov 2025) says "Market Cap: $17.163B", Leng
writes it as `A1 MktCap FY2025 = 17163 mn USD`. But that's
the spot price at report date, not a FY2025 projection or
average.

Worse: a second report (Jefferies, Q3 FY2026) says
"Market Cap: $94.7B" — a completely different spot price
7 months later. First-write-wins means whichever chunk Leng
hits first determines the stencil value. The Validator for
Mizuho chunk B1 even CORRECTLY rejected it ("No FY2026
market cap data present in chunk"), but a second Validator
from a different Mizuho chunk wrote B1=$17,332M anyway.

The stencil currently treats MktCap as a retrieve row with
`timeframe: annual`. The sysprompt enforces "annual = full
fiscal year only" but MktCap isn't a fiscal year metric.
This is a category error at the stencil design level
(Sekei), not just Leng.

Evidence: `PMS2_FailureModes.md` Root Cause A.

**1b. Semantic confusion — price target extracted as share
price.**

B5 SharePrice FY26 = $900. Source: OFC Recap table
"KEY STOCKS FEATURED: LITE | BUY | $900.00". That $900 is
the analyst's PRICE TARGET, not the current share price.
The Jefferies report had actual price $994.56 but the
Validator correctly rejected it as "current/recent price
snapshot, not FY2025 annual metric."

Irony: the Validator that caught the temporal issue
rejected the CORRECT value ($994.56), while the one that
missed it wrote the WRONG value ($900 PT).

The Leng sysprompt has zero instruction distinguishing
price targets from share prices. Table headers don't
always label this clearly — the table says "PRICE TARGET"
but Leng doesn't parse column headers as semantic context.

Evidence: `PMS2_FailureModes.md` Root Cause B.

**1c. GAAP vs non-GAAP — multiple EPS from same period.**

A6 EPS FY25 = $4.22. Source: Jefferies "Estimate changes"
table with "EPS | 2025A | 4.22". But Rosenblatt's
Components table shows FY2025A annual EPS = $2.04
(summing quarters: 0.18+0.42+0.57+0.88).

$4.22 vs $2.04 — both claim FY2025A. Likely difference:
GAAP vs non-GAAP, or different fiscal year alignment
(LITE FY ends June 30). Multiple Validators attempted
different EPS values (2.04, 2.06, 3.54, 4.22, 4.35).
First-write-wins gave it to $4.22.

Leng has no instruction about GAAP/non-GAAP preference.
It has no instruction about preferring company filings
over sell-side estimates. It doesn't even know that
multiple valid EPS values can exist for the same period.

Evidence: `PMS2_FailureModes.md` Root Cause C.

#### Points of failure

1. **Leng sysprompt** (`pms2_leng/anthropic_hayasui.md`) —
   23 lines. Covers format rules (denom, unit, timeframe,
   cross-lingual). Zero guidance on:
   - Spot vs period metrics
   - Price target vs share price vs market price
   - GAAP vs non-GAAP
   - Analyst estimate vs reported actual
   - Company filing vs sell-side report provenance
   - Table header parsing for semantic context

2. **Validator sysprompt** (`pms2_validator/anthropic_hayasui.md`)
   — 35 lines. "Literal match, period/timeframe, denom,
   unit." Timeframe check is correct but insufficient —
   it catches "wrong quarter" but doesn't catch "this is
   a spot value not a period aggregate." It doesn't
   distinguish price target from market price. It doesn't
   know about GAAP/non-GAAP.

3. **Stencil design** (`sekei_loop.py` / Sekei LLM) —
   classifies MktCap and SharePrice as `type: retrieve,
   timeframe: annual`. These metrics don't have a
   meaningful "annual" value. Sekei has no concept of
   "spot" type metrics that need different extraction
   logic.

4. **First-write-wins** (`validator_loop.py:194-200`) —
   compare-and-swap means whichever Validator finishes
   first writes the cell. No quality ranking between
   competing values from different sources. No
   consideration of source recency, provenance, or
   confidence.

---

### Problem 3: FiscalCalResolver — wrong FY end dates

#### Problem definition

`_resolve_fiscal_calendar` (`dispatcher.py:112-190`)
uses Haiku + `web_search=True` to look up a firm's
fiscal year end date, then computes calendar date ranges
for each period.

For LITE (Lumentum), it returned FY end = December 31.
LITE's actual FY end is **June 30**. This shifted every
quarterly period by 6 months:

```
Resolver said:                  Actual:
Q1FY2025 = Jan-Mar 2024        Q1FY2025 = Jul-Sep 2024
Q2FY2025 = Apr-Jun 2024        Q2FY2025 = Oct-Dec 2024
Q3FY2025 = Jul-Sep 2024        Q3FY2025 = Jan-Mar 2025
Q4FY2025 = Oct-Dec 2024        Q4FY2025 = Apr-Jun 2025
```

Downstream impact: Leng searched for "Q1FY2025 = Jan-Mar
2024" data — anything it found from that calendar window
got written. EPS values [2.51, 7.54, 11.63, 2.85] are
suspect: Q2=7.54 and Q3=11.63 look like annual or YTD
numbers, not single quarters.

Evidence: `PMS2_FailureModes.md` Root Cause D, Demo 2.

#### Why it fails

1. **One-shot LLM guess with no validation.** The
   resolver asks Haiku "What is LITE's fiscal year-end
   date? Search the web to confirm." Haiku returns a
   structured answer. No cross-check against:
   - The actual documents in `files_ingested/` (which
     contain fiscal year references in 10-K headers)
   - Known fiscal calendars of major companies
   - The user (who likely knows the FY end)

2. **web_search is unreliable.** Haiku's web_search
   tool may return stale, wrong, or ambiguous results.
   "Lumentum" might match an old entity or a subsidiary
   with a different FY end. The prompt gives an example
   ("if FY ends June 30, then Q1 = Jul-Sep") but Haiku
   can still get the FY end wrong and compute all date
   ranges from that wrong base.

3. **No fallback validation.** The resolver checks
   `if calendar:` (non-empty) and accepts. It doesn't
   sanity-check whether the returned FY end is
   plausible. It doesn't cross-reference against filing
   dates visible in the ingested documents (e.g. a 10-K
   filed in September likely has FY ending June 30, not
   December 31).

4. **Ask-user fallback is last resort.** The user
   fallback only triggers after 3 web_search failures
   (all returning empty). If Haiku confidently returns
   wrong data, the fallback never fires — wrong data
   passes validation because the dict is non-empty.

5. **Only fires for non-annual granularity.** Annual
   extractions skip FiscalCalResolver entirely
   (`dispatcher.py` only calls it when `granularity !=
   "annual"`). So the problem is invisible in annual
   demos and only surfaces when someone tries quarterly
   or half-yearly — the exact case where accuracy
   matters most.

#### Blast radius

FiscalCalResolver runs ONCE per firm, at the start of
each dispatcher. Its output (`fiscal_calendar` dict) is
injected into:
- Every Leng sysprompt for that firm (via
  `{{fiscal_calendar}}` template var)
- Every Validator sysprompt for that firm (same var)
- The Batch Planner's context (indirectly, via cell
  descriptions that reference periods)

If the FY end is wrong, EVERY Leng call for that firm
searches with wrong period-to-date mappings. EVERY
Validator checks against wrong dates. The error is
systematic, not random — it affects all cells for that
firm, all periods.

For annual granularity, the fiscal_calendar_text is
empty (no resolver call). So Leng gets no temporal
guidance at all — just "FY2025" with no date range.
This means Leng has to guess what FY2025 means from
chunk context alone. Usually fine (10-K headers say
"Fiscal Year Ended June 30, 2025"), but relies entirely
on the chunk containing the right header.

---

### Problem 4: Charting ignores fiscal calendar alignment

#### Problem definition

`stencil2chart.py:150` plots fiscal periods as categorical
x-axis labels:

```python
line, = ax.plot(periods, s["values"], label=s["metric"])
```

Where `periods = ["FY2021", "FY2022", "FY2023"]`. Every
firm's "FY2022" lands on the same x-tick. But different
firms have different fiscal year ends:

- **LITE** (Lumentum): FY ends June 30.
  FY2022 = Jul 2021 - Jun 2022. Midpoint: ~Jan 2022.
- **Innolight**: FY ends December 31.
  FY2022 = Jan 2022 - Dec 2022. Midpoint: ~Jul 2022.

When both are plotted on the same chart, LITE's FY2022
revenue sits on the same x-tick as Innolight's FY2022
revenue. But LITE's FY2022 ended 6 months BEFORE
Innolight's FY2022 started. The chart implies temporal
co-occurrence that doesn't exist.

At quarterly granularity it's worse:

```
LITE Q3FY2025 = Jan-Mar 2025    → calendar Q1 2025
Innolight Q1FY2025 = Jan-Mar 2025 → calendar Q1 2025
```

LITE's "Q3" and Innolight's "Q1" cover the SAME calendar
quarter but plot on different x-ticks (`Q3FY2025` vs
`Q1FY2025`). Comparing them on the current chart implies
they're 6 months apart when they're simultaneous.

#### Where the data is (and isn't)

The charting pipeline currently has NO access to fiscal
calendar data:

```
Phase 2 (merge_compute.py)
  → _serialize_to_display_stencils
    → display_stencil = {firm, periods: ["FY2025","FY2026"], rows: [...]}
      → PTECA (tool_pteca.py)
        → chart_input = {title, periods, series: [...]}
          → stencil2chart.py
            → ax.plot(periods, values)  ← periods are STRINGS
```

`periods` flows through the entire chain as opaque
strings. No FY end date attached. No calendar equivalent
computed. The display stencil doesn't carry
`fy_end_month_day`. The chart_input doesn't carry it.
`stencil2chart` couldn't normalize even if it wanted to.

The fiscal calendar data DOES exist upstream — it's
computed by `_resolve_fiscal_calendar` in `dispatcher.py`
and stored in the `fiscal_calendar` dict. But it's
consumed only by Leng/Validator sysprompts and never
propagated to the stencil, the display stencil, or the
charting layer.

#### What correct charting looks like

For a multi-firm chart comparing LITE and Innolight
revenue FY2021-FY2023:

**Current (wrong):**
```
x-axis:  FY2021    FY2022    FY2023
LITE:    ●─────────●─────────●
Inno:    ●─────────●─────────●
         ↑ same x-tick but 6 months apart in reality
```

**Correct (calendar-normalized):**
```
x-axis:  Q1'21  Q3'21  Q1'22  Q3'22  Q1'23  Q3'23
LITE:       ●─────────────●─────────────●
            ↑ Jul'20-Jun'21  Jul'21-Jun'22  Jul'22-Jun'23
            midpoint: Jan'21  midpoint: Jan'22  midpoint: Jan'23

Inno:              ●─────────────●─────────────●
                   ↑ Jan-Dec'21    Jan-Dec'22    Jan-Dec'23
                   midpoint: Jul'21  midpoint: Jul'22  midpoint: Jul'23
```

The x-axis becomes a CONTINUOUS calendar timeline, not
categorical fiscal period labels. Each datapoint is
plotted at its fiscal period midpoint (or end-date).
Firms with different FY ends naturally offset on the
x-axis, reflecting real temporal relationships.

For single-firm charts, this doesn't matter much —
the offset is consistent so trends are preserved. But
for multi-firm comparisons, it's the difference between
a correct and misleading visualization.

#### Points of failure

1. **`stencil2chart.py:150`** — `ax.plot(periods, values)`
   treats periods as categorical. matplotlib places them
   as equally-spaced string ticks. No temporal meaning.

2. **Display stencil contract** — `{firm, periods, rows}`
   has no FY end date, no calendar mapping. Can't
   normalize downstream without upstream data.

3. **`_serialize_to_display_stencils`** (`merge_compute.py`)
   — builds display stencils from work_stencil. Copies
   `periods` as-is. Doesn't attach fiscal calendar data
   even though it's available in the pipeline context.

4. **PTECA** (`tool_pteca.py`) — builds `chart_input`
   from display stencils. Passes `periods` through as
   strings. No normalization step.

5. **No calendar normalization function exists anywhere.**
   Converting "FY2022 with FY end June 30" →
   "midpoint = January 2022" or "end = June 2022" is a
   simple date computation, but nothing in the codebase
   does it.

---

### Coupling between Problems 1, 3, and 4

```
Problem 3 (FiscalCal wrong)
    ↓ wrong date ranges injected into all Leng/Validator calls
Problem 1 (Leng dumb)
    ↓ extracts wrong values (temporal, semantic, GAAP)
    ↓ first-write-wins locks in wrong values

Problem 3 (FiscalCal) ──also──→ Problem 4 (charting)
    ↓ FY end data never propagated to stencil/chart layer
    ↓ multi-firm charts misalign temporally
```

Problem 4 depends on Problem 3 being CORRECT (need
accurate FY end dates to normalize), but Problem 4 also
requires the data to be PROPAGATED through the pipeline
even when Problem 3 is correct — the plumbing doesn't
exist regardless of accuracy.

Fixing Problem 1 (smarter Leng) without fixing Problem 3
(correct fiscal calendar) means Leng is smart but
operating on wrong temporal context. Fixing Problem 4
(charting) without fixing Problem 3 means normalizing
against wrong FY end dates. All three need addressing,
but they can be built incrementally because they touch
different pipeline stages.

Problems 2 (divergence reconciliation) and 5 (stencil
misdesign) moved to Spec 20c.

---

## Outcome imagination
- Target UX 
- Wat was desired by user

# Solution space
- Idea of solving 
- Type: breaking, patch, config, etc
- Points of chg
  - Graph (pipeline) design affected (node existential chg, )
  - Downstream nodes affected (input/output contract)

## Graph Change 

## Hence File by file Change

## Failure modes

## Unit tests

## LLM unit tests

## Execution
