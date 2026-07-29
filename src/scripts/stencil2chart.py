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
import re
import threading
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe from worker threads
import lovelyplots  # noqa: F401,E402 — registers styles on import
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as ticker  # noqa: E402

from src.harness.terminal_router import register, ToolChannel


_STYLES = ["ipynb", "colors10-markers", "svg_no_fonttype"]
_LABEL_FONTSIZE = "small"

# Counter for filename uniqueness within a single process/second.
_call_counter = 0
_counter_lock = threading.Lock()

_default_channel = register("S2C")


def _sanitize(text: str) -> str:
    """Collapse to filesystem-safe slug: non-word chars → _, runs collapsed, edges stripped."""
    return re.sub(r"_+", "_", re.sub(r"[^\w\-]", "_", text)).strip("_")


def _check_gaps(chart_input: dict) -> list[str]:
    """Scan series for None values. Return list of gap descriptions."""
    gaps = []
    for s in chart_input.get("series", []):
        periods = chart_input.get("periods", [])
        for i, v in enumerate(s.get("values", [])):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                period = periods[i] if i < len(periods) else f"index {i}"
                gaps.append(f"{s['metric']} @ {period}")
    return gaps


def _nudge_labels(y_positions: list[float], min_gap: float) -> list[float]:
    """Adjust y positions so no two direct-line labels overlap.

    Greedy bottom-up: sorts by y, walks upward, pushes any label
    that's too close to its neighbor. Works for 2-8 series.
    """
    if len(y_positions) <= 1:
        return list(y_positions)

    # (original_index, y_value) sorted by y ascending
    indexed = sorted(enumerate(y_positions), key=lambda x: x[1])
    adjusted = [0.0] * len(y_positions)

    adjusted[indexed[0][0]] = indexed[0][1]
    prev_y = indexed[0][1]

    for orig_idx, y in indexed[1:]:
        if y - prev_y < min_gap:
            y = prev_y + min_gap
        adjusted[orig_idx] = y
        prev_y = y

    return adjusted


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
        plt.rcParams.update({"font.family": "Aptos"})
        fig, ax = plt.subplots(figsize=(max(6.4, len(periods) * 1.8), 4.8))

        # Plot lines and collect endpoints for direct labels
        endpoints = []  # (y_value, label_text, line_color)
        for s in chart_input["series"]:
            line, = ax.plot(periods, s["values"], label=s["metric"])
            # Find last non-None value for label placement
            last_y = None
            for v in reversed(s["values"]):
                if v is not None:
                    last_y = v
                    break
            if last_y is not None:
                endpoints.append(
                    (last_y, s["metric"], line.get_color())
                )

        ax.set_title(title)
        if denom_label:
            ax.set_ylabel(denom_label)

        # Direct line labels instead of legend box
        if endpoints:
            font_pts = matplotlib.font_manager.FontProperties(
                size=_LABEL_FONTSIZE,
            ).get_size_in_points()
            y_lo, y_hi = ax.get_ylim()
            ax_h_in = fig.get_figheight() * ax.get_position().height
            data_per_pt = (y_hi - y_lo) / (ax_h_in * 72)
            min_gap = font_pts * data_per_pt * 1.1
            y_vals = [ep[0] for ep in endpoints]
            nudged = _nudge_labels(y_vals, min_gap)

            x_pos = len(periods) - 1
            for i, (_, label, color) in enumerate(endpoints):
                ax.text(
                    x_pos + 0.15, nudged[i], f"  {label}",
                    va="center", fontsize=_LABEL_FONTSIZE, color=color,
                    clip_on=False,
                )

        # Disable scientific notation — ipynb style enables it by
        # default which mangles percentage/count axes (shows "1e1").
        ax.yaxis.set_major_formatter(
            ticker.ScalarFormatter(useOffset=False)
        )
        ax.ticklabel_format(style="plain", axis="y")

        # ── Save ───────────────────────────────────────────────
        global _call_counter
        with _counter_lock:
            _call_counter += 1
            filename = f"chart_{_call_counter}_{_sanitize(title)}.svg"
        path = output_dir / filename
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)

    return path
