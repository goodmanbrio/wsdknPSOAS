"""
stencil2chart.py — Render a display table as a line chart (SVG).

Takes a plain dict (from upstream LLM / stencil2displaystencils.py)
and produces one SVG file per call. One chart per call — unit-based
splitting is done upstream before this is called.

Input dict shape:
    {
        "title": "Store Count Comparison (FY2021-2023)",
        "periods": ["FY2021", "FY2022", "FY2023"],
        "denomination_label": "(millions)",
        "series": [
            {"metric": "Revenue", "values": [51761, 46298, 43452]},
            {"metric": "Gross Profit", "values": [15005, 13610, None]},
        ]
    }

Usage:
    from src.stencil2chart import stencil2chart
    path = stencil2chart(chart_input, output_dir=Path("./output"))
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import lovelyplots  # noqa: F401 — registers styles on import
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


_STYLES = ["ipynb", "colors10-markers", "svg_no_fonttype"]
# Future: pull style list from Config if customization needed.

# Counter for filename uniqueness within a single process/second.
_call_counter = 0


def _check_gaps(chart_input: dict) -> list[str]:
    """Scan series for None or NaN values. Return list of gap descriptions."""
    gaps = []
    for s in chart_input.get("series", []):
        periods = chart_input.get("periods", [])
        for i, v in enumerate(s.get("values", [])):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                period = periods[i] if i < len(periods) else f"index {i}"
                gaps.append(f"{s['metric']} @ {period}")
    return gaps


def _prompt_gaps(gaps: list[str]) -> int:
    """Print gap report and prompt user for action. Returns 1, 2, or 3."""
    print(f"\n⚠ {len(gaps)} missing value(s):")
    for g in gaps:
        print(f"  - {g}")
    print("\n1. Allow gap (line will break at missing points)")
    print("2. Terminate (caller retries with better input)")
    print("3. Cancel")

    while True:
        choice = input("Choice [1/2/3]: ").strip()
        if choice in ("1", "2", "3"):
            return int(choice)
        print("Enter 1, 2, or 3.")


def stencil2chart(
    chart_input: dict,
    output_dir: Path,
) -> Path | None:
    """Render chart_input as a line chart SVG.

    Args:
        chart_input: Display table dict with title, periods,
            denomination_label, and series.
        output_dir: Directory to save the SVG. Created if missing.

    Returns:
        Path to saved SVG, or None if user chose terminate (2)
        or cancel (3) on gap prompt.
    """
    # ── Gap check ──────────────────────────────────────────────
    gaps = _check_gaps(chart_input)
    if gaps:
        choice = _prompt_gaps(gaps)
        if choice == 2:
            return None
        if choice == 3:
            return None

    # ── Setup ──────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)

    periods = chart_input["periods"]
    title = chart_input["title"]
    denom_label = chart_input.get("denomination_label", "")

    # ── Plot ───────────────────────────────────────────────────
    with plt.style.context(_STYLES):
        fig, ax = plt.subplots()

        for s in chart_input["series"]:
            ax.plot(periods, s["values"], label=s["metric"])

        ax.set_title(title)
        if denom_label:
            ax.set_ylabel(denom_label)
        ax.legend()

        # Disable scientific notation — ipynb style enables it by
        # default which mangles percentage/count axes (shows "1e1").
        ax.yaxis.set_major_formatter(ticker.ScalarFormatter(useOffset=False))
        ax.ticklabel_format(style="plain", axis="y")

        # ── Save ───────────────────────────────────────────────
        global _call_counter
        _call_counter += 1
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"stencil2charted_{ts}_{_call_counter}.svg"
        path = output_dir / filename
        fig.savefig(path)
        plt.close(fig)

    return path
