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

import threading
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe from worker threads
import lovelyplots  # noqa: F401,E402 — registers styles on import
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as ticker  # noqa: E402

from src.harness.terminal_router import register, ToolChannel


_STYLES = ["ipynb", "colors10-markers", "svg_no_fonttype"]

# Counter for filename uniqueness within a single process/second.
_call_counter = 0
_counter_lock = threading.Lock()

_default_channel = register("S2C")


def _check_gaps(chart_input: dict) -> list[str]:
    """Scan series for None values. Return list of gap descriptions."""
    gaps = []
    for s in chart_input.get("series", []):
        periods = chart_input.get("periods", [])
        for i, v in enumerate(s.get("values", [])):
            if v is None:
                period = periods[i] if i < len(periods) else f"index {i}"
                gaps.append(f"{s['metric']} @ {period}")
    return gaps


def _prompt_gaps(gaps: list[str], channel: ToolChannel) -> int:
    """Print gap report and prompt user for action. Returns 1, 2, or 3."""
    channel.print(f"⚠ {len(gaps)} missing value(s):")
    for g in gaps:
        channel.print(f"  - {g}")
    channel.print("1. Allow gap (line will break at missing points)")
    channel.print("2. Terminate (caller retries with better input)")
    channel.print("3. Cancel")

    while True:
        choice = channel.input("Choice [1/2/3]: ").strip()
        if choice in ("1", "2", "3"):
            return int(choice)
        channel.print("Enter 1, 2, or 3.")


def stencil2chart(
    chart_input: dict,
    output_dir: Path,
    channel: ToolChannel | None = None,
) -> Path | None:
    """Render chart_input as a line chart SVG.

    Args:
        chart_input: Display table dict with title, periods,
            denomination_label, and series.
        output_dir: Directory to save the SVG. Created if missing.
        channel: ToolChannel for user interaction. Falls back to
            module-level default if None.

    Returns:
        Path to saved SVG, or None if user chose terminate (2)
        or cancel (3) on gap prompt.
    """
    ch = channel or _default_channel

    # ── Gap check ──────────────────────────────────────────────
    gaps = _check_gaps(chart_input)
    if gaps:
        choice = _prompt_gaps(gaps, ch)
        if choice in (2, 3):
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
        with _counter_lock:
            _call_counter += 1
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"stencil2charted_{ts}_{_call_counter}.svg"
        path = output_dir / filename
        fig.savefig(path)
        plt.close(fig)

    return path
