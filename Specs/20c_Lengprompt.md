# Spec 20c: Leng Quality

## Frontmatter
- Write date: 
- Update date: 
- Codebase last changed date: 
- Implemented Y/N: N
- 
- **Status**: 

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
