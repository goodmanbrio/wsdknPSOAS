"""Bunragman sekei — mocked-discovery edge cases only.

Calls run_bunragman_sekei() with zako_discover patched, so these two
cases never reach a live LLM call or either of the two confirm/modify/
redo loops: discovery failure, and discovery returning zero directories
(N=0, a valid result, not a failure).

For the full live path — real discovery, real GROUP, both real confirm
loops, real per-source agent, real reconcile — see test_bunragman_e2e.py.
For the per-source agent (call_bunragman) tested in isolation, see
test_bunragman_agent_live.py.

Usage:
    python tests/test_bunragman_sekei_stub.py
"""

import sys
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.scripts.config import Config
from src.scripts.bunragman.sekei import run_bunragman_sekei
from src.scripts.zako import FAILURE

QUERY = "What is LITE's performance according to different brokers?"


class PrintChannel:
    """Duck-typed ToolChannel — .print() goes straight to real stdout.

    Both mocked cases below short-circuit before any ask_user call
    (discovery fails, or returns zero directories), so .input() is
    never actually exercised here — it errors loudly if it is, since
    that would mean a case stopped being mocked the way this test
    assumes.
    """

    def print(self, msg, **kw):
        print(f"  {msg}")

    def input(self, question, **kw):
        raise AssertionError(
            "unexpected ask_user call — this test's cases are mocked to "
            "never reach one; see test_bunragman_e2e.py for the live path"
        )


def run_fail_discovery_case():
    print("\n=== Case 1: discovery fails ===")
    config = Config.from_env()
    channel = PrintChannel()
    session_dir = Path(mkdtemp(prefix="bunragman_test_"))

    with patch(
        "src.scripts.bunragman.sekei.zako_discover",
        return_value=FAILURE,
    ):
        answer = run_bunragman_sekei(QUERY, config, channel, session_dir)

    print(f"\n--- Result ---\n{answer}\n")
    assert answer == "**Discovery failed.** No directories resolved."


def run_empty_sources_case():
    print("\n=== Case 2: discovery returns nothing (N=0) ===")
    config = Config.from_env()
    channel = PrintChannel()
    session_dir = Path(mkdtemp(prefix="bunragman_test_"))

    with patch(
        "src.scripts.bunragman.sekei.zako_discover",
        return_value=(QUERY, {}),
    ):
        answer = run_bunragman_sekei(QUERY, config, channel, session_dir)

    print(f"\n--- Result ---\n{answer}\n")
    assert answer == "**No sources found.**"


if __name__ == "__main__":
    run_fail_discovery_case()
    run_empty_sources_case()
    print("\nAll cases ran.")
