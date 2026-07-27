"""00_Ingest.py — Convert raw source files to markdown.

Walks data/files_raw/ for .pdf and .docx files, converts each to
markdown via docling, writes to mirrored paths in data/files_ingested/.

Outputs:
  - data/files_ingested/<mirrored>.md   — converted markdown files
  - data/files_ingested/manifest.json   — {md_rel_path: original_ext}
  - data/files_ingested/convert_hashes.json — SHA-256 for incremental

Run: python src/scripts/PMS2/00_Ingest.py
"""

from __future__ import annotations

import hashlib
import json
import os
import warnings
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RAW_DIR = _PROJECT_ROOT / "data" / "files_raw"
_INGESTED_DIR = _PROJECT_ROOT / "data" / "files_ingested"
_MANIFEST_PATH = _INGESTED_DIR / "manifest.json"
_HASHES_PATH = _INGESTED_DIR / "convert_hashes.json"

_SUPPORTED_EXTS = {".pdf", ".docx"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_hashes() -> dict[str, str]:
    """Load existing convert_hashes.json, or empty dict if missing."""
    try:
        return json.loads(_HASHES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _find_raw_files() -> list[tuple[Path, str]]:
    """Find all supported files in files_raw/.

    Returns list of (absolute_path, relative_path_str) tuples.
    """
    files = []
    for path in sorted(_RAW_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SUPPORTED_EXTS:
            rel = str(path.relative_to(_RAW_DIR))
            files.append((path, rel))
    return files


def _md_rel_path(raw_rel: str) -> str:
    """Convert raw relative path to ingested .md relative path.

    e.g. "LITE/Company/results.pdf" → "LITE/Company/results.md"
    """
    p = Path(raw_rel)
    return str(p.with_suffix(".md"))


def run_ingest() -> None:
    """Main entry point for 00_Ingest."""
    # 1. Find all supported raw files
    raw_files = _find_raw_files()
    if not raw_files:
        print("[Chunk_master] Raw files are empty JIT!!")
        return

    print(f"[00_Ingest] Found {len(raw_files)} supported files in {_RAW_DIR}")

    # 2. Load existing hashes for incremental
    old_hashes = _load_hashes()
    new_hashes: dict[str, str] = {}

    # Ensure output dir exists
    _INGESTED_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Convert new/changed files
    converted = 0
    skipped = 0
    failed = 0

    # Docling's httpx picks up ALL_PROXY (SOCKS) which fails without
    # socksio. Temporarily suppress SOCKS proxy for docling calls.
    _saved_all_proxy = os.environ.pop("ALL_PROXY", None)
    _saved_all_proxy_lc = os.environ.pop("all_proxy", None)

    # Lazy-init converter (expensive import, do once)
    converter = None

    for abs_path, raw_rel in raw_files:
        file_hash = _sha256_file(abs_path)
        new_hashes[raw_rel] = file_hash

        md_rel = _md_rel_path(raw_rel)
        md_abs = _INGESTED_DIR / md_rel

        # Skip if hash unchanged AND output .md exists
        if raw_rel in old_hashes and old_hashes[raw_rel] == file_hash and md_abs.exists():
            skipped += 1
            continue

        # Convert
        try:
            if converter is None:
                from docling.document_converter import DocumentConverter
                converter = DocumentConverter()

            result = converter.convert(str(abs_path))
            md_text = result.document.export_to_markdown()

            # Write to mirrored path
            md_abs.parent.mkdir(parents=True, exist_ok=True)
            md_abs.write_text(md_text, encoding="utf-8")
            converted += 1
            print(f"  Converted: {raw_rel}")

        except Exception as exc:
            warnings.warn(
                f"[00_Ingest] Failed to convert {raw_rel}: {exc}",
                RuntimeWarning,
            )
            failed += 1

    # Restore proxy env vars
    if _saved_all_proxy is not None:
        os.environ["ALL_PROXY"] = _saved_all_proxy
    if _saved_all_proxy_lc is not None:
        os.environ["all_proxy"] = _saved_all_proxy_lc

    print(f"[00_Ingest] {converted} converted, {skipped} unchanged, {failed} failed")

    # 4. Build manifest from ALL raw files (not just converted)
    manifest: dict[str, str] = {}
    for _, raw_rel in raw_files:
        md_rel = _md_rel_path(raw_rel)
        ext = Path(raw_rel).suffix.lstrip(".").lower()
        manifest[md_rel] = ext

    _MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[00_Ingest] manifest.json: {len(manifest)} entries")

    # 5. Write hashes
    _HASHES_PATH.write_text(
        json.dumps(new_hashes, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[00_Ingest] convert_hashes.json written")


if __name__ == "__main__":
    run_ingest()
