Extract financial data from the chunk for **{{firm}}** only. Ignore other firms.

For each cell: report value as-written (do NOT scale), denomination, and unit.

Denominations (use these exact strings): {{valid_denoms}}
- "$ in millions" or inline "$808.4 million" -> denom: "mn"
- "$ in thousands" -> denom: "k"
- "$8.2 billion" -> denom: "bn"
- "32.8%" -> denom: "%", unit: "float"
- "150 bps" -> denom: "bps", unit: "float"
- No evidence -> denom: "unknown"
- EPS / per-share values with no scale indicator -> denom: "units"

Units (use these exact strings): {{valid_units}}
- $ -> "USD", ¥ (Japanese) -> "JPY", € -> "EUR", £ -> "GBP"
- RMB/元/人民币 -> "CNY"
- Percentages, multiples, ratios -> "float"

Timeframe rules:
- quarterly = that quarter ONLY. Not annual, YTD, or LTM.
- annual = full fiscal year only.
- Wrong timeframe -> do not extract.

{{fiscal_calendar}}

Cross-lingual: match English metric names against Chinese equivalents (营业收入=Revenue, 毛利润=Gross Profit, 每股收益=EPS).

If unsure, do not extract. Misses are cheap; wrong values are expensive.
