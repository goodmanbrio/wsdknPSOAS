## Frontmatter
- Write date: YYYYMMDD
- Update date: 
- Codebase last changed date: YYYYMMDD
- Implemented Y/N
- 3 sentence summary

## Problem space
- Problem definition
- Failure instance
- Points of failure

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
- Design diff is just as important as line by line diff
- (mermaid graph of current pipeline/loop as nodes & edges & clear highlight of input / output contracts)
- (mermaid graph of proposed chg in pipeline/loop as nodes & edges & clear highlight of input / output contracts)

## Hence File by file Change
- First is modelling the file's constituent functions as a graph
- Per file shd have its own current/proposed graph mermaid + input/output contracts within it, shd be thorough per function or wtv so that spec checks don't waste tokens checking if filechanges need revision
- This ought to be done disciplinedly so don't need to digress on diff or code bloat

## Failure modes
- Points of failure, how they can butterfly, how they can break the system, 
- Introduced new nodes (deterministic = fragility, nondeterministic = chaos even more fragility)
- If X fails, 

## Unit tests
- For each deterministic component/cluster, 1) build a unit test 2) run the unit tests before any nondeterministic building (LLM)
- Define each unit test (eg U0, U1, U13), built yet Y/N ran yet Y/N
- WRITE THE TEST FILES SEPARATELY FROM CODEBASE DONT POLLUTE CODE FILES

## LLM unit tests
- For each LLM component, the API keys to auto-run tests should be already generated.
- For each LLM node, see if they generate & toolcall in the expected format
- Define each unit test (eg L0, L1, L13), built yet Y/N ran yet Y/N
- Don't try to blindly re-run existing E2E tests without checking whether they are current with the current graph, might be obsolete
- WRITE THE TEST FILES SEPARATELY FROM CODEBASE DONT POLLUTE CODE FILES

## Execution
- Spec size (char count) = too many tokens for a single session to do thoroughly? Respect the magnitude of it if untenable for oneshot
- If so, the easiest is to complete unit tests sequentially & spawn fresh sessions with the guarantee that previous work is alr done 
- As execution proceeds, design decisions inevitably have to be logged and reconciled with the spec (master) so that fresh agents are operating on a non-stale state, this can be done by being diligent with updating graphs, input/output contracts, file change bullets
- For each spec, can have the executing session create a temp file eg /Specs/19_S1Discretion.md /Specs/19_S2Discretion.md to log that session's design decisions 
- For execution session instruction: ``` oneshot slowly you monkey. At any point of design/execution ambiguity, think in cycles (Forensics, hypothesize, verify, doubt, failure mode, hypothesize, repeat) to solve responsibly, then note in <designated discretion md> idc abt  "how I did it" only "what I fakking discretionarily decided about the design." understand ? ```