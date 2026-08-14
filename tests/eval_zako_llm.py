"""Explicit names-only Bunragman evaluation for Zako.

This file is intentionally named ``eval_`` rather than ``test_`` so normal
unit-test discovery does not make a live model call. Run it explicitly only
after ``tests/test_zako_unit.py`` passes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scripts.config import Config
from src.scripts.zako.scanner import ROOT_FILES_KEY
from src.scripts.zako.selector import BunragmanSelector, parse_selection


def _node(files=None, folders=None, file_count=0, readable=True):
    value = {
        "folders": folders or {},
        "files": files or [],
        "file_count": file_count,
    }
    if not readable:
        value["readable"] = False
    return value


CASES = [
    {
        "name": "company-specific",
        "query": "What is LITE's competitive outlook?",
        "inventory": {
            "LITE": _node(["LITE/annual_report.md"], file_count=1),
            "COHR": _node(["COHR/annual_report.md"], file_count=1),
        },
        "must_select": ["LITE"],
        "must_not_select": [],
    },
    {
        "name": "thematic-source",
        "query": "How is the optical market developing?",
        "inventory": {
            "0 Optical": _node(
                ["0 Optical/market.md"],
                folders={"Reports": _node(["market.md"], file_count=1)},
                file_count=1,
            ),
            "AMAT": _node(["AMAT/company.md"], file_count=1),
        },
        "must_select": ["0 Optical"],
        "must_not_select": [],
    },
    {
        "name": "root-files",
        "query": "Summarize the loose market note at the corpus root.",
        "inventory": {
            ROOT_FILES_KEY: _node(["market_note.md"], file_count=1),
            "LITE": _node([], file_count=0),
        },
        "must_select": [ROOT_FILES_KEY],
        "must_not_select": [],
    },
    {
        "name": "no-relevant-source",
        "query": "What is the history of medieval architecture?",
        "inventory": {
            "LITE": _node(["earnings.md"], file_count=1),
            "COHR": _node(["earnings.md"], file_count=1),
        },
        "must_select": [],
        "must_not_select": ["LITE", "COHR"],
    },
]


def run_eval() -> None:
    selector = BunragmanSelector(Config())
    for case in CASES:
        raw = selector(case["query"], case["inventory"])
        selected = parse_selection(raw, set(case["inventory"]))
        missing = set(case["must_select"]) - set(selected)
        prohibited = set(case["must_not_select"]) & set(selected)
        if missing or prohibited:
            raise AssertionError(
                f"{case['name']}: selected={selected}, "
                f"missing={sorted(missing)}, prohibited={sorted(prohibited)}"
            )
        print(json.dumps({"case": case["name"], "selected": selected}))


if __name__ == "__main__":
    run_eval()
