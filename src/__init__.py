# PSOAS src package.
# Extend __path__ so modules are importable as src.xxx:
#   src/scripts/          → config.py, llm.py, stencil2chart.py (PSOAS-modified copies)
#   src/scripts/PMS1/src/ → sekei.py, orchestrator.py, pto.py, stencil.py, etc. (PMS1 originals)
# Priority: scripts/ searched first, so modified copies shadow originals.
from pathlib import Path as _Path

_base = _Path(__file__).parent
__path__.append(str(_base / "scripts"))
__path__.append(str(_base / "scripts" / "Poony_Multiretrieval_S1" / "src"))
