from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.commercial_api.models import CostRecord, UsageRecord

# Pricing data is never guessed. A cost estimate is produced only when the
# operator explicitly configures a pricing file with a matching pricing key.

DEFAULT_PRICING_FILE = Path(__file__).resolve().parent / "corpus" / "pricing.example.json"


class PricingConfig:
    def __init__(self, pricing_file: Path | None):
        self._pricing: dict[str, dict[str, Any]] = {}
        self.source: str | None = None
        if pricing_file is not None:
            self._pricing = json.loads(pricing_file.read_text(encoding="utf-8")).get(
                "pricing", {}
            )
            self.source = str(pricing_file.resolve())

    def estimate(self, key: str | None, usage: UsageRecord) -> CostRecord | None:
        if not key or key not in self._pricing:
            return None
        rates = self._pricing[key]
        per_1k_input = float(rates.get("per_1k_input", 0.0))
        per_1k_output = float(rates.get("per_1k_output", 0.0))
        fixed = float(rates.get("fixed_per_transaction", 0.0))
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        transactions = usage.transactions or 0
        estimated = (
            input_tokens / 1000.0 * per_1k_input
            + output_tokens / 1000.0 * per_1k_output
            + transactions * fixed
        )
        if estimated <= 0:
            return CostRecord(estimated_usd=None, pricing_source=self.source)
        return CostRecord(
            estimated_usd=round(estimated, 6),
            pricing_source=self.source,
        )
