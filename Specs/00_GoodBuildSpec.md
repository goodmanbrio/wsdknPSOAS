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
- Don't digress on diff or code bloat, graph change section input/output contracts shd be so well defined that it 

## Failure modes
- Points of failure, how they can butterfly, how they can break the system, 
- Introduced new nodes (deterministic = fragility, nondeterministic = chaos even more fragility)
- If X fails, 

## Unit tests
- For each deterministic component/cluster, 1) build a unit test 2) run the unit tests before any nondeterministic building (LLM)

## LLM unit tests
- For each LLM component, the API keys to auto-run tests should be already generated.
- For each LLM node, see if they generate & toolcall in the expected format