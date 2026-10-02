"""Registro costi: una riga JSON per chiamata in <corso>/.sbob/costs.jsonl, più riepilogo per il report."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from sbob.llm.base import LLMResult


@dataclass
class Prices:
    """USD per milione di token. Se mancano in config il costo resta 0 ma i token vengono contati."""
    input: float = 0.0
    output: float = 0.0
    cached_input: float | None = None

    def cost(self, r: LLMResult) -> float:
        u = r.usage
        cached_price = self.input if self.cached_input is None else self.cached_input
        fresh_in = max(u.input_tokens - u.cached_tokens, 0)
        return (fresh_in * self.input + u.cached_tokens * cached_price
                + (u.output_tokens + u.thinking_tokens) * self.output) / 1_000_000


@dataclass
class CostTracker:
    log_path: Path | None = None
    totals: dict[str, dict[str, float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, role: str, result: LLMResult, prices: Prices, item: str | None = None) -> None:
        usd = prices.cost(result)
        u = result.usage
        with self._lock:
            t = self.totals.setdefault(f"{role}:{result.provider}/{result.model}",
                                       {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
            t["calls"] += 1
            t["input_tokens"] += u.input_tokens
            t["output_tokens"] += u.output_tokens + u.thinking_tokens
            t["usd"] += usd
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "item": item,
                        "provider": result.provider, "model": result.model,
                        "in": u.input_tokens, "out": u.output_tokens, "cached": u.cached_tokens,
                        "thinking": u.thinking_tokens, "usd": round(usd, 6),
                        "error": result.error_kind,
                        **({"msg": (result.error or "")[:300]} if result.error_kind else {}),
                    }, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        return {"per_modello": self.totals,
                "usd_totale": round(sum(t["usd"] for t in self.totals.values()), 4)}
