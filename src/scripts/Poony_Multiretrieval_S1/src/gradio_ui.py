"""
gradio_ui.py — S1 Pipeline Visualizer for Poony Multiretrieval S1.

Visualizes the full E2E flow:
    Sekei (live LLM) → parallel PTO Retrieval → Judge Extraction
    → Stencil Eval (in-place fill) → Source Browser

Layout:
    Top bar:    Query | Sector (cosmetic) | Firm(s) | Run → spinner
    Upper:      Stencil grid (updates in place: empty → filled)
    Lower:      Phase-dependent — batch cards → source browser

Usage (via app.py):
    python app.py
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.config import Config
from src.sekei import (
    SekeiPlan, validate_plan, sekei,
)
from src.stencil import CellResult
from src.pto import (
    PTOBatchRequest, pto_retrieve, pto_judge, pto_judge_to_stencil,
)


_RED = "#9c3730"


# ═════════════════════════════════════════════════════════════════════════
# HTML rendering helpers
# ═════════════════════════════════════════════════════════════════════════

_SPINNER_CSS = """<style>
@keyframes spin { to { transform: rotate(360deg); } }
.pms1-spinner {
    display: inline-block; width: 12px; height: 12px;
    border: 2px solid #444; border-top-color: #9c3730;
    border-radius: 50%; animation: spin 0.6s linear infinite;
}
</style>"""

_STATUS_SPINNER = (
    f'{_SPINNER_CSS}'
    '<div style="display:flex;align-items:center;gap:8px;'
    'color:#9c3730;font-family:monospace;font-size:12px">'
    '<span class="pms1-spinner"></span><span>Running...</span></div>'
)


def _render_stencil(plan: SekeiPlan, values: dict[str, CellResult] | None = None) -> str:
    """Stencil as HTML table. Shows cell IDs when empty, values when filled."""
    if values is None:
        values = {}

    # row_num → {metric, type, formula, cols: {col_idx: cell_id}}
    rows: dict[int, dict] = {}
    for cid, cell in plan.cells.items():
        m = re.match(r"([A-Z])(\d+)", cid)
        if not m:
            continue
        col, row = ord(m.group(1)) - ord("A"), int(m.group(2))
        if row not in rows:
            rows[row] = {"metric": cell.metric, "type": cell.type,
                         "formula": cell.formula, "cols": {}}
        rows[row]["cols"][col] = cid

    h = ['<table style="border-collapse:collapse;font-family:monospace;'
         'font-size:12px;margin:0 auto">']
    # header
    h.append("<tr><th style='border:1px solid #444;padding:6px 10px;"
             "background:#1a1a2e;color:#dcdcaa;text-align:left'></th>")
    for p in plan.periods:
        h.append(f"<th style='border:1px solid #444;padding:6px 10px;"
                 f"background:#1a1a2e;color:#dcdcaa;text-align:right'>"
                 f"{html_mod.escape(p)}</th>")
    h.append("</tr>")

    for rn in sorted(rows):
        rd = rows[rn]
        is_c = rd["type"] == "compute"
        label = rd["metric"] + (" [C]" if is_c else "")
        h.append(f"<tr><td style='border:1px solid #444;padding:6px 10px;"
                 f"background:#1a1a2e;color:#c0c0c0;font-weight:bold;"
                 f"text-align:left'>{html_mod.escape(label)}</td>")

        for ci in range(len(plan.periods)):
            cid = rd["cols"].get(ci, "")
            if cid and cid in values:
                v = values[cid].value
                if is_c:
                    txt = f"{v:.2%}"
                elif abs(v) >= 1:
                    txt = f"{v:,.0f}"
                else:
                    txt = f"{v:.4f}"
                color = "#4ec9b0" if is_c else "#569cd6"
                h.append(f"<td style='border:1px solid #444;padding:6px 10px;"
                         f"text-align:right;color:{color};font-weight:bold'>"
                         f"{txt}</td>")
            elif cid:
                color = "#555"
                if is_c and rd["formula"]:
                    txt = f"{cid}={rd['formula']}"
                else:
                    txt = cid
                h.append(f"<td style='border:1px solid #444;padding:6px 10px;"
                         f"text-align:right;color:{color};font-style:"
                         f"{'italic' if is_c else 'normal'}'>"
                         f"{html_mod.escape(txt)}</td>")
            else:
                h.append("<td style='border:1px solid #444;padding:6px 10px;"
                         "text-align:right;color:#444'>&mdash;</td>")
        h.append("</tr>")

    h.append("</table>")
    return "".join(h)


def _render_batch_cards(plan: SekeiPlan, batch_states: dict) -> str:
    """Horizontal flex row of batch cards. Content depends on batch phase."""
    cards = []
    for batch in plan.batches:
        bid = batch.id
        bs = batch_states.get(bid, {})
        phase = bs.get("phase", "pending")
        metrics = [plan.cells[c].metric for c in batch.cells]

        hdr = (f"{bid} | {batch.period}\n"
               f"{batch.statement}\n"
               f"Metrics: {', '.join(metrics)}")

        if phase == "pending":
            status = '<span class="pms1-spinner"></span> Queued'
            body = ""
        elif phase == "retrieving":
            status = '<span class="pms1-spinner"></span> PTO Retrieve'
            body = ""
        elif phase == "retrieved":
            result = bs.get("result")
            status = '<span class="pms1-spinner"></span> Judging...'
            lines = []
            if result:
                ml = result.method_log
                lines.append(f'<span style="color:#569cd6">HyDE good: '
                             f'{html_mod.escape(ml.hyde_good[:80])}</span>')
                lines.append(f'<span style="color:#569cd6">HyDE bad: '
                             f'{html_mod.escape(ml.hyde_bad[:80])}</span>')
                lines.append(f"top={len(result.top_chunks)} "
                             f"runners={len(result.runner_ups)}")
                lines.append("")
                for i, node in enumerate(result.top_chunks[:3]):
                    meta = node.node.metadata
                    nid = node.node.node_id
                    g = result.good_scores.get(nid, 0.0)
                    b = result.bad_scores.get(nid, 0.0)
                    lines.append(f'<span style="color:#569cd6">'
                                 f'#{i} g={g:.3f} b={b:.3f} f={node.score:.3f}'
                                 f'</span>')
                    lines.append(f"  {html_mod.escape(meta.get('file_name', '?'))}")
                    sec = meta.get("section", "?")[:50]
                    lines.append(f"  {html_mod.escape(sec)}")
                    txt = node.node.text[:200].replace("\n", " ")
                    lines.append(f"  {html_mod.escape(txt)}...")
                    lines.append("")
            body = "\n".join(lines)
        elif phase == "judged":
            status = '<span style="color:#4ec9b0">Done</span>'
            jo = bs.get("judge_output", {})
            lines = []
            for metric, entry in jo.items():
                if entry.get("sufficient"):
                    val = entry.get("answer", "?")
                    den = entry.get("denomination", "")
                    unit = entry.get("unit", "")
                    lines.append(f'<span style="color:#4ec9b0">'
                                 f'{html_mod.escape(str(metric))}: {val} '
                                 f'{den} {unit}</span>')
                else:
                    lines.append(f'<span style="color:{_RED}">'
                                 f'{html_mod.escape(str(metric))}: '
                                 f'INSUFFICIENT</span>')
            body = "\n".join(lines)
        elif phase == "error":
            status = f'<span style="color:{_RED}">Error</span>'
            body = (f'<span style="color:{_RED}">'
                    f'{html_mod.escape(bs.get("error", "?"))}</span>')
        else:
            status, body = "?", ""

        card = (
            f'<div style="flex:1;min-width:250px;background:#1a1a2e;'
            f'border:1px solid #333;border-radius:6px;padding:10px;'
            f'font-family:monospace;font-size:11px;color:#c0c0c0;'
            f'white-space:pre-wrap;line-height:1.4;max-height:400px;'
            f'overflow-y:auto">'
            f'<div style="color:#dcdcaa;font-weight:bold;border-bottom:'
            f'1px solid #444;padding-bottom:4px;margin-bottom:6px">'
            f'{html_mod.escape(hdr)}</div>'
            f'<div style="margin-bottom:6px">{status}</div>'
            f'{body}</div>'
        )
        cards.append(card)

    return (_SPINNER_CSS
            + '<div style="display:flex;gap:8px;overflow-x:auto">'
            + "".join(cards) + '</div>')


def _try_eval_compute(plan: SekeiPlan, values: dict[str, CellResult]) -> None:
    """Eagerly eval compute cells whose deps are all in values. Mutates values."""
    for cid, cell in plan.cells.items():
        if cell.type != "compute" or cid in values:
            continue
        refs = re.findall(r"[A-Z]\d+", cell.formula)
        if not all(r in values for r in refs):
            continue
        expr = cell.formula
        for ref in sorted(values, key=len, reverse=True):
            expr = expr.replace(ref, str(values[ref].value))
        try:
            values[cid] = CellResult(value=eval(expr), source=cell.formula)  # noqa: S307
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════
# Source browser
# ═════════════════════════════════════════════════════════════════════════

def _render_source(cell_id: str, plan_cells: dict, values: dict, index) -> str:
    """Render source panel for a selected cell."""
    if not cell_id or cell_id not in plan_cells:
        return ""
    ci = plan_cells[cell_id]
    vi = values.get(cell_id)

    parts = ['<div style="background:#1a1a2e;border:1px solid #333;'
             'border-radius:6px;padding:12px;font-family:monospace;'
             'font-size:11px;color:#c0c0c0;white-space:pre-wrap;'
             'line-height:1.4;max-height:400px;overflow-y:auto">']

    parts.append(f'<span style="color:#dcdcaa;font-weight:bold">'
                 f'{html_mod.escape(cell_id)}: '
                 f'{html_mod.escape(ci["metric"])} '
                 f'({html_mod.escape(ci["period"])})</span>\n')

    if vi:
        v = vi["value"]
        if ci["type"] == "compute":
            disp = f"{v:.2%}" if abs(v) < 1 else f"{v:,.2f}"
        elif abs(v) >= 1:
            disp = f"{v:,.0f}"
        else:
            disp = f"{v:.4f}"
        parts.append(f'<span style="color:#4ec9b0">Value: {disp}</span>')
        if vi.get("denomination"):
            parts.append(f'Denomination: {html_mod.escape(vi["denomination"])}')
        if vi.get("unit"):
            parts.append(f'Unit: {html_mod.escape(vi["unit"])}')
        parts.append("")

    if ci["type"] == "compute":
        parts.append(f'<span style="color:#ce9178">Formula: '
                     f'{html_mod.escape(ci["formula"])}</span>')
        if vi:
            for ref in re.findall(r"[A-Z]\d+", ci["formula"]):
                rc = plan_cells.get(ref, {})
                rv = values.get(ref)
                if rv:
                    parts.append(f'  {ref} ({rc.get("metric", "?")}) = '
                                 f'{rv["value"]:,.2f}')
    elif ci["type"] == "retrieve" and vi:
        node_id = vi["source"]
        parts.append(f'Source node: {html_mod.escape(node_id[:50])}')
        parts.append("")
        try:
            text = index.docstore.docs[node_id].text
            parts.append(f'<span style="color:#9cdcfe">'
                         f'{html_mod.escape(text)}</span>')
        except (KeyError, AttributeError):
            parts.append('<span style="color:#666">[Node not in index]</span>')

    parts.append("</div>")
    return "\n".join(parts)


# ═════════════════════════════════════════════════════════════════════════
# Main streaming pipeline
# ═════════════════════════════════════════════════════════════════════════

def _stream_s1(query, sector, firm, index, config):
    """S1 pipeline generator.

    Yields:
        (stencil_html, pipeline_html, cell_radio_update,
         source_html, state_dict)
    """
    import gradio as gr

    empty = gr.update(choices=[], value=None)
    placeholder = ('<div style="color:#666;font-family:monospace;'
                   'font-size:12px;padding:20px;text-align:center">'
                   'Stencil will appear here after running a query.</div>')

    if not query.strip():
        yield (placeholder, "<p>Enter a query.</p>", empty, "", {})
        return

    full_query = query.strip()
    if firm.strip():
        full_query += f"\nCompany: {firm.strip()}"

    # ── Sekei planning ──────────────────────────────────────────────
    yield (
        '<div style="color:#666;font-family:monospace;font-size:12px;'
        'padding:20px;text-align:center">Running Sekei planner...</div>',
        "", empty, "", {},
    )

    try:
        plan = sekei(full_query, config)
    except Exception as exc:
        yield (
            f'<div style="color:{_RED};font-family:monospace;padding:12px">'
            f'Sekei failed: {html_mod.escape(str(exc))}</div>',
            "", empty, "", {},
        )
        return

    validate_plan(plan)

    # ── Empty stencil ───────────────────────────────────────────────
    stencil = _render_stencil(plan)
    yield (stencil, "", empty, "", {})

    # ── Parallel PTO batches ────────────────────────────────────────
    all_values: dict[str, CellResult] = {}
    bs: dict[str, dict] = {}

    for batch in plan.batches:
        bs[batch.id] = {"phase": "pending"}

    yield (stencil, _render_batch_cards(plan, bs), empty, "", {})

    with ThreadPoolExecutor(max_workers=max(len(plan.batches), 2)) as pool:
        for batch in plan.batches:
            metrics = [plan.cells[c].metric for c in batch.cells]
            req = PTOBatchRequest(
                batch_id=batch.id, firm=plan.firm,
                period=batch.period, statement=batch.statement,
                metrics=metrics, retrieve_target=batch.retrieve_target,
            )
            cell_map = {c: plan.cells[c].metric for c in batch.cells}
            bs[batch.id]["request"] = req
            bs[batch.id]["cell_map"] = cell_map
            bs[batch.id]["retrieve_future"] = pool.submit(
                pto_retrieve, req, index, config,
            )
            bs[batch.id]["phase"] = "retrieving"

        yield (stencil, _render_batch_cards(plan, bs), empty, "", {})

        done = False
        while not done:
            for bid, st in bs.items():
                if (st["phase"] == "retrieving"
                        and st["retrieve_future"].done()):
                    try:
                        result = st["retrieve_future"].result()
                        st["result"] = result
                        st["phase"] = "retrieved"
                        st["judge_future"] = pool.submit(
                            pto_judge, st["request"], result, config,
                        )
                    except Exception as exc:
                        st["phase"] = "error"
                        st["error"] = str(exc)

                if (st["phase"] == "retrieved"
                        and "judge_future" in st
                        and st["judge_future"].done()):
                    try:
                        jo = st["judge_future"].result()
                        st["judge_output"] = jo
                        st["phase"] = "judged"
                        bv = pto_judge_to_stencil(
                            jo, st["cell_map"], st["result"],
                        )
                        for cid, pcr in bv.items():
                            all_values[cid] = CellResult(
                                value=pcr.value, source=pcr.source,
                                denomination=pcr.denomination,
                                unit=pcr.unit,
                            )
                        _try_eval_compute(plan, all_values)
                    except ValueError as exc:
                        st["phase"] = "judged"
                        st["judge_output"] = {}
                        st["error"] = str(exc)
                    except Exception as exc:
                        st["phase"] = "error"
                        st["error"] = str(exc)

            done = all(s["phase"] in ("judged", "error") for s in bs.values())
            stencil = _render_stencil(plan, all_values)
            yield (stencil, _render_batch_cards(plan, bs), empty, "", {})
            if not done:
                time.sleep(0.15)

    # ── Source browser ──────────────────────────────────────────────
    plan_cells = {
        cid: {"metric": c.metric, "period": c.period,
              "type": c.type, "formula": c.formula}
        for cid, c in plan.cells.items()
    }
    vals_ser = {
        cid: {"value": r.value, "source": r.source,
              "denomination": r.denomination, "unit": r.unit}
        for cid, r in all_values.items()
    }
    state = {"cells": plan_cells, "values": vals_ser}

    sorted_cids = sorted(plan.cells, key=lambda c: (c[0], int(c[1:])))
    choices = [
        (f"{cid} \u2014 {plan.cells[cid].metric} ({plan.cells[cid].period})",
         cid)
        for cid in sorted_cids
    ]
    first = sorted_cids[0] if sorted_cids else None
    first_src = _render_source(first, plan_cells, vals_ser, index) if first else ""

    yield (
        stencil,
        "",
        gr.update(choices=choices, value=first),
        first_src,
        state,
    )


# ═════════════════════════════════════════════════════════════════════════
# UI Construction
# ═════════════════════════════════════════════════════════════════════════

def create_ui(config, index, gradio_host="127.0.0.1", gradio_port=7860,
              share=False):
    """Build and return the Gradio Blocks app."""
    import gradio as gr

    css = """
    @keyframes spin { to { transform: rotate(360deg); } }
    .pms1-spinner {
        display: inline-block; width: 12px; height: 12px;
        border: 2px solid #444; border-top-color: #9c3730;
        border-radius: 50%; animation: spin 0.6s linear infinite;
    }

    /* ── cell-list-col: Column flex item — shrink-wrap ── */
    #cell-list-col {
        flex: 0 0 auto !important;
        width: fit-content !important;
        min-width: 180px !important;
    }

    /* ── cell-list: Radio stripped to flat monospace list ── */
    .cell-list {
        width: 100% !important;
    }
    .cell-list .wrap {
        gap: 0 !important;
        flex-direction: column !important;
    }
    .cell-list label {
        display: block !important;
        padding: 5px 10px !important;
        margin: 0 !important;
        border-radius: 0 !important;
        border: none !important;
        background: transparent !important;
        font-family: monospace !important;
        font-size: 11px !important;
        color: #c0c0c0 !important;
        cursor: pointer !important;
        white-space: nowrap !important;
        transition: background 0.1s !important;
    }
    .cell-list label:hover {
        background: #2a2a3e !important;
    }
    .cell-list label.selected,
    .cell-list label:has(input:checked) {
        background: #3a4a6e !important;
        border-left: 3px solid #569cd6 !important;
        color: #dcdcaa !important;
    }
    .cell-list label input {
        display: none !important;
    }
    .cell-list label .text-lg {
        font-size: 11px !important;
    }

    /* ── full page width ── */
    .gradio-container {
        max-width: 100% !important;
        --color-accent: #384f37 !important;
        --color-accent-soft: #2a3b29 !important;
    }

    /* ── kill orange accents ── */
    button.primary,
    .gr-button-primary,
    button[variant="primary"] {
        background: #384f37 !important;
        border-color: #384f37 !important;
    }
    button.primary:hover,
    .gr-button-primary:hover,
    button[variant="primary"]:hover {
        background: #4a6849 !important;
        border-color: #4a6849 !important;
    }
    *:focus {
        --ring-color: #384f37 !important;
    }
    input:focus, textarea:focus, select:focus,
    .wrap:focus-within {
        border-color: #384f37 !important;
        box-shadow: 0 0 0 2px rgba(56, 79, 55, 0.3) !important;
    }
    .selected, .border-accent {
        border-color: #384f37 !important;
    }

    /* ── strip internal block borders inside groups ── */
    .input-group .block {
        border: none !important;
        background: transparent !important;
        padding: 4px 0 !important;
        box-shadow: none !important;
    }
    .input-group {
        padding: 12px !important;
        gap: 4px !important;
    }
    """

    with gr.Blocks(title="PMS1 Pipeline Visualizer") as demo:

        gr.HTML(f"<style>{css}</style>")

        gr.Markdown(
            "# Poony Multiretrieval S1 &nbsp;&nbsp;&nbsp; *tabular retrieval only*"
        )

        # ── Top bar — single container ─────────────────────────────
        with gr.Group(elem_classes=["input-group"]):
            query_input = gr.Textbox(
                label="Query", lines=1,
                placeholder="e.g. What were Best Buy's gross profit "
                            "margin and net profit margin for FY2022 and FY2023?",
            )
            with gr.Row():
                with gr.Column(scale=1):
                    sector_input = gr.Textbox(
                        label="Sector", lines=1,
                        placeholder="(cosmetic)",
                    )
                    firm_input = gr.Textbox(
                        label="Firm(s)", lines=1,
                        placeholder="e.g. Best Buy",
                    )
                run_btn = gr.Button("Run", variant="primary", scale=0)

        # ── Stencil (upper) ─────────────────────────────────────────
        stencil_output = gr.HTML(
            value='<div style="color:#666;font-family:monospace;'
                  'font-size:12px;padding:20px;text-align:center">'
                  'Stencil will appear here after running a query.</div>',
        )

        # ── Pipeline cards (lower, during execution) ────────────────
        pipeline_output = gr.HTML(value="")

        # ── Source browser (lower, after execution) ─────────────────
        with gr.Row():
            with gr.Column(scale=0, elem_id="cell-list-col"):
                cell_selector = gr.Radio(
                    label="Select Cell", choices=[],
                    elem_classes=["cell-list"],
                )
            with gr.Column(scale=1):
                source_display = gr.HTML(value="")

        # ── State ───────────────────────────────────────────────────
        index_state = gr.State(index)
        config_state = gr.State(config)
        pipeline_state = gr.State({})

        # ── Lock / unlock ───────────────────────────────────────────
        lockable = [query_input, sector_input, firm_input, run_btn]
        all_outputs = [
            stencil_output, pipeline_output, cell_selector,
            source_display, pipeline_state,
        ]

        def _lock():
            return [gr.update(interactive=False)] * 4

        def _unlock():
            return [gr.update(interactive=True)] * 4

        run_btn.click(
            fn=_lock, inputs=None, outputs=lockable,
        ).then(
            fn=_stream_s1,
            inputs=[query_input, sector_input, firm_input,
                    index_state, config_state],
            outputs=all_outputs,
        ).then(
            fn=_unlock, inputs=None, outputs=lockable,
        )

        # ── Source browser click ────────────────────────────────────
        def _on_cell_select(cell_id, state, idx):
            if not cell_id or not state:
                return ""
            return _render_source(
                cell_id, state.get("cells", {}),
                state.get("values", {}), idx,
            )

        cell_selector.change(
            fn=_on_cell_select,
            inputs=[cell_selector, pipeline_state, index_state],
            outputs=[source_display],
        )

    return demo
