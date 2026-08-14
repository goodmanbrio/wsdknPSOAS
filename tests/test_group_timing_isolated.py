"""Isolated GROUP timing probe — reproduces the exact call that hung
before the agentic-loop rebuild: _run_group_loop() against the same 3
raw dirs (0 Optical, LITE, COHR — 32 files total) Zako handed it, timed,
no discovery, no fan-out after.

AutoChannel auto-confirms whatever the model's first ask_user question
is with "y" — this test is about latency, not exercising modify/redo
conversation depth.

Needs DEEPSEEK_API_KEY.

Usage:
    python tests/test_group_timing_isolated.py
"""

import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.scripts.config import Config
from src.scripts.zako.scanner import scan_source_root, expand_selection
from src.scripts.bunragman.sekei import _run_group_loop

QUERY = "What is LITE's performance according to different brokers?"
DEFAULT_ROOT = "/Users/goodmanbrio/Downloads/HVC/Packs"
DIRS = ["0 Optical", "LITE", "COHR"]


class AutoChannel:
    def print(self, msg, **kw):
        print(f"  {msg}")

    def input(self, question, **kw):
        print(f"  [ASK] {question}")
        print("  [ANSWER] y")
        return "y"


def main() -> None:
    source_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_ROOT)
    print(f"Source root: {source_root}")

    inventory = scan_source_root(source_root)
    raw_dirs = expand_selection(inventory, DIRS)
    total_files = sum(len(v) for v in raw_dirs.values())
    print(f"Raw dirs: {[(k, len(v)) for k, v in raw_dirs.items()]}")
    print(f"Total files: {total_files}\n")

    config = Config.from_env()
    channel = AutoChannel()
    print("Calling _run_group_loop() — the new agentic GROUP loop...")
    t0 = time.monotonic()
    sources = _run_group_loop(raw_dirs, QUERY, config, channel)
    t1 = time.monotonic()

    print(f"\n[GROUP] _run_group_loop(): {t1 - t0:.2f}s")
    if sources is None:
        print("Result: None (cancelled or exhausted)")
    else:
        print(f"Sources proposed: {len(sources)}")
        for label, files in sources.items():
            print(f"  - {label}: {len(files)} file(s)")


if __name__ == "__main__":
    main()
