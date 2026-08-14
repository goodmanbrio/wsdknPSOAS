---
Created: 2026-08-14
Updated: 2026-08-14
Last checked: 2026-08-14
---

### Process diagram — working components only

Skeleton view of the Bunragman pipeline: orchestrator, sekei, the per-source
Bunragman fan-out, and the two tools each Bunragman agent calls. No decision
diamonds, resource nodes, or gap annotations — see `wsnBunragMan.md` for the
full build-status diagram those belong to. Three Bunragman boxes are drawn to
show the fan-out shape; the real count is set by how many sources sekei
resolves at runtime, same as the full diagram.

Legend: rectangle = process, oval = start/end, orange parallelogram =
blackboxed tool (identity and I/O contract drawn, internals not).

```mermaid
%%{init: {'flowchart': {'useMaxWidth': false, 'htmlLabels': true}}}%%
flowchart TD
    classDef default text-align:left
    classDef tool fill:#ffab40,stroke:#e65100,color:#111,text-align:left

    START(["`user query`"]) --> ZAKO

    subgraph ORCHESTRATOR["`orchestrator (process) — wraps the whole pipeline`"]
        subgraph SEKEI["`Bunragman sekei (process) — dispatcher + reconciler, one component`"]
            ZAKO[/"`Zako discovery tool — returns file list per source`"/]
            ZAKO --> FANOUT["`sekei fans out one Bunragman agent per source — fired in parallel`"]

            FANOUT --> BM1 & BM2 & BM3

            BM1["`Bunragman agent — source 1`"]
            BM1 --> OSHA1[/"`OSHA`"/]
            BM1 --> BUNNAV1[/"`BunNavHarness`"/]
            OSHA1 --> SUM1["`writes source summary`"]
            BUNNAV1 --> SUM1

            BM2["`Bunragman agent — source 2`"]
            BM2 --> OSHA2[/"`OSHA`"/]
            BM2 --> BUNNAV2[/"`BunNavHarness`"/]
            OSHA2 --> SUM2["`writes source summary`"]
            BUNNAV2 --> SUM2

            BM3["`Bunragman agent — source 3`"]
            BM3 --> OSHA3[/"`OSHA`"/]
            BM3 --> BUNNAV3[/"`BunNavHarness`"/]
            OSHA3 --> SUM3["`writes source summary`"]
            BUNNAV3 --> SUM3

            SUM1 & SUM2 & SUM3 --> RECONCILE
            RECONCILE["`sekei (same component, reconciler phase) — reads all summaries, writes final cross-source answer`"]
        end
    end

    RECONCILE --> ANSWER(["`final cross-source answer`"])

    class ZAKO,OSHA1,OSHA2,OSHA3,BUNNAV1,BUNNAV2,BUNNAV3 tool
```
