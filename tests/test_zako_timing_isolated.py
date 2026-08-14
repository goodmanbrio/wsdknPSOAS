"""Isolated Zako timing probe — NOT the full zako_discover() flow.

Runs the three stages of one selection attempt separately, timed, against
a real source root: filesystem scan (scan_source_root), JSON
serialization of the payload sent to the model, and the actual
BunragmanSelector LLM call. Points at a directory you pass on the command
line (or /Users/goodmanbrio/Downloads/HVC/Packs by default) rather than
the repo's small data/files_ingested sample, so the timing reflects real
production scale.

No ask_user loop, no confirm/modify/redo — this is not run_bunragman_sekei,
it calls the selector once and reports where the time actually went.

Needs DEEPSEEK_API_KEY.

Usage:
    python tests/test_zako_timing_isolated.py ["<query>"] [source_root]
"""

import json
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
from src.scripts.zako.scanner import scan_source_root
from src.scripts.zako.selector import BunragmanSelector

DEFAULT_QUERY = "What is LITE's performance according to different brokers?"
DEFAULT_ROOT = "/Users/goodmanbrio/Downloads/HVC/Packs"


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    source_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(DEFAULT_ROOT)

    print(f"Source root: {source_root}")
    print(f"Query: {query}\n")

    # ── Stage 1: filesystem scan (no LLM, no network) ────────────────
    t0 = time.monotonic()
    inventory = scan_source_root(source_root)
    t1 = time.monotonic()
    print(f"[1] scan_source_root(): {t1 - t0:.2f}s")
    print(f"    top-level candidates: {len(inventory.candidates)}")
    total_files = sum(len(c.files) for c in inventory.candidates.values())
    print(f"    total files across all candidates (recursive): {total_files}")

    # ── Stage 2: JSON serialization of what gets sent to the model ───
    t2 = time.monotonic()
    payload_str = json.dumps(inventory.payload, ensure_ascii=False)
    t3 = time.monotonic()
    print(f"[2] json.dumps(inventory.payload): {t3 - t2:.3f}s")
    print(f"    payload size: {len(payload_str)} chars (~{len(payload_str)//4} tokens)")

    # ── Stage 3: the actual selector LLM call ─────────────────────────
    config = Config.from_env()
    selector = BunragmanSelector(config)
    t4 = time.monotonic()
    raw = selector(query, inventory.payload)
    t5 = time.monotonic()
    print(f"[3] BunragmanSelector LLM call: {t5 - t4:.2f}s")
    print(f"    raw response ({len(raw)} chars): {raw[:500]}")

    print(f"\nTotal: {t5 - t0:.2f}s "
          f"(scan {t1-t0:.2f}s + serialize {t3-t2:.3f}s + LLM {t5-t4:.2f}s)")


if __name__ == "__main__":
    main()
