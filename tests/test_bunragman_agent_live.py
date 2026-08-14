"""Bunragman per-source agent live test — calls call_bunragman() directly,
not through sekei's discovery/GROUP loop.

SELECT and SUMMARIZE are real DeepSeek calls (bunragman_agent role, see
src/scripts/bunragman/sekei.py's module docstring). OSHA and BunNavHarness
are both monkeypatched — _call_osha_stub() always returns one fixed prior
research answer, _call_bunnavharness_stub() returns one of two fixed JSON
fixtures picked deterministically by the xlsx file's path. Needs
DEEPSEEK_API_KEY.

Four cases, each a synthetic {label: [file_path, ...]} source, not real
files on disk (the stubs never open them):
  A — non-xlsx only: no SELECT call needed, OSHA stub text only.
  B — xlsx only: SELECT picks the file, BunNav stub only, no OSHA text.
  C — mixed, xlsx path hashes to the INCONGRUENT fixture: same source
      gets both OSHA text and conflicting BunNav numbers — summary should
      flag the conflict.
  D — mixed, xlsx path hashes to the CONGRUENT fixture: same shape, no
      conflict — summary should read as plain.

Usage:
    python tests/test_bunragman_agent_live.py
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
from src.scripts.bunragman.sekei import call_bunragman

QUERY = (
    "What are the current sell-side analyst price targets and internal "
    "model EPS estimates for LITE?"
)

# Source A: non-xlsx only.
SOURCE_A = {"JPM": ["JPM/20251205_JP_Morgan_CIEN_Hardware.md"]}

# Source B: xlsx only.
SOURCE_B = {"Internal Model": ["Internal Model/LITE_model_2026Q1.xlsx"]}

# Source C: mixed, xlsx path hashes to the INCONGRUENT fixture — expect a
# flagged conflict in the summary.
SOURCE_C = {
    "Mixed-Conflict": [
        "Mixed/JPM_note.md",
        "Internal Model/LITE_model_2026Q1.xlsx",  # hashes incongruent
    ]
}

# Source D: mixed, xlsx path hashes to the CONGRUENT fixture — expect a
# plain, unflagged summary.
SOURCE_D = {
    "Mixed-Congruent": [
        "Mixed/JPM_note.md",
        "Internal Model/LITE_model_congruent.xlsx",  # hashes congruent
    ]
}

CASES = [
    ("A (non-xlsx only)", SOURCE_A),
    ("B (xlsx only)", SOURCE_B),
    ("C (mixed, conflict expected)", SOURCE_C),
    ("D (mixed, no conflict expected)", SOURCE_D),
]


class PrintChannel:
    """Duck-typed ToolChannel — .print() goes straight to real stdout."""

    def print(self, msg, **kw):
        print(f"  {msg}")

    def input(self, question, **kw):
        raise AssertionError("call_bunragman should never call .input()")


def run_case(label: str, source: dict, config: Config, session_dir: Path) -> None:
    print(f"\n=== Source {label} ===")
    channel = PrintChannel()
    path = call_bunragman(source, QUERY, config, channel, session_dir)
    print(f"\n--- {path} ---\n{Path(path).read_text()}")


if __name__ == "__main__":
    config = Config.from_env()
    session_dir = Path(mkdtemp(prefix="bunragman_agent_test_"))
    print(f"Session dir: {session_dir / 'bunragman'}")

    for label, source in CASES:
        run_case(label, source, config, session_dir)

    print("\nAll cases ran.")
