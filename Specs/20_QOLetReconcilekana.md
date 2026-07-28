# Spec 20: QOLet Reconcile かな

Index spec. Four problems, two sub-specs.

| Sub-spec | Problems | Scope |
|----------|----------|-------|
| `20a_QOL.md` | Phase 1 terminal visibility (BP plan table, Leng/Validator counters) | UX only, no extraction logic |
| `20b_ReconcileKana.md` | FiscalCalResolver validation, Leng sysprompt quality, divergence reconciliation | Extraction quality + data integrity |

Dependency: 20b problems are causally chained (upstream → downstream).
Bad FiscalCal → bad Leng → bad reconciliation.
20a is orthogonal to 20b.
