#!/usr/bin/env python3
"""
app.py — Launch the PMS1 pipeline visualizer.

Usage:
    python app.py
    python app.py --port 8080 --host 0.0.0.0
    python app.py --share
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent
_psoas_root = _project_root.parent.parent.parent  # PMS1 → scripts → src → PSOAS

if str(_psoas_root) not in sys.path:
    sys.path.insert(0, str(_psoas_root))
if str(_project_root) not in sys.path:
    sys.path.insert(1, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


def main():
    parser = argparse.ArgumentParser(
        description="Launch the PMS1 pipeline visualizer."
    )
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--index-dir", type=str, default=None)
    args = parser.parse_args()

    from src.config import Config

    data_dir = Path(args.data_dir) if args.data_dir else _project_root / "data"
    index_dir = Path(args.index_dir) if args.index_dir else _project_root / "index"

    config = Config.from_env(
        data_dir=data_dir,
        index_dir=index_dir,
    )

    try:
        config.validate()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    from src.index_store import IndexManager

    print("Loading index...")
    mgr = IndexManager(config)
    index = mgr.load_or_build()
    mgr.persist()

    from src.gradio_ui import create_ui

    demo = create_ui(config, index)
    print(f"\nStarting at http://{args.host}:{args.port}")
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
    )


if __name__ == "__main__":
    main()
