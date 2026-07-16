#!/usr/bin/env python3
"""Quick smoke test: stencil2chart with d_bby ground truth data."""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.stencil2chart import stencil2chart

output_dir = _project_root / "output"

# Chart 1: USD metrics (Revenue, Gross Profit, Net Income)
usd_chart = {
    "title": "Best Buy — Income Statement ($M)",
    "periods": ["FY2022", "FY2023"],
    "denomination_label": "(millions)",
    "series": [
        {"metric": "Revenue", "values": [51761, 46298]},
        {"metric": "Gross Profit", "values": [11640, 9912]},
        {"metric": "Net Income", "values": [2454, 1419]},
    ],
}

# Chart 2: % metrics (Gross Margin, Net Profit Margin)
pct_chart = {
    "title": "Best Buy — Margins",
    "periods": ["FY2022", "FY2023"],
    "denomination_label": "(%)",
    "series": [
        {"metric": "Gross Margin", "values": [22.49, 21.41]},
        {"metric": "Net Profit Margin", "values": [4.74, 3.06]},
    ],
}

for chart in [usd_chart, pct_chart]:
    result = stencil2chart(chart, output_dir)
    if result:
        print(f"Saved: {result}")
    else:
        print("Cancelled or terminated.")
