#!/usr/bin/env python3
"""
eval_pms2_leng.py — Eval harness: Haiku vs DeepSeek Leng extraction accuracy + cost.

Runs each test case through structured_complete with configurable providers,
compares output against hand-labeled ground truth.

Usage:
    cd PSOAS_Jul20
    python tests/eval_pms2_leng.py [--providers haiku,v4pro,v4flash]
                                   [--cases tests/eval_data/leng_cases.json]
                                   [--out tests/eval_runs/]
"""

import argparse
import json
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

# ── sys.path setup ──
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.scripts.config import Config
from src.scripts.llm import _load_profiles, _resolve_profile, _make_llm
from src.scripts.PMS2.leng_caller import LENG_OUTPUT_SCHEMA
from src.harness.sysprompts import load_sysprompt
from src.scripts.PMS2.denom_reconcile import DENOM_FACTORS

# ── Provider → profile mapping ──
PROVIDER_PROFILES = {
    "haiku": "anthropic_hayasui",
    "v4pro": "deepseek_v4pro_leng",
    "v4flash": "deepseek_v4flash_leng",
}

# ── Pricing per 1M tokens (input/output) ──
PRICING = {
    "anthropic_hayasui":    {"input": 1.00, "output": 5.00},
    "deepseek_v4pro_leng":  {"input": 0.435, "output": 0.87},
    "deepseek_v4flash_leng": {"input": 0.14, "output": 0.28},
}

_VALID_DENOMS = list(DENOM_FACTORS.keys())


def load_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def build_leng_prompt(case: dict) -> str:
    """Build the Leng prompt identically to leng_caller._run_single_leng."""
    return (
        f"{case['cell_descriptions']}\n\n"
        f"\n\n"
        f"--- CHUNK (node_id: eval-{case['id']}) ---\n"
        f"{case['chunk_text']}\n"
        f"--- END CHUNK ---"
    )


def load_leng_sysprompt(profile: str, firm: str) -> str:
    valid_units = json.loads(
        (Path(__file__).resolve().parent.parent
         / "src" / "scripts" / "PMS2" / "hardcode_dependencies" / "units.json")
        .read_text()
    )
    return load_sysprompt(
        "pms2_leng",
        profile,
        firm=firm,
        valid_denoms=json.dumps(_VALID_DENOMS),
        valid_units=json.dumps(valid_units),
        fiscal_calendar="Not available.",
    )


def value_matches(expected: float, actual: float, tolerance: float = 0.01) -> bool:
    """Check if values match within tolerance (default 1% for float rounding)."""
    if expected == 0:
        return actual == 0
    return abs(expected - actual) / abs(expected) <= tolerance


def grade_case(expected_cells: dict, expected_misses: list, result_cells: dict) -> dict:
    """Grade a single case. Returns per-cell metrics."""
    tp = 0  # true positive: in expected AND in result, value correct
    fp = 0  # false positive: in result but NOT in expected
    fn = 0  # false negative: in expected but NOT in result
    denom_errors = 0
    unit_errors = 0
    total_found = 0
    details = []

    # Check expected hits
    for cell_id, expected in expected_cells.items():
        if cell_id in result_cells:
            actual = result_cells[cell_id]
            if not isinstance(actual, dict):
                fn += 1
                details.append(f"{cell_id}: malformed (not a dict)")
                continue

            actual_val = actual.get("value")
            actual_denom = actual.get("denom", "")
            actual_unit = actual.get("unit", "")

            if actual_val is not None and value_matches(expected["value"], actual_val):
                tp += 1
                total_found += 1
                if actual_denom != expected["denom"]:
                    denom_errors += 1
                    details.append(
                        f"{cell_id}: value OK, DENOM ERROR "
                        f"({actual_denom} vs {expected['denom']})"
                    )
                elif actual_unit != expected["unit"]:
                    unit_errors += 1
                    details.append(
                        f"{cell_id}: value OK, UNIT ERROR "
                        f"({actual_unit} vs {expected['unit']})"
                    )
                else:
                    details.append(f"{cell_id}: OK")
            else:
                fn += 1
                details.append(
                    f"{cell_id}: WRONG VALUE "
                    f"(got {actual_val}, expected {expected['value']})"
                )
        else:
            fn += 1
            details.append(f"{cell_id}: MISSED (false negative)")

    # Check false positives (in result but not in expected)
    all_expected_ids = set(expected_cells.keys())
    for cell_id in result_cells:
        if cell_id not in all_expected_ids:
            fp += 1
            total_found += 1
            actual = result_cells[cell_id]
            val = actual.get("value", "?") if isinstance(actual, dict) else actual
            details.append(f"{cell_id}: FALSE POSITIVE (value={val})")

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "denom_errors": denom_errors,
        "unit_errors": unit_errors,
        "total_found": total_found,
        "details": details,
    }


def run_eval(
    cases: list[dict],
    profiles_to_test: list[str],
    config: Config,
) -> str:
    """Run all cases across all providers, return markdown report."""
    md = StringIO()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profiles = _load_profiles(config.llm_profiles_path)

    md.write(f"# Leng Eval — {ts}\n\n")
    md.write(f"**Profiles**: {', '.join(f'`{p}`' for p in profiles_to_test)}\n\n")
    md.write(f"**Cases**: {len(cases)}\n\n")
    md.write("---\n\n")

    # Aggregate metrics per provider
    agg: dict[str, dict] = {}

    for profile_name in profiles_to_test:
        prof = _resolve_profile(profiles, profile_name)
        backend = _make_llm(prof)
        provider_label = profile_name

        print(f"\n{'='*60}")
        print(f"PROVIDER: {provider_label} ({backend.model_name()})")
        print(f"{'='*60}")

        md.write(f"## Provider: `{provider_label}`\n\n")
        md.write(f"Model: `{backend.model_name()}`\n\n")

        prov_agg = {
            "tp": 0, "fp": 0, "fn": 0,
            "denom_errors": 0, "unit_errors": 0,
            "total_cost": 0.0, "total_latency": 0.0,
            "cases_run": 0, "errors": 0,
        }

        case_rows = []

        for case in cases:
            case_id = case["id"]
            firm = case["firm"]
            print(f"  Case: {case_id}...", end=" ", flush=True)

            prompt = build_leng_prompt(case)
            try:
                sys_prompt = load_leng_sysprompt(profile_name, firm)
            except FileNotFoundError as e:
                print(f"SKIP (no sysprompt: {e})")
                md.write(f"### {case_id}\n\n**SKIPPED**: no sysprompt\n\n")
                continue

            t0 = time.time()
            try:
                result = backend.structured_complete(
                    prompt=prompt,
                    schema=LENG_OUTPUT_SCHEMA,
                    system_prompt=sys_prompt,
                    label=f"eval-leng-{case_id}",
                )
            except Exception as e:
                latency = time.time() - t0
                print(f"ERROR ({latency:.1f}s): {e}")
                md.write(f"### {case_id}\n\n**ERROR**: `{e}`\n\n")
                prov_agg["errors"] += 1
                prov_agg["cases_run"] += 1
                case_rows.append((case_id, "-", "-", "-", "-", "-", f"ERROR: {str(e)[:40]}"))
                continue

            latency = time.time() - t0

            result_cells = result.get("cells", {})
            if not isinstance(result_cells, dict):
                result_cells = {}

            grades = grade_case(
                case.get("expected_cells", {}),
                case.get("expected_misses", []),
                result_cells,
            )

            # Estimate cost (rough: ~3k input tokens, ~300 output tokens per call)
            pricing = PRICING.get(profile_name, {"input": 0, "output": 0})
            est_input_tokens = len(prompt) // 4 + 500  # rough char→token
            est_output_tokens = len(json.dumps(result)) // 4
            cost = (
                est_input_tokens * pricing["input"] / 1_000_000
                + est_output_tokens * pricing["output"] / 1_000_000
            )

            prov_agg["tp"] += grades["tp"]
            prov_agg["fp"] += grades["fp"]
            prov_agg["fn"] += grades["fn"]
            prov_agg["denom_errors"] += grades["denom_errors"]
            prov_agg["unit_errors"] += grades["unit_errors"]
            prov_agg["total_cost"] += cost
            prov_agg["total_latency"] += latency
            prov_agg["cases_run"] += 1

            n_expected = len(case.get("expected_cells", {}))
            n_found = grades["total_found"]
            status = "OK" if grades["fp"] == 0 and grades["fn"] == 0 else "ISSUE"
            print(f"{status} ({latency:.1f}s) TP={grades['tp']} FP={grades['fp']} FN={grades['fn']}")

            case_rows.append((
                case_id,
                str(n_expected),
                str(n_found),
                str(grades["tp"]),
                str(grades["fp"]),
                str(grades["fn"]),
                "; ".join(grades["details"][:3]),
            ))

            # Per-case detail
            md.write(f"### {case_id} ({latency:.1f}s, ~${cost:.4f})\n\n")
            md.write("```json\n")
            md.write(json.dumps(result, indent=2, ensure_ascii=False))
            md.write("\n```\n\n")
            for d in grades["details"]:
                md.write(f"- {d}\n")
            md.write("\n")

        # Provider summary table
        total_tp = prov_agg["tp"]
        total_fp = prov_agg["fp"]
        total_fn = prov_agg["fn"]
        precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
        total_found = total_tp + total_fp
        denom_acc = (
            (total_found - prov_agg["denom_errors"]) / total_found
            if total_found > 0 else 1.0
        )
        avg_cost = prov_agg["total_cost"] / max(prov_agg["cases_run"], 1)
        avg_latency = prov_agg["total_latency"] / max(prov_agg["cases_run"], 1)

        md.write(f"### Summary: `{provider_label}`\n\n")
        md.write("| Metric     | Value |\n")
        md.write("|------------|-------|\n")
        md.write(f"| Cases      | {prov_agg['cases_run']} |\n")
        md.write(f"| Errors     | {prov_agg['errors']} |\n")
        md.write(f"| Precision  | {precision:.2f} |\n")
        md.write(f"| Recall     | {recall:.2f} |\n")
        md.write(f"| Denom acc  | {denom_acc:.2f} |\n")
        md.write(f"| Avg cost   | ${avg_cost:.4f}/case |\n")
        md.write(f"| Avg latency| {avg_latency:.1f}s |\n\n")

        md.write("| Case | Expected | Found | TP | FP | FN | Notes |\n")
        md.write("|------|----------|-------|----|----|----|-------|\n")
        for row in case_rows:
            md.write(f"| {' | '.join(row)} |\n")
        md.write("\n---\n\n")

        agg[provider_label] = prov_agg

    # Cross-provider comparison
    if len(agg) > 1:
        md.write("## Cross-provider comparison\n\n")
        md.write("| Provider | Precision | Recall | Denom Acc | Avg Cost | Avg Latency |\n")
        md.write("|----------|-----------|--------|-----------|----------|-------------|\n")
        for name, a in agg.items():
            tp, fp, fn = a["tp"], a["fp"], a["fn"]
            p = tp / (tp + fp) if (tp + fp) > 0 else 0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0
            found = tp + fp
            da = (found - a["denom_errors"]) / found if found > 0 else 1.0
            ac = a["total_cost"] / max(a["cases_run"], 1)
            al = a["total_latency"] / max(a["cases_run"], 1)
            md.write(f"| {name} | {p:.2f} | {r:.2f} | {da:.2f} | ${ac:.4f} | {al:.1f}s |\n")
        md.write("\n")

    return md.getvalue()


def main():
    parser = argparse.ArgumentParser(description="PMS2 Leng eval harness")
    parser.add_argument(
        "--providers", default="v4pro",
        help="Comma-separated provider keys: haiku,v4pro,v4flash (default: v4pro)",
    )
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).parent / "eval_data" / "leng_cases.json"),
        help="Path to eval cases JSON",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "eval_runs"),
        help="Output directory for reports",
    )
    args = parser.parse_args()

    config = Config.from_env()

    provider_keys = [k.strip() for k in args.providers.split(",")]
    profiles = []
    for k in provider_keys:
        if k not in PROVIDER_PROFILES:
            print(f"Unknown provider '{k}'. Known: {list(PROVIDER_PROFILES.keys())}")
            sys.exit(1)
        profiles.append(PROVIDER_PROFILES[k])

    cases = load_cases(Path(args.cases))
    print(f"Loaded {len(cases)} eval cases")

    report = run_eval(cases, profiles, config)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    providers_slug = "_".join(provider_keys)
    out_path = out_dir / f"leng_eval_{providers_slug}_{ts}.md"
    out_path.write_text(report)
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
