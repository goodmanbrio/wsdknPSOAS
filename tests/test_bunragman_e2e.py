"""Bunragman end-to-end live test — full run_bunragman_sekei() loop, no
mocking of sekei's own control flow. Every step in Specs/wsnBunragMan.md's
diagram runs for real except the two monkeypatched tool calls inside
call_bunragman (OSHA, BunNavHarness — see src/scripts/bunragman/sekei.py's
module docstring for why those two stay stubs).

Two live ask_user confirm/modify/redo loops fire in sequence: Zako's over
the directories it discovers, then Bunragman's own over GROUP's proposed
source batching. AutoChannel answers "y" to both; anything beyond that
gets "exit", so an unscripted follow-up (e.g. a `modify` sub-question)
fails fast instead of hanging.

For the narrower, mocked-discovery edge cases (discovery failure, N=0
sources), see test_bunragman_sekei_stub.py.

Needs DEEPSEEK_API_KEY. Hits the real data/files_ingested corpus.

Usage:
    python tests/test_bunragman_e2e.py
"""

import sys
from pathlib import Path
from tempfile import mkdtemp

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

QUERY = "What is LITE's performance according to different brokers?"


class AutoChannel:
    """Duck-typed ToolChannel. Answers "y" to the first N scripted
    confirm prompts (Zako's, then Bunragman's own), "exit" to anything
    beyond that — avoids hanging on an unscripted follow-up this test
    isn't written to answer (e.g. a `modify` sub-question)."""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self._idx = 0

    def print(self, msg, **kw):
        print(f"  {msg}")

    def input(self, question, **kw):
        print(f"  [ASK] {question}")
        answer = (
            self._answers[self._idx] if self._idx < len(self._answers) else "exit"
        )
        self._idx += 1
        print(f"  [ANSWER] {answer}")
        return answer


def run_e2e() -> None:
    print(
        "\n=== Bunragman e2e: real discovery, real GROUP, real confirm x2, "
        "real per-source agent, real reconcile ==="
    )
    config = Config.from_env()
    channel = AutoChannel(["y", "y"])
    session_dir = Path(mkdtemp(prefix="bunragman_e2e_"))

    answer = run_bunragman_sekei(QUERY, config, channel, session_dir)

    print(f"\n--- Final answer ({len(answer)} chars) ---\n{answer}\n")
    out_dir = session_dir / "bunragman"
    print(f"Per-source summaries written to: {out_dir}")
    for p in sorted(out_dir.glob("*.md")) if out_dir.exists() else []:
        print(f"  - {p.name}")


if __name__ == "__main__":
    run_e2e()
    print("\nDone.")
