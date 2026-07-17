#!/usr/bin/env python3
"""Test stencil2chart against var_3 and var_5 chart-input JSONs (Best Buy vs Boeing)."""

import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.scripts.stencil2chart import stencil2chart

# ── Paths ─────────────────────────────────────────────────────────────
SESSION_DIR = _root / "temp/sessions/20260716152738/assets"
CHART_INPUT_PATHS = [
    SESSION_DIR / "var_3_Best_Buy_Boeing_chart_inputs.json",
    SESSION_DIR / "var_5_Best_Buy_Boeing_chart_inputs.json",
]
OUTPUT_DIR = _root / "temp/sessions/20260716152738/charts"

# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for path in CHART_INPUT_PATHS:
        with open(path) as f:
            chart_inputs = json.load(f)
        print(f"\n── {path.name} ({len(chart_inputs)} chart(s)) ──")
        for chart_input in chart_inputs:
            result = stencil2chart(chart_input, OUTPUT_DIR)
            if result:
                print(f"  Saved: {result}")
            else:
                print("  Cancelled or terminated.")
