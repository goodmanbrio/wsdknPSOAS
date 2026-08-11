# stencil2chart.py — Input / Output Contract

Renders a display table as a line chart SVG. One call = one chart.
Unit-based splitting is done **upstream** before calling this function.

**Location:** `src/stencil2chart.py`

**Not integrated into PMS1 pipeline.** PMS1 (Sekei → PTO → Stencil) and
stencil2chart are separate tools inside a larger LLM agent harness. The
harness chains: PMS1 → display stencil trimming → stencil2chart. The
orchestrator does not call stencil2chart directly.

## Harness adaptation needed

stencil2chart currently uses `print()` / `input()` for gap prompts
(missing value handling). This works when the harness routes stdin/stdout
to the LLM, but has not been tested or adapted for any specific harness
framework. When the harness interface is defined, the gap prompt
mechanism may need to change (e.g. return a structured response instead
of blocking on `input()`).

---

## Function signature

```python
from src.stencil2chart import stencil2chart
from pathlib import Path

result: Path | None = stencil2chart(
    chart_input: dict,    # display table (see Input below)
    output_dir: Path,     # directory for SVG output (created if missing)
)
```

---

## Input: `chart_input` dict

```json
{
    "title": "Best Buy — Income Statement ($M)",
    "periods": ["FY2021", "FY2022", "FY2023"],
    "denomination_label": "(millions)",
    "series": [
        {"metric": "Revenue", "values": [51761, 46298, 43452]},
        {"metric": "Gross Profit", "values": [15005, 13610, 12800]},
        {"metric": "Net Income", "values": [2454, 1419, null]}
    ]
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | `str` | Yes | Chart title. Provided by upstream LLM. Rendered above the chart. |
| `periods` | `list[str]` | Yes | X-axis labels, left to right. Typically `["FY2021", "FY2022", "FY2023"]`. |
| `denomination_label` | `str` | No | Y-axis label. e.g. `"(millions)"`, `"(%)"`, `"(count)"`. Omit or `""` for no label. |
| `series` | `list[dict]` | Yes | One entry per line on the chart. See below. |

### Series entry

| Field | Type | Required | Description |
|---|---|---|---|
| `metric` | `str` | Yes | Legend label for this line. e.g. `"Revenue"`, `"Gross Margin"`. |
| `values` | `list[float \| null]` | Yes | One value per period, positionally aligned with `periods`. `null` = missing value (triggers gap prompt). |

### Constraints

- `len(values)` must equal `len(periods)` for every series entry.
- All series in one `chart_input` should share the same unit (USD, %, count, etc.). The upstream splitter is responsible for grouping by unit.
- `title` is always provided by the upstream LLM. No fallback/auto-generation.

---

## Output

### On success: `Path`

Returns the absolute path to the saved SVG file.

Filename format: `stencil2charted_YYYYMMDDHHMMSS_N.svg`
- Timestamp is dynamic (`datetime.now()`)
- `N` is a process-scoped counter to prevent collisions within the same second

Example: `output/stencil2charted_20260714170844_1.svg`

The output directory is created (`mkdir -p`) if it does not exist.

### On cancel/terminate: `None`

Returns `None` if the user chose option 2 (terminate for retry) or
option 3 (cancel) during the gap prompt. The caller should handle
`None` accordingly — retry with better input or stop.

---

## Gap handling (missing values)

If any series contains `null` values, stencil2chart prints a prompt
before rendering:

```
⚠ 1 missing value(s):
  - Net Income @ FY2023

1. Allow gap (line will break at missing points)
2. Terminate (caller retries with better input)
3. Cancel
Choice [1/2/3]:
```

| Choice | Behavior | Return |
|---|---|---|
| 1 | Render chart with broken line at missing points | `Path` |
| 2 | Abort — caller expected to fix input and re-call | `None` |
| 3 | Abort — stop entirely | `None` |

---

## Styling

Uses [LovelyPlots](https://github.com/killiansheriff/LovelyPlots)
matplotlib styles:

```python
["ipynb", "colors10-markers", "svg_no_fonttype"]
```

- `ipynb` — clean base theme (figure size, axes)
- `colors10-markers` — 10-color palette with distinct markers (dots) per line
- `svg_no_fonttype` — strips font info from SVG so it inherits the host document's font (ideal for docx/pptx insertion)

Style customization via config is commented out — future work.

---

## Config

`Config.output_dir` (in `src/config.py`) defaults to `./output/`.
This is the default target for SVG output. The caller can pass any
`Path` as `output_dir` to override.

---

## Example usage

```python
from pathlib import Path
from src.stencil2chart import stencil2chart

chart = {
    "title": "Best Buy vs Amcor — Gross Margin",
    "periods": ["FY2021", "FY2022", "FY2023"],
    "denomination_label": "(%)",
    "series": [
        {"metric": "Best Buy", "values": [23.1, 22.49, 21.41]},
        {"metric": "Amcor", "values": [19.8, 19.39, 18.54]},
    ],
}

path = stencil2chart(chart, output_dir=Path("./output"))
if path:
    print(f"Chart saved: {path}")
else:
    print("Cancelled or terminated.")
```
