"""Denomination factors and unit aliases.

Constants only. Imported by validator_loop.py.
"""

DENOM_FACTORS = {
    "units": 1,
    "k": 1_000,
    "mn": 1_000_000,
    "bn": 1_000_000_000,
    "tn": 1_000_000_000_000,
    "%": 0.01,
    "bps": 0.0001,
}

_UNIT_ALIASES = {"RMB": "CNY"}

# Safety net for non-canonical denom strings from Haiku.
# Validator prompt tells it to normalize, but ~10% of the time
# Haiku passes through raw strings like "million" or "M".
_DENOM_ALIASES = {
    "million": "mn",
    "millions": "mn",
    "M": "mn",
    "m": "mn",
    "billion": "bn",
    "billions": "bn",
    "B": "bn",
    "b": "bn",
    "thousand": "k",
    "thousands": "k",
    "K": "k",
    "trillion": "tn",
    "trillions": "tn",
    "T": "tn",
    "percent": "%",
    "percentage": "%",
    "pct": "%",
    "basis points": "bps",
    "bp": "bps",
    "per share": "units",
    "per_share": "units",
    "unit": "units",
}
