#!/usr/bin/env python3
"""Tier-1 repro: does TerminalRouter bind an answer to its question?

Hypothesis (P1): it does not. Lines typed while nothing is reading sit
in the kernel tty buffer and are handed to whatever question is
dequeued next. A bare Enter typed earlier therefore answers a later
`continue? [y/n]` gate with "" -> dispatcher reads that as "not y" ->
status "user_aborted", a decision the human never made.

Modes
-----
  python tests/repro_stdin_typeahead.py            # automated, pty-driven
  python tests/repro_stdin_typeahead.py manual     # human types, no pty

The automated harness drives a child process over a pty and writes
bytes into the master BEFORE the child renders its prompt. That is
byte-for-byte what type-ahead on a real terminal does.

Scenarios
  A typeahead_y     pre-type "y\\n"      -> expect instant return "y"
  B stale_newline   pre-type "\\n"       -> expect instant return ""   (the CIEN kill)
  C control         type only AFTER the prompt renders -> expect a real block
  D cascade         pre-type "\\ny\\n", two firms ask -> expect firm1 "" / firm2 "y"

Scenario D is the CIEN/COHR transcript reproduced from one stray Enter
plus one stray y.

This file is a forensic probe, not a regression test. It touches no
production code.
"""

from __future__ import annotations

import fcntl
import os
import pty
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

WINDOW = 3.0          # seconds the child idles with nothing reading stdin
INSTANT = 0.5         # returns faster than this == came from the buffer


# ══════════════════════════════════════════════════════════════════
# CHILD — runs inside the pty
# ══════════════════════════════════════════════════════════════════

def _emit(line: str) -> None:
    """Marker line straight to fd 1, bypassing rich."""
    os.write(1, (line + "\n").encode())


def _child_single() -> None:
    from src.harness.terminal_router import register

    ch = register("REPRO")
    _emit("###WINDOW-OPEN")
    time.sleep(WINDOW)

    t0 = time.monotonic()
    ans = ch.input("did you type this? [y/n]")
    el = time.monotonic() - t0

    _emit(f"###RESULT|single|{el:.3f}|{ans!r}")


def _child_dual() -> None:
    """Two firms hit their gate, serialized by the router's FIFO."""
    from src.harness.terminal_router import register

    results: dict[str, tuple[float, str]] = {}
    lock = threading.Lock()

    def ask(firm: str, delay: float) -> None:
        time.sleep(delay)
        ch = register(f"PMS2-disp-{firm}")
        t0 = time.monotonic()
        ans = ch.input(
            f"3 iterations for {firm}, 2 found nothing. continue? [y/n]"
        )
        el = time.monotonic() - t0
        with lock:
            results[firm] = (el, ans)

    _emit("###WINDOW-OPEN")
    time.sleep(WINDOW)

    threads = [
        threading.Thread(target=ask, args=("CIEN", 0.0)),
        threading.Thread(target=ask, args=("COHR", 0.4)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    for firm in ("CIEN", "COHR"):
        el, ans = results.get(firm, (-1.0, "<NO ANSWER>"))
        _emit(f"###RESULT|{firm}|{el:.3f}|{ans!r}")


def _child_spinner() -> None:
    """Does a worker-thread spinner repaint over a live prompt?"""
    from src.harness.terminal_router import register, _router

    ch = register("REPRO")

    def spam() -> None:
        time.sleep(WINDOW + 1.0)          # prompt is live by now
        for i in range(6):
            _router.start_spinner(f"PMS2-leng-LITE")
            time.sleep(0.3)

    threading.Thread(target=spam, daemon=True).start()

    _emit("###WINDOW-OPEN")
    time.sleep(WINDOW)
    t0 = time.monotonic()
    ans = ch.input("prompt should stay visible. [y/n]")
    el = time.monotonic() - t0
    _emit(f"###RESULT|spinner|{el:.3f}|{ans!r}")


# ══════════════════════════════════════════════════════════════════
# PARENT — pty harness
# ══════════════════════════════════════════════════════════════════

def _set_winsize(fd: int, rows: int = 50, cols: int = 200) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


_RAW_SEQ = [0]


def _run_scenario(
    child_mode: str,
    payload: bytes,
    when: str,          # "before_prompt" | "after_prompt"
) -> tuple[list[tuple[str, float, str]], bytes]:
    """Spawn child on a pty, inject payload, collect ###RESULT lines."""
    master, slave = pty.openpty()
    _set_winsize(slave)

    env = dict(os.environ, PYTHONUNBUFFERED="1", COLUMNS="200")
    proc = subprocess.Popen(
        [sys.executable, __file__, "child", child_mode],
        stdin=slave, stdout=slave, stderr=slave,
        close_fds=True, cwd=str(_ROOT), env=env,
    )
    os.close(slave)

    captured = bytearray()
    results: list[tuple[str, float, str]] = []
    injected = False
    n_expected = 2 if child_mode == "dual" else 1
    deadline = time.monotonic() + WINDOW + 25

    def _pump() -> bool:
        """Read one chunk if any is ready within 0.25s. False on EOF."""
        import select
        r, _, _ = select.select([master], [], [], 0.25)
        if not r:
            return True
        try:
            chunk = os.read(master, 4096)
        except OSError:
            return False
        if not chunk:
            return False
        captured.extend(chunk)
        return True

    while time.monotonic() < deadline:
        alive = _pump()
        text = captured.decode("utf-8", "replace")

        if text.count("###RESULT|") >= n_expected:
            break
        if not alive:
            break
        if proc.poll() is not None and not alive:
            break

        if not injected:
            if when == "before_prompt" and "###WINDOW-OPEN" in text:
                time.sleep(0.4)          # comfortably inside the idle window
                os.write(master, payload)
                injected = True
            # NB: cannot match on "[y/n]" — rich parses it as markup and
            # strips it from the rendered question. See scenario notes.
            elif when == "after_prompt" and "did you type this?" in text:
                time.sleep(1.2)          # let it genuinely block
                os.write(master, payload)
                injected = True

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    os.close(master)

    _RAW_SEQ[0] += 1
    raw_path = Path(f"/tmp/repro_raw_{_RAW_SEQ[0]}_{child_mode}.txt")
    raw_path.write_bytes(bytes(captured))

    # NOTE: the marker is NOT at line start — rich's `console.input`
    # writes the "> " prompt with no trailing newline, so the child's
    # next write lands on the same line.
    for line in captured.decode("utf-8", "replace").splitlines():
        idx = line.find("###RESULT|")
        if idx >= 0:
            _, label, el, ans = line[idx:].strip().split("|", 3)
            results.append((label, float(el), ans))
    if not results:
        print(f"    [raw output saved to {raw_path}, "
              f"rc={proc.returncode}, injected={injected}]")
    return results, bytes(captured)


def _verdict(ok: bool) -> str:
    return "CONFIRMED" if ok else "NOT REPRODUCED"


def main_parent() -> int:
    print("=" * 72)
    print("P1 repro — is a typed line bound to the question it answers?")
    print(f"idle window {WINDOW}s, 'instant' threshold {INSTANT}s")
    print("=" * 72)

    failures = 0

    # ── A: pre-typed "y" ──────────────────────────────────────────
    res, _ = _run_scenario("single", b"y\n", "before_prompt")
    print("\n[A] typeahead_y — typed 'y' while NOTHING was reading")
    for label, el, ans in res:
        instant = el < INSTANT
        print(f"    answer={ans}  elapsed={el:.3f}s  instant={instant}")
        ok = instant and ans in ("'y'", '"y"')
        print(f"    -> stale line consumed as an answer: {_verdict(ok)}")
        failures += 0 if ok else 1
    if not res:
        print("    -> NO RESULT (child died) — inconclusive")
        failures += 1

    # ── B: pre-typed bare Enter (the CIEN kill) ───────────────────
    res, _ = _run_scenario("single", b"\n", "before_prompt")
    print("\n[B] stale_newline — pressed Enter once while nothing was reading")
    for label, el, ans in res:
        instant = el < INSTANT
        print(f"    answer={ans}  elapsed={el:.3f}s  instant={instant}")
        ok = instant and ans in ("''", '""')
        print(f"    -> empty answer delivered without asking: {_verdict(ok)}")
        print("       dispatcher would read this as 'not y' -> user_aborted")
        failures += 0 if ok else 1
    if not res:
        print("    -> NO RESULT (child died) — inconclusive")
        failures += 1

    # ── C: control — type only after the prompt renders ───────────
    res, _ = _run_scenario("single", b"n\n", "after_prompt")
    print("\n[C] control — typed only AFTER the prompt was on screen")
    for label, el, ans in res:
        blocked = el >= 1.0
        print(f"    answer={ans}  elapsed={el:.3f}s  blocked={blocked}")
        ok = blocked
        print(f"    -> input() blocks when the buffer is empty: {_verdict(ok)}")
        failures += 0 if ok else 1
    if not res:
        print("    -> NO RESULT (child died) — inconclusive")
        failures += 1

    # ── D: cascade — the CIEN/COHR transcript ─────────────────────
    res, raw = _run_scenario("dual", b"\ny\n", "before_prompt")
    print("\n[D] cascade — one stray Enter + one stray 'y', two firms asking")
    got = {label: (el, ans) for label, el, ans in res}
    for firm in ("CIEN", "COHR"):
        el, ans = got.get(firm, (-1.0, "<NO ANSWER>"))
        print(f"    {firm}: answer={ans}  elapsed={el:.3f}s")
    ok = (
        got.get("CIEN", (0, ""))[1] in ("''", '""')
        and got.get("COHR", (0, ""))[1] in ("'y'", '"y"')
    )
    print(f"    -> firm1 aborted on a phantom 'n', firm2 continued on a")
    print(f"       stale 'y' the human meant for firm1: {_verdict(ok)}")
    failures += 0 if ok else 1

    print("\n" + "=" * 72)
    if failures == 0:
        print("ALL SCENARIOS REPRODUCED — P1 mechanism proven.")
    else:
        print(f"{failures} scenario(s) did not reproduce — see above.")
    print("=" * 72)
    return 0


# ══════════════════════════════════════════════════════════════════
# MANUAL mode — real keyboard, real terminal
# ══════════════════════════════════════════════════════════════════

def main_manual() -> None:
    from src.harness.terminal_router import register, _router

    ch = register("REPRO")
    print("\nMASH THE KEYBOARD NOW — include some Enters.")
    print(f"Nothing is reading stdin for {WINDOW + 2:.0f} seconds.")
    print("A spinner will also start, to see if it eats the prompt.\n")

    def spam() -> None:
        time.sleep(WINDOW + 2.5)
        for _ in range(8):
            _router.start_spinner("PMS2-leng-LITE")
            time.sleep(0.3)

    threading.Thread(target=spam, daemon=True).start()
    time.sleep(WINDOW + 2)

    t0 = time.monotonic()
    ans = ch.input("did you type this? [y/n]")
    el = time.monotonic() - t0

    print(f"\nanswer   = {ans!r}")
    print(f"elapsed  = {el:.3f}s")
    if el < INSTANT:
        print("VERDICT: instant return -> the answer came from the tty")
        print("         buffer, not from a decision you made at this prompt.")
    else:
        print("VERDICT: it blocked. Type-ahead not reproduced this run.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        mode = sys.argv[2] if len(sys.argv) > 2 else "single"
        {"single": _child_single,
         "dual": _child_dual,
         "spinner": _child_spinner}[mode]()
    elif len(sys.argv) > 1 and sys.argv[1] == "manual":
        main_manual()
    else:
        sys.exit(main_parent())
