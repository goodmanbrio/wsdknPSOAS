# Filing Conventions Brief — Metadata Enrichment Agent Handoff

## Context

Read `src/pto.py` and `src/sekei.py` to understand the retrieval pipeline.
ASK THE USER for context until you fully understand PTO's use case and
what it needs from chunk metadata. Do not assume — ask at full resolution.

## Problem

PTO hard-filters table chunks before BM25 scoring. Current filters:
chunk_type, fiscal_year, entity. This is not enough.

Concrete example: querying Amcor FY2023 Total Assets (balance sheet).
238 table chunks survive the hard filter. The actual consolidated balance
sheet ranked #12. Chunks that outranked it: an Obligor Group supplemental
balance sheet (from guarantor disclosure), a Note 17 Income Taxes table,
a Deed of Cross Guarantee balance sheet, fair value measurement tables.
All contain the words "assets", "total", "balance" — BM25 cannot tell
them apart from the real consolidated balance sheet.

The fix is not better scoring — it is better metadata. If chunks carried
filing structure information, PTO could filter to just the ~20 primary
financial statement tables before scoring, instead of searching all 238.

## Two tiers of metadata

Not every file in the database is a filing. Many are sellside reports,
industry research, analyst notes — unstructured, no predictable layout.
Do not force filing convention logic onto those.

**Tier 1 — all files.** Basic metadata that every chunk gets regardless
of whether the file is a recognized filing:
- chunk_type ("table" / "text")
- fiscal_year ("FY2023")
- dir_implied_firm ("Best Buy" — from parent directory name)
- dir_implied_sector ("Consumer Discretionary" — from grandparent dir)
- section (raw header text)

These already exist in ingest.py.

**Tier 2 — recognized filings only.** Rich metadata extracted from filing
structure. Only applied when the file matches a known filing convention.
Blank/absent for unrecognized files — PTO falls back to BM25 without them.

Starting ideas for tier 2 fields:
- filing_type — "10K", "10Q", "earnings_release", "8K", etc.
- is_primary_statement — boolean. Is this section in the primary
  financial statements zone, or in notes/supplemental/MD&A?
- statement_type — canonical label: "income_statement", "balance_sheet",
  "cash_flow", "equity", "". Maps messy real headers to standard types.

ASK THE USER what other tier 2 fields are needed. Do not decide
independently — present options and wait for user direction.

## Approach — filing convention detection

Financial filings follow standardized structures dictated by regulators.
The structure tells you where the primary financial statements live.
If you detect the filing type, you know the structure. If you know
the structure, you can tag which sections are primary vs notes vs
supplemental. This is deterministic — no LLM needed.

The approach has three steps per filing convention:
1. **Detect** — identify what filing type a file is from its headers
2. **Boundary** — find where the primary statement zone starts and ends
3. **Standardize** — map raw section headers to canonical statement types

ASK THE USER how to structure this approach before deciding on any
data structure or code. ASK THE USER which filings to look at and
which conventions to handle. Present findings and options at every
step — do not design independently.

## Illustrated examples (3 of ~30 conventions)

These show the kind of reasoning applied to 3 conventions. They are
NOT templates to copy. They illustrate the detection → boundary →
standardization pattern. ASK THE USER which conventions to tackle
and how to handle each one.

### SEC 10-K

**Detection:** file contains a `## Item 8` header (all SEC 10-Ks have this).

**Boundaries:** primary financial statements live between `## Item 8`
(upper) and the first `## Note \d` or `## Notes to` header (lower).
Everything between = primary. Everything before (MD&A, supplemental
guarantor disclosures) or after (notes) = not primary.

**Example — AMCOR_2023_10K.md:**
```
line  887:  ## Basis of Preparation          ← NOT primary (MD&A/supplemental, before Item 8)
line 1109:  ## Item 8. - Financial Statements ← UPPER BOUNDARY
line 1151:  ## Consolidated Statements of Income      ← PRIMARY → statement_type="income_statement"
line 1201:  ## Consolidated Balance Sheets             ← PRIMARY → statement_type="balance_sheet"
line 1260:  ## Consolidated Statements of Cash Flows   ← PRIMARY → statement_type="cash_flow"
line 1320:  ## Consolidated Statements of Equity       ← PRIMARY → statement_type="equity"
line 1360:  ## Note 1 - Business Description  ← LOWER BOUNDARY
line 2774:  ## Deed of Cross Guarantee Consolidated Balance Sheets  ← NOT primary (inside notes)
```

**Nuance:** section headers are messy. Boeing uses
`## The Boeing Company and Subsidiaries Consolidated Statements of Operations`
(company-prefixed). Amcor uses `## Amcor plc and Subsidiaries Consolidated
Statements of Income`. The statement_type mapping must handle prefixes,
"Condensed" variants, "(Unaudited)" suffixes, etc.

### SEC 10-Q

**Detection:** file contains `## Part I` and `## Item 1` with
"Financial Statements" in the header.

**Boundaries:** same lower boundary as 10-K (`## Note \d`). Upper
boundary is `## Part I...Item 1...Financial Statements` instead of
`## Item 8`.

**Example — AMCOR_2023Q2_10Q.md:**
```
line  135:  ## Part I - Financial Information Item 1. Financial Statements  ← UPPER BOUNDARY
line  137:  ## Condensed Consolidated Statements of Income   ← PRIMARY → statement_type="income_statement"
line  185:  ## Condensed Consolidated Balance Sheets          ← PRIMARY → statement_type="balance_sheet"
line  243:  ## Condensed Consolidated Statements of Cash Flows ← PRIMARY → statement_type="cash_flow"
line  296:  ## Condensed Consolidated Statements of Equity    ← PRIMARY → statement_type="equity"
line  347:  ## Notes to Condensed Consolidated Financial...   ← LOWER BOUNDARY
```

### SEC Earnings Release

**Detection:** no Item/Part structure. Contains financial statement
headers directly (e.g. `## CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS`).

**Boundaries:** none. Flat structure — no notes, no Item 8. All sections
treated as potentially primary. Cannot structurally distinguish the income
statement from segment information or non-GAAP reconciliations.

**Example — BESTBUY_2023Q4_EARNINGS.md:**
```
line  119:  ## CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS   ← statement_type="income_statement"
line  150:  ## CONDENSED CONSOLIDATED BALANCE SHEETS            ← statement_type="balance_sheet"
line  184:  ## CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS  ← statement_type="cash_flow"
line  228:  ## BEST BUY CO., INC. SEGMENT INFORMATION           ← statement_type=""
line  273:  ## REVENUE CATEGORY SUMMARY                         ← statement_type=""
line  301:  ## RECONCILIATION OF NON-GAAP FINANCIAL MEASURES    ← statement_type=""
```

is_primary_statement = True for ALL sections here (no structural
discrimination possible). PTO relies on BM25 + destructive fusion
to sort them out.

## Agent behavioral rules

1. You are a tool, not an architect. You do not make design decisions.
2. At every decision point, present options and ask the user.
3. Do not design data structures without user approval.
4. Do not write code without user approval.
5. Present findings, then wait for command.
6. If something is ambiguous, ask. Do not guess and run with it.
7. You may read filings freely to gather information. Everything
   else — designing, structuring, building — requires user direction.
