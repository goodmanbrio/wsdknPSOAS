# Spec 22b: Dispatcher / Batch Planner Gate Patch

## Frontmatter
- Write date: 20260731
- Update date:
- Codebase last changed date: 20260731
- Implemented: N
- **STATUS: PROBLEM SPACE ONLY.** No solution space, no graph diff, no
  file-by-file change. Do not implement from this document.
- 3 sentence summary: PMS2 Phase 1 puts its only human checkpoint in the
  node that has nothing useful to ask — the Dispatcher, which holds a
  count of missing cells — while Batch Planner, the node that knows which
  cells it cannot place and which files it already tried, is told by its
  sysprompt to hedge rather than ask and routes its give-up condition
  into a silent, ungated exit. The dispatcher gate that does exist is
  additionally forgeable (22a), treats an empty line as an abort, records
  `user_aborted` for decisions no human made, and is unbacked by any
  working interrupt. This spec covers the dispatcher/BP side of the
  20260730 runaway; the input-layer root cause is `22a_CLInputBlock.md`,
  which is load-bearing for anything here.

Related: `22a_CLInputBlock.md`. **22a must land first** — see Dependency.

---

## Problem space

### Problem definition

**The human contact point is in the wrong node.**

The Dispatcher loop is *intended* to keep iterating as long as any cell
of any kind gets filled (confirmed with the user — see "Not a defect"
below). Given that, the dispatcher's dry-run gate is not the control
surface and never was: it can only fire once BP has already stopped
producing, i.e. after the moment when a human could have helped. And the
dispatcher knows nothing useful to ask about — it holds a count of null
cells, not a reason for them.

Batch Planner is the only node that knows *which* cells it cannot find
and *which* files it has already speculated over. It has an `ask_user`
tool. Its sysprompt tells it not to use it, and routes the exact
condition that should trigger a question — "no new information to try" —
straight into a silent, ungated exit instead (PF6/PF7/PF8).

Secondarily, the dispatcher gate that does exist is itself broken:
`run_dispatcher` (`dispatcher.py:274`) is an unbounded loop with three
exits, only one of which involves the human, and that one is:

- **rarely reachable** — it opens only after two consecutive iterations
  write zero cells anywhere in the job stencil,
- **sampled at most once per two iterations** — a `y` resets the counter,
  buying an uninterruptible stretch of two full BP + Leng cycles,
- **unable to distinguish an answer from the absence of one** — `""` and
  `"n"` take the same branch,
- **mislabelling** — that branch writes `status="user_aborted"`, a string
  asserting a human decision, into data that flows on to Phase 2 and to
  the orchestrator's report,
- **not backed by a working interrupt** — Ctrl-C cannot reach the worker
  threads; Ctrl-D silently mass-aborts every firm.

Separately, the exit that BP itself controls — `report_exhausted` — has
**no human gate at all**, and BP's fallthrough at `_MAX_TURNS` gives up
silently.

Net: PMS2 Phase 1 can run indefinitely with no human checkpoint, and when
it does stop, the recorded reason may be fiction.

### Failure instance

Same run as 22a — 20260730, `run_pms2` over LITE / CIEN / COHR. See
`22a_CLInputBlock.md` for the verbatim transcript. Dispatcher-side facts:

- CIEN reached `dry_run_count >= 2` at iteration 3, gate rendered,
  `input()` returned `""` (22a scenario B), `startswith("y")` False →
  `status = "user_aborted"`, loop exited.
- COHR reached the same gate, `input()` returned a stale `y` (22a
  scenario D), `dry_run_count` reset to 0, `iter 3` began — a further two
  iterations minimum before the human could be asked again.
- BP never called `report_exhausted` for COHR; it produced a batch plan
  for iteration 3 and returned `"complete"`.
- LITE was still mid-fan-out (`475/477 Lengs done`) throughout.
- The user ended the session with Ctrl-C. `tests/debug` is empty — the
  traces were destroyed by the only escape available.

User's own account: *"human DIDN'T enter y … somehow dispatch keeps
looping"* and *"bp didn't askuserquestion for exhaustion either."* Both
are accurate readings of what the code does.

### Points of failure

Ordered by centrality to the problem as now defined: **PF6/PF7/PF8 are
primary** (the human is never asked by the node that could ask usefully).
PF1–PF4 and PF9 are secondary — the dispatcher gate that does exist is
broken and unbacked. PF2/PF3 and PF10–PF12 are the stop-and-forensics
cluster: no working interrupt, and evidence destroyed both by hard kills
and, silently, on clean runs.

| ID | Point | Location |
|---|---|---|
| PF1 | `""` and `"n"` collapse to the same branch, and the branch is named after the one that didn't happen. `status="user_aborted"` propagates into `job_stencil["status"]`, Phase 2, and the orchestrator's summary. The system reports a decision the human never made. | `dispatcher.py:354-358` |
| PF2 | Ctrl-C is not an abort. `KeyboardInterrupt` is delivered to the main thread only; `with ThreadPoolExecutor(...)` `__exit__` calls `shutdown(wait=True)` and blocks until every dispatcher finishes its current Leng fan-out. Workers never see it. | `pms2.py:191-208` |
| PF3 | Ctrl-D is the only fast exit, and it is indiscriminate: `EOFError` → `_is_dead=True` → every pending question drained with `""` → **every firm** takes the PF1 branch and is recorded as `user_aborted`. | `terminal_router.py:272-284` + PF1 |
| PF4 | Gate duty cycle ≥2 iterations. `y` sets `dry_run_count = 0`, so the next checkpoint is two full iterations away. From the failure instance an iteration is a BP agent loop plus ~477 Leng calls. The price of one wrong or stale answer is two complete cycles, not one prompt. | `dispatcher.py:356` |
| **PF6** | **BP exhaustion is ungated.** `bp_result == "exhausted"` → set status, `break`, no ask. This is the exit a well-behaved BP is supposed to take, and it never involves the human. | `dispatcher.py:326-328` |
| **PF7** | **BP gives up silently on turn exhaustion.** Falling out of `_MAX_TURNS = 4` returns `"exhausted"` with only a `channel.print`. Indistinguishable downstream from a reasoned `report_exhausted`. | `batch_planner.py:438-443` |
| **PF8** | **The sysprompt routes the "can't find it" condition away from the human.** Rule 8 gives `report_exhausted` a concrete trigger — *"Iteration > 0 and no new information to try"* — the precise condition that should produce a question. Rule 9 gives `ask_user` only vague triggers (*"genuinely unsure about which files to try"*) plus an explicit prohibition (*"NOT for routine routing decisions — hedge instead"*), reinforced by rule 2 (*"Hedging over certainty"*). An obedient BP therefore terminates silently at exactly the moment a handhold was available. | `sysprompts/pms2_batch_planner/anthropic_sonnetmedthink.md` rules 2, 8, 9 |
| PF9 | The gate's answer format is never displayed. The prompt string is `"... continue? [y/n]"` but renders as `"... continue? "` — rich eats the bracketed text (22a/PF6, reproduced). The human is asked a question whose accepted answers are invisible. | `dispatcher.py:352` via `terminal_router.py:264` |
| PF10 | Traces flush only at iteration boundaries and in `finally`. A `KeyboardInterrupt` during a `ThreadPoolExecutor` fan-out does not reliably run worker `finally` blocks. The one scenario where forensics matter most — a runaway — is the one that produces no traces. | `dispatcher.py:276-283, 369-375` |
| PF11 | The Leng/Validator trace is one buffer per LengCaller **batch**, written once at the end. All ~477 Lengs and every Validator append into `PMS2-lv-{firm}` in memory (`with_trace` at `leng_caller.py:327,414`) and hit disk only in the `finally` at `:484`. It is simultaneously the fattest buffer, the longest-lived, and the last written — a kill mid-batch loses every prompt and response in it. | `leng_caller.py:230,484` |
| PF12 | Traces overwrite each other across iterations. `_build_filename` keys on the component string alone and `flush_to_disk` opens with `"w"`. LengCaller's component is `PMS2-lv-{firm}` with no iteration in it, and the Dispatcher's is `PMS2-disp-{firm}` flushed once per iteration to the same path — so **iteration N destroys iterations 0..N-1**. Batch Planner escapes only because its name embeds `#{iteration}`. Even a clean, uninterrupted run keeps only the last iteration's Leng/Validator evidence, silently. | `trace.py:127-145`, `leng_caller.py:230`, `dispatcher.py:248,279` |

### Not a defect — PF5 retracted

An earlier draft listed "the dispatcher loop has no iteration ceiling,
and one filled cell of any kind resets `dry_run_count`" as PF5.
**Retracted.** Confirmed with the user: looping while any cell fills is
the intended behaviour, and the any-cell dry metric is the intended
definition.

The observation still matters, but as a *consequence* rather than a
defect: because the loop is designed to run while progress of any kind
continues, the dispatcher gate is structurally a late signal. It fires
only once BP has gone completely unproductive for two iterations — i.e.
after the useful window has closed — and it can ask only "continue?",
because the dispatcher holds a count, not a reason. That is the argument
for moving the human contact point up into BP (PF6/PF7/PF8), not for
capping the loop.

### Not a disincentive — BP's turn budget

Asking is nearly free for BP. `ask_user` resets `turn_counter = 0`
(`batch_planner.py:424`) before the `turn_counter += 1` at `:436`, so a
question **refills** BP's 4-turn budget rather than spending it. There is
no mechanical pressure against asking. The bias is entirely in the
sysprompt (PF8) — which is what makes a prompt-level change plausibly
sufficient at this layer.

---

## Failure mechanism

### The loop and its doors

```
while state.status == "running":

    ├─ all ans cells filled?          ──────────────► complete
    │
    ├─ BP returned "exhausted"?       ──────────────► bp_exhausted
    │        └─ no ask ── PF6                    ◄── THE PRIMARY PROBLEM
    │        └─ may be a silent _MAX_TURNS fallthrough ── PF7
    │        └─ the prompt sends "nothing new to try" HERE
    │           instead of to ask_user ── PF8
    │
    ├─ dry_run_count >= 2?            ──────────────► ASK USER
    │        └─ a late signal by construction (see PF5 retraction)
    │        └─ can only ask "continue?" — holds a count, not a reason
    │        └─ its hint is invisible ── PF9
    │        └─ its answer may be stale ── 22a
    │        └─ "" takes the abort branch ── PF1
    │
    └─ else ────────────────────────────► iterate again
             intended: loop while any cell fills
```

### Gate semantics — two very different inputs, one branch

```
answer = channel.input("... continue? [y/n]")     ← renders as "continue? " (PF9)
              │
   answer.strip().lower().startswith("y")
              │
      ┌───────┴────────┐
     TRUE            FALSE
      │                │
 dry_run_count = 0   status = "user_aborted"
 loop again                   ▲
                              │
             ┌────────────────┼────────────────┐
            "n"              ""             "   "
       a decision      a stale newline    whitespace
                       NOT a decision
                       (22a scenario B)
```

There is no third exit for "that wasn't an answer". PF1.

### Duty cycle — what a single stray `y` costs

```
iter 0   [BP + ~477 Lengs]   1 cell     dry=0
iter 1   [BP + ~477 Lengs]   0 cells    dry=1
iter 2   [BP + ~477 Lengs]   0 cells    dry=2  ──► GATE ──"y"──► dry=0
iter 3   [BP + ~477 Lengs]   0 cells    dry=1        ┐
iter 4   [BP + ~477 Lengs]   0 cells    dry=2  ──► GATE   ├ no door here
         └──────────── uninterruptible ─────────────┘
```

PF4. And per PF2/PF3 there is no interrupt underneath that stretch.

### Why the dispatcher gate is the wrong instrument

The dry-run metric counts **every** non-null value in
`job_stencil["values"]`, helper rows included:

```python
filled_after = sum(1 for v in state.job_stencil["values"].values()
                   if v is not None)
```

Intended, confirmed with the user. The consequence is that the gate is a
*trailing* indicator: it opens only after BP has produced nothing
whatsoever, twice. And when it opens, the only question the dispatcher
can pose is "continue?" — it holds `null_ans_cells` and a count, not a
hypothesis about why those cells are missing or which files might carry
them.

BP holds all of that: the active cells, the file inventory,
`searched_files` (what was tried, what was found, what was rejected and
why), and its own speculation about filename → content. It is the only
node that can ask a question a human can actually answer. It asks it
almost never, by prompt design (PF8), and its give-up path is
unconditional (PF6/PF7).

### The sysprompt routing, as written

`sysprompts/pms2_batch_planner/anthropic_sonnetmedthink.md`:

```
rule 2   "Hedging over certainty."                     ─┐
                                                        ├─ push away from asking
rule 9   "When to ask_user:                            ─┘
            - Systematic unit mismatch pattern
            - Genuinely unsure about which files to try    ← vague
            - NOT for routine routing decisions — hedge instead"

rule 8   "When to call report_exhausted:
            - All promising files tried, remaining cells are
              forward estimates not in any source
            - User explicitly said to stop
            - Iteration > 0 and no new information to try"  ← CONCRETE
                          │
                          ▼
              report_exhausted  →  dispatcher: bp_exhausted, no ask (PF6)
```

The condition that should summon the human is named precisely once in
the prompt — and it is wired to the silent exit.

### Abort paths

```
Ctrl-C
  main thread   with ThreadPoolExecutor(...) as pool:   ← KeyboardInterrupt HERE
                    for fut in as_completed(futures): ...
                __exit__ → shutdown(wait=True)          ← BLOCKS
  worker CIEN   ──── inside 477 Leng HTTP calls ─────►  never interrupted
  worker COHR   ──── inside BP agent loop ───────────►  never interrupted

Ctrl-D
  console.input() raises EOFError
     → _is_dead = True   (permanent)
     → drain _input_queue: every pending question gets ""
     → every firm's gate takes the PF1 branch
     → ALL firms recorded as "user_aborted"
```

PF2, PF3. One escape blocks on the thing it is trying to stop; the other
kills everything and files it as the human's decision.

### Phase 1 human-checkpoint inventory

| Checkpoint | Fires when | Knows enough to ask a useful question? | State |
|---|---|---|---|
| Dispatcher dry-run gate | 2 consecutive zero-cell iterations | **No** — holds a count of null cells, can only ask "continue?" | trailing signal by construction; forgeable via 22a; destructive via PF1; hint invisible via PF9 |
| BP `ask_user` | BP's discretion | **Yes** — holds active cells, file inventory, `searched_files`, rejection reasons | prompt-biased against firing (PF8); free on the turn budget |
| BP `report_exhausted` → dispatcher exit | BP gives up | n/a — it is the give-up | **no ask at all** (PF6), may be a silent `_MAX_TURNS` timeout (PF7) |

That is the complete list. The only node that can ask a question a human
can answer is the one that almost never asks. Both outcomes that end a
firm's extraction — `user_aborted` and `bp_exhausted` — either never ask
the human or ask through a channel that cannot deliver the answer.

---

## Outcome imagination (tentative — user-stated, not yet designed)

Two outcomes, one per layer. The BP one is primary.

### Primary — BP asks for a handhold instead of giving up

User's words: *"BP actively asks user 'which files shd I search for XYZ'
for a handhold when in doubt (after 2 iterations?)."*

Target UX, roughly:

```
[PMS2-disp-COHR] iter 2: 3 null ans, 6 active
[PMS2-disp-COHR] I can't place Optical Revenue % for COHR FY24-FY26.
                 Tried: COHR_10K_FY24.md (no segment split),
                        COHR_AR2025.md (revenue total only),
                        0_Optical_Sector_2025.pdf (peer table, no COHR).
                 Which file should I search for it? Or is it not in
                 the corpus?
> the segment split is in the Q4 earnings deck, try COHR_Q4FY24_deck.md
[PMS2-disp-COHR] iter 3: routing Optical Revenue % → COHR_Q4FY24_deck.md
```

The shape that matters: BP names **which cells**, **what it already
tried**, and asks a question whose answer is a *file*, not a yes/no. The
human supplies corpus knowledge BP cannot have (it has never read a file
— rule 1 — it only sees names and sizes). That is the handhold.

Open, marked as undecided by the user's own "?":

- **Trigger.** "After 2 iterations" is a guess, not a decision. Candidate
  triggers, not yet chosen: iteration count; the rule-8 condition
  ("no new information to try") re-pointed at `ask_user` instead of
  `report_exhausted`; per-cell staleness (this cell has survived N
  iterations unfilled); or `report_exhausted` becoming a gated exit that
  must ask before it is honoured.
- **Mechanism.** Sysprompt-only (rewrite rules 2/8/9 so the give-up
  condition routes to `ask_user` first), or structural (dispatcher
  refuses to accept `bp_exhausted` until BP has asked at least once),
  or both. Not decided. Note the sysprompt-only route is cheap — PF8's
  bias is prompt-borne, and asking is free on BP's turn budget.
- **Blast radius of asking more.** BP's `ask_user` is free-text and
  inherits every 22a defect. A stale line here does not abort anything —
  it enters BP's LLM context as though the human said it, and nothing
  logs the misattribution. Asking more through a broken channel means
  more silent corruption, not less. This is the strongest argument for
  22a landing first.

### Secondary — the dispatcher gate stops accepting garbage

User's words: *"dispatch userinput gate only accepts y/n."*

Read literally: the gate rejects anything that is not an affirmative or a
negative and re-asks, rather than choosing a branch on the human's
behalf. `""` stops being a vote (kills PF1). Requires the answer format
to actually be visible (PF9).

Explicitly still open: whether `user_aborted` should be split so the
record stops asserting decisions that were not made.

### Third — a stop that keeps the evidence

Settled 20260731. Ctrl-C becomes the single stop button, in two stages:
first press = graceful (set a run-scoped abort flag, workers check it at
safe points and unwind through their existing `finally` blocks, traces
flush, "stopping…" printed), second press = hard exit. EOF from 22a
enters the same graceful path.

Honest about the latency: an in-flight HTTP call cannot be interrupted,
so with ~477 Lengs out the graceful stop is bounded by the slowest call
currently open. The UX should say so rather than imply instant.

Independent of cancellation, and worth doing regardless: flush the
Leng/Validator buffer incrementally rather than once at the end (PF11),
and stop traces from overwriting each other across iterations (PF12).
Both reduce what a hard kill costs and both are already losing data on
clean runs.

Not in the imagined outcome, per the user: capping the loop. Looping
while any cell fills is intended. Nor a second stop key — Ctrl-D is not
given its own meaning (see 22a, "Deliberately rejected").

---

## Dependency on 22a

Strict y/n parsing does **not** fix the failure instance. A stale `y`
from the kernel buffer is a valid `y`. Tightening the parse kills the
CIEN half (`""` → phantom abort) and leaves the COHR half — the actual
runaway — exactly as it is.

Sequence: 22a first, or this gate stays forgeable no matter how strictly
it parses.

The primary outcome makes the dependency sharper, not weaker. Making BP
ask more *increases* traffic through the broken channel. A misbound line
at a y/n gate produces a visible wrong branch; a misbound line at BP's
free-text `ask_user` produces a plausible wrong *file routing*, silently,
attributed to the human, and it is nowhere flagged as such. Building 22b
first would multiply 22a's blast radius.

---

## NOT YET WRITTEN

Solution space, graph diff, file-by-file change, failure modes of the
change, unit tests (U-series), LLM tests (L-series), execution plan.
Deliberately absent. Problem definition is the current deliverable.
