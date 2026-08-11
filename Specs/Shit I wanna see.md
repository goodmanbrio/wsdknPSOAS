- On PSOAS startup, if stale docstore/ingest, prompt to rerun chunking y/n
- Shouldnt have to expand periods 

Ideal UX
```
python src/psoas.py
-{PSOAS — Poony Sophomore Orchestrated Analyst Strapon}-
[PMS2] ⚠ files_ingested/ modified (2026-07-25 15:42) after docstore (2026-07-25 15:01). rerun 01_Chunk ?
> y
[PMS2] re-chunking ...
> hi LITE primary seg rev (laser ?) for fy24-26 + capex + gross margin
[ORCHESTRATOR] PMS2 query, fwding to sekei
[PMS2-Sekei] hi, u want this ? 
Firms: LITE (Lumentum)                                                                       
Metric: Gross Margin                                                                         
Periods: 3QFY2025 through 3QFY2026 (that's 3Q25, 4Q25, 1Q26, 2Q26, 3Q26 — five quarters)     
Granularity: quarterly
> y 
[PMS2-Sekei] running mapper...
[PMS2-Sekei] this the stencil u want ? Finalize stencil ?.....
> y
[PMS2-Sekei] Validating stencil...
c
[Stencil finalizer] error: formula doesn't use row names as terms: Gross Margin = Poo/Pee
[PMS2-Sekei] OOPS heehee retrying
[PMS2-Stencil finalizer] Stencil finalized: 5 rows × 3 periods = 15 cells. 
3 ans metrics: Laser 
Revenue, CAPEX, Gross Margin. Topo sort OK. Formulas rewritten to Rn notation. 
Saved to /Users/goodmanbrio/Desktop/wasinoClaude/_Imano/HVCPipeline/KTQuery/Omaya/PSOAS_Jul20/temp/sessions/20260727_215014/pms2/work_stencil.json.
``` 