#!/usr/bin/env python3
"""Quick test: run PTECA directly with pre-loaded stencils."""

import sys
import json
from pathlib import Path

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.harness.terminal_router import register
from src.tools.tool_pteca import run_pteca

assets = Path("temp/sessions/20260716155146/assets")
with open(assets / "var_1_Best_Buy_stencil.json") as f:
    bby = json.load(f)
with open(assets / "var_2_Boeing_stencil.json") as f:
    ba = json.load(f)

ch = register("PTECA-Best Buy+Boeing")
chart_inputs = run_pteca(
    [bby, ba],
    "Compare gross and net margins FY2020-FY2022",
    channel=ch,
)

print(f"\n--- PTECA returned {len(chart_inputs)} chart(s) ---")
for i, ci in enumerate(chart_inputs):
    print(f"\nChart {i+1}: {ci['title']}")
    print(f"  Periods: {ci['periods']}")
    print(f"  Series: {[s['metric'] for s in ci['series']]}")
