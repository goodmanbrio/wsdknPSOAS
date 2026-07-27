"""
config.py — Single source of truth for all tunable parameters.

Every component imports from here. To change behavior (chunk size, embedding
model, top_k, etc.), edit here — no hunting through multiple files.

Usage:
    from src.config import Config
    cfg = Config.from_env()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# PMS1 project root (where data/ lives)
_PMS1_ROOT = Path(__file__).resolve().parent / "Poony_Multiretrieval_S1"

# PSOAS project root (parent of src/)
_PSOAS_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Config:
    """All configuration for the query engine in one place.

    Override defaults by setting environment variables (see .env) or by
    passing keyword arguments to the constructor.
    """

    # ── Paths ───────────────────────────────────────────────────────────
    data_dir: Path = field(default_factory=lambda: _PMS1_ROOT / "data")
    index_dir: Path = field(default_factory=lambda: _PMS1_ROOT / "index")

    # ── Chunking (financial-document aware) ─────────────────────────────
    chunk_size: int = 512        # tokens per chunk — good for financial paragraphs
    chunk_overlap: int = 50      # token overlap — prevents context splitting
    min_chunk_size: int = 100    # discard chunks shorter than this

    # ── Embedding model ─────────────────────────────────────────────────
    # BGE-base: 768-dim, 110 MB. Same family as prior bge-small, 2x capacity.
    # Used by MultiFinRAG + FinAgent-RAG for financial retrieval.
    # Snowflake Arctic ruled out — custom GTE code requires xformers (CUDA-only).
    # Previous: "BAAI/bge-small-en-v1.5" (384-dim, 33 MB).
    embed_model_name: str = "BAAI/bge-base-en-v1.5"

    # ── LLM role → profile mapping ─────────────────────────────────────
    # Profile specs live in llm_profiles.yaml (model, provider, params).
    # To swap a model: change the profile name here.
    # To tweak model params: edit llm_profiles.yaml.
    llm_profiles_path: Path = field(
        default_factory=lambda: Path(__file__).parent / "llm_profiles.yaml"
    )
    sekei_profile: str = "deepseek_v4pro_highalloc"         # Sekei planning: V4 Pro, generous budget for stencil JSON
    pto_hyde_profile: str = "deepseek_v4flash_temp0"         # PTO HyDE keyword gen: V4 Flash, deterministic
    pto_judge_profile: str = "deepseek_v4pro_highalloc_temp0"  # PTO judge extraction: V4 Pro, deterministic, high alloc
    # ── Harness LLM role → profile mapping ─────────────────────────────
    orchestrator_profile: str = "deepseek_v4pro_orchestrator"  # Agent loop: V4 Pro, tool calling
    pteca_profile: str = "deepseek_v4pro_pteca"                # PTECA chart planner: V4 Pro, tool calling

    # ── PUMBA (LLM-reasoned chunk retrieval fallback) ─────────────────
    pumba_dailo_profile: str = "deepseek_v4pro_highalloc_temp0"  # Dailo: V4 Pro, deterministic extraction
    pumba_gulei_profile: str = "deepseek_v4pro_med"              # Gulei: V4 Pro, chunk selection
    pumba_leng_profile: str = "deepseek_v4flash_leng"            # Leng screening: V4 Flash, cheapest pass

    # ── PMS2 LLM role → profile mapping ──────────────────────────────
    # ── PMS2 data paths ──────────────────────────────────────────────
    pms2_raw_dir: Path = field(default_factory=lambda: _PSOAS_ROOT / "data" / "files_raw")
    pms2_data_dir: Path = field(default_factory=lambda: _PSOAS_ROOT / "data" / "files_ingested")
    pms2_index_dir: Path = field(default_factory=lambda: _PSOAS_ROOT / "data" / "index")

    pms2_sekei_profile: str = "anthropic_opushighthink"       # Opus 4.6 + 10k thinking: metric decomposition + stencil JSON
    pms2_mapper_profile: str = "anthropic_hayasui"            # Haiku 4.5: dir nav, low stakes
    pms2_fiscal_cal_profile: str = "anthropic_hayasui"        # Haiku: FY end lookup via web_search
    pms2_batch_planner_profile: str = "anthropic_sonnetmedthink"  # Sonnet 4.6 + 5k thinking: file routing reasoning
    pms2_leng_profile: str = "anthropic_hayasui"              # Haiku: cheap extraction
    pms2_validator_profile: str = "anthropic_hayasui"          # Haiku: cheap verification
    pms2_leng_max_workers: int = 100                           # concurrent Leng structured_complete calls per firm
    pms2_validator_max_workers: int = 50                       # concurrent Validator agent loops per firm

    # ── Chart output ──────────────────────────────────────────────────
    output_dir: Path = field(default_factory=lambda: _PMS1_ROOT / "output")
    # chart_styles: list[str] = ...  # Future: override LovelyPlots styles

    # ── Server ──────────────────────────────────────────────────────────
    gradio_host: str = "127.0.0.1"
    gradio_port: int = 7860

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        """Create a Config from optional overrides.

        API keys are resolved per-provider at LLM construction time
        (see PROVIDER_CONFIG in llm.py). No key wiring needed here.
        """
        return cls(**overrides)

    def get_llm_profile(self, name: str) -> dict:
        """Load a named profile from llm_profiles.yaml."""
        with open(self.llm_profiles_path) as f:
            profiles = yaml.safe_load(f)
        if name not in profiles:
            raise ValueError(
                f"LLM profile '{name}' not found. "
                f"Available: {list(profiles.keys())}"
            )
        return profiles[name]

    def validate(self) -> None:
        """Raise ValueError if required settings are missing."""
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_dir.exists():
            self.index_dir.mkdir(parents=True, exist_ok=True)
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.llm_profiles_path.exists():
            raise ValueError(
                f"LLM profiles file not found: {self.llm_profiles_path}"
            )


# ── Firm synonym groups ─────────────────────────────────────────────────────
# Loaded from hardcode_dependencies/firm_synonyms.json.
# Key = readable firm name (also used as dir_implied_firm on chunks).
# Value = all equivalent strings (tickers, legal names, filename prefixes).
# To add a company: edit firm_synonyms.json, not this file.
_FIRM_SYNONYMS_PATH = (
    _PMS1_ROOT / "hardcode_dependencies" / "firm_synonyms.json"
)
with open(_FIRM_SYNONYMS_PATH) as _f:
    FIRM_SYNONYMS: dict[str, list[str]] = json.load(_f)
