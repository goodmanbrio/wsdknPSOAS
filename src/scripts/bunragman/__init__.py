"""bunragman — source-scoped router sitting above OSHA.

Splits a query across per-source (firm/broker/dir/sector, whatever the
query calls for) retrieval paths, one Bunragman agent per source, so no
single shared retrieval budget lets one source drown out another.

Entry point: run_bunragman_sekei() in sekei.py. Control flow matches
Specs/wsnBunragMan.md's process diagram (SEKEI through RECONCILE).
"""

from src.scripts.bunragman.sekei import run_bunragman_sekei

__all__ = ["run_bunragman_sekei"]
