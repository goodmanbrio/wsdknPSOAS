# PMS2 Failure Modes

## Demo 1: Annual, 2 firms — EV/EBITDA, P/E, Laser Revenue

Date: 2026-07-25
Input: `firms=["LITE","Innolight"], query="EV/EBITDA, P/E, Laser Revenue", periods=["FY2025","FY2026"], granularity="annual"`

5 divergence warnings. 3 root causes. All are data quality /
prompt quality issues, not pipeline bugs.

### Root cause A: Temporal mismatch (first-write-wins problem)

Corpus has Mizuho report (Nov 2025, LITE at $242, mktcap $17B)
AND Jefferies report (Q3 FY26, LITE at $994, mktcap $94.7B).
First-write-wins means whichever Leng hits the cell first wins.

- A1 MktCap FY25 = $17.163B (Mizuho Nov-25 current mktcap)
- B1 MktCap FY26 = $17.332B (Mizuho, same table, NOT a FY26 projection)
- B8 EV Leng-direct = $94.792B (Jefferies current: $94,682M mktcap + $110M net debt)
- Formula EV = R1+R2 = $17.332B + $0.11B = $17.442B

The Mizuho "Market Cap" is the SPOT value at report date (Nov 2025),
NOT a FY25/FY26 projection. Leng correctly extracted the number but
assigned it to the wrong period. The Validator for the Mizuho chunk
even caught this — it rejected B1 with "No FY2026 market cap data
present in chunk". But a SECOND Validator from a different Mizuho
chunk wrote B1=$17,332M anyway.

Decision implications: Market cap / share price are inherently
spot values. The Leng prompt needs to either (a) only extract from
chunks that clearly label the period, or (b) the stencil should
not request "Market Cap FY2025" as a retrieve — it's not a fiscal
year metric. Possible future fix: Sekei classifies market-cap-like
metrics as "spot" type, handled differently.

### Root cause B: Semantic confusion (price target vs share price)

B5 SharePrice FY26 = $900. Source: OFC Recap table "KEY STOCKS
FEATURED: LITE | BUY | $900.00". That's the PRICE TARGET, not the
share price. The Jefferies report had actual price $994.56 but
that Validator correctly rejected it as "current/recent price
snapshot, not FY2025 annual metric."

Irony: the Validator that caught the temporal issue rejected the
CORRECT value ($994.56), while the one that missed it wrote the
WRONG value ($900 PT).

Decision implications: Leng prompt needs explicit instruction to
distinguish price targets from share prices. Table headers don't
always make this obvious (the table just says "PRICE TARGET" but
Leng didn't parse the header).

### Root cause C: EPS source inconsistency

A6 EPS FY25 = $4.22. Source: Jefferies OFC Recap "Estimate changes"
table showing "EPS | 2025A | 4.22". This is a CONSENSUS estimate
(the same table shows "Cons. EPS | 0.95 | 4.22"). Meanwhile, the
Rosenblatt report's Components Segment table shows FY2025A annual
EPS = $2.04 (summing quarters: 0.18+0.42+0.57+0.88).

$4.22 vs $2.04 — both claim FY2025A. Likely difference: GAAP vs
non-GAAP, or different fiscal year alignment (LITE FY ends June 30).
Multiple Validators attempted different EPS values (2.04, 2.06,
3.54, 4.22, 4.35) — first-write-wins gave it to $4.22.

### Summary

Pipeline works. Extraction quality is the bottleneck.

The divergence warnings are doing exactly what they should. The
5 warnings correctly identified 5 cases where Leng-direct values
conflict with formula-computed values. In all cases, the conflict
traces to prompt-level issues (temporal context, semantic disambiguation,
GAAP vs non-GAAP) not pipeline bugs.

Fix priorities for prompt tuning:
1. Leng: "Market Cap and Share Price are spot values — only extract
   if chunk explicitly labels the period"
2. Leng: "Price target != share price"
3. Leng: "Prefer non-GAAP EPS unless otherwise specified" (or make
   GAAP/non-GAAP a stencil row attribute)

---

## Demo 2: Quarterly, 1 firm — EPS, Laser Revenue

Date: 2026-07-25
Input: `firms=["LITE"], query="EPS, Laser Revenue", periods=["FY2025"], granularity="quarterly"`

8/8 cells filled after fix (see Bug 1 below). Two issues found.

### Root cause D: FiscalCalResolver wrong FY end

FiscalCalResolver (Haiku + web_search) reported LITE FY ends
December 31. LITE's actual FY ends **June 30** (Lumentum's
fiscal year runs Jul 1 - Jun 30).

Result: all quarterly periods mapped wrong:
- Q1FY2025 resolved to Jan-Mar 2024 (should be Jul-Sep 2024)
- Q2FY2025 resolved to Apr-Jun 2024 (should be Oct-Dec 2024)
- Q3FY2025 resolved to Jul-Sep 2024 (should be Jan-Mar 2025)
- Q4FY2025 resolved to Oct-Dec 2024 (should be Apr-Jun 2025)

Downstream impact: Leng searched for Q1FY2025 data meaning
Jan-Mar 2024 — extracted whatever it found, which may be from
the wrong fiscal quarter entirely. EPS values [2.51, 7.54,
11.63, 2.85] are suspect — Q2=7.54 and Q3=11.63 look like
cumulative/annual numbers, not single quarters.

### Root cause A (repeat): EPS quarterly vs cumulative

Same first-write-wins problem as Demo 1. Multiple sources have
different EPS figures for the same period label. Q2 EPS = 7.54
is likely an annual/YTD figure, not a single quarter. First
validated write wins regardless.

---

## Bug: LLM-only confirmation gate (no code-level enforcement)

Date: 2026-07-27
Scope: Sekei (PMS2), but pattern exists anywhere an LLM `ask_user`
result gates a subsequent action (PTECA, orchestrator, etc.)

### Symptom

Pipeline advances to Phase 1 (dispatchers) before user finishes
typing their answer to the stencil confirmation prompt. The LLM
calls `finalize_stencil` without the user having said "y".

### Root cause

The sekei loop feeds `ask_user` answers to the LLM as
`"User answered: {answer}"` and trusts the LLM to only call
`finalize_stencil` if the answer is affirmative. No code-level
gate exists. If the LLM misreads, hallucinates, or ignores the
answer, `finalize_stencil` runs unconditionally.

`_handle_finalize` has no confirmation step of its own — it
validates, saves, and returns immediately.

### Bandaid fix (sekei_loop.py only)

Added `user_confirmed` flag in `run_sekei`. After each `ask_user`,
flag is set `True` only if answer matches `y/yes/ok/confirm/lgtm`.
Before `finalize_stencil` dispatches, if `user_confirmed` is
`False`, Python hard-prompts the user directly ("Finalize stencil?
[y/n]") and rejects if they say no. LLM cannot bypass this.

### Not yet fixed elsewhere

The same pattern (LLM-only gate on user confirmation) exists in:
- PTECA clarification flow (`tool_pteca.py`)
- Orchestrator `ask_user` tool (`execute_tool.py` / `agent_loop.py`)
- Any agent loop that relies on the LLM to respect a "only proceed
  after user says yes" sysprompt instruction

Each of these needs a similar code-level gate if the action after
`ask_user` is destructive or non-reversible.
