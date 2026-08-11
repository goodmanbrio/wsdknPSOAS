"""
sysprompts.py — Load externalized system prompts from .md files.

Lookup key is (role, profile_name) → sysprompts/{role}/{profile_name}.md.
Template vars use {{placeholder}} syntax, resolved via str.replace().

Usage:
    from src.harness.sysprompts import load_sysprompt
    prompt = load_sysprompt("pto_judge", "deepseek_v4pro_highalloc_temp0",
                            denomination_values=denom_str,
                            unit_values=unit_str)
"""

import re
from pathlib import Path

SYSPROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "sysprompts"


def load_sysprompt(role: str, profile: str, **template_vars) -> str:
    """Load and resolve a system prompt .md file.

    Raises:
        FileNotFoundError: if sysprompts/{role}/{profile}.md missing
        ValueError: if unresolved {{...}} vars remain after substitution
    """
    path = SYSPROMPTS_DIR / role / f"{profile}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No sysprompt: {path}\n"
            f"Create sysprompts/{role}/{profile}.md or change profile"
        )
    text = path.read_text()
    for key, value in template_vars.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    # Crash on unresolved vars
    unresolved = re.findall(r"\{\{(\w+)\}\}", text)
    if unresolved:
        raise ValueError(
            f"Unresolved template vars in {path}: {unresolved}"
        )
    return text
