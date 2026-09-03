"""The priority ladder and the budget admission engine.

This is where the product thesis becomes an algorithm. The drop record is not
optional: "you spent 34% of the file budget on keywords and dropped 12 test
expectations" is precisely the signal the benchmark optimizes against.

The token counter is a fast approximation calibrated per language and is used ONLY
for budgeting. It is always reported as approximate; LeanBench's tokenizer is
authoritative for scoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

PRIORITY_LADDER = {
    0: "P0 signatures/types/locations/public API/schema/security+business invariants/"
    "test-enforced behavior",
    1: "P1 important calls/side effects/exceptions/module docs/routes/architecture "
    "decisions/E2E behavior",
    2: "P2 imports/keywords/fixtures/mocks/useful docs/rationale comments",
    3: "P3 ordinary comments/secondary metadata",
    4: "P4 boilerplate/noise",
}


class TokenCounter:
    """Approximate, deterministic, calibrated per language."""

    def __init__(self, cfg: Any) -> None:
        self.per_char = float(cfg.get("budget.tokens_per_char", 0.27))
        self.floor = int(cfg.get("budget.token_floor", 1))

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(self.floor, int(len(text) * self.per_char) + 1)

    def count_lines(self, lines: Sequence[str]) -> int:
        return sum(self.count(line) + 1 for line in lines)


@dataclass
class BudgetItem:
    kind: str
    priority: int
    confidence: float
    value: str
    tokens: int = 0
    payload: Any = None

    def sort_key(self) -> tuple[int, float, str, str]:
        # priority asc, confidence desc, kind asc, value asc -> a total order
        return (self.priority, -self.confidence, self.kind, self.value)


@dataclass
class BudgetReport:
    budget: int
    admitted: int = 0
    tokens_approx: int = 0
    truncated: bool = False
    dropped: dict[str, int] = field(default_factory=dict)
    dropped_tokens: dict[str, int] = field(default_factory=dict)
    dropped_by_priority: dict[int, int] = field(default_factory=dict)
    spend_by_kind: dict[str, int] = field(default_factory=dict)
    total_candidates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "admitted": self.admitted,
            "tokens_approx": self.tokens_approx,
            "approximate": True,
            "truncated": self.truncated,
            "dropped": dict(sorted(self.dropped.items())),
            "dropped_tokens_would_have_cost": dict(sorted(self.dropped_tokens.items())),
            "dropped_by_priority": {str(k): v for k, v in sorted(self.dropped_by_priority.items())},
            "spend_by_kind": dict(sorted(self.spend_by_kind.items())),
            "total_candidates": self.total_candidates,
        }

    def accounts_for_all(self) -> bool:
        return self.admitted + sum(self.dropped.values()) == self.total_candidates


def admit(
    items: Sequence[BudgetItem],
    budget: int,
    per_kind_caps: dict[str, int] | None = None,
    counter: TokenCounter | None = None,
) -> tuple[list[BudgetItem], BudgetReport]:
    """The deterministic admission algorithm.

    1. sort by (priority asc, confidence desc, kind asc, value asc)
    2. greedily admit while cumulative_tokens + cost(item) <= budget
    3. within a kind, respect per-kind caps BEFORE the global budget
    4. record every dropped item as (kind, priority, count, tokens_would_have_cost)
    5. emit a budget_report alongside the payload
    """
    caps = per_kind_caps or {}
    report = BudgetReport(budget=budget, total_candidates=len(items))
    ordered = sorted(items, key=BudgetItem.sort_key)
    admitted: list[BudgetItem] = []
    used = 0
    per_kind_used: dict[str, int] = {}

    for item in ordered:
        cost = item.tokens if item.tokens else (counter.count(item.value) if counter else 1)
        cap = caps.get(item.kind)
        if cap is not None and per_kind_used.get(item.kind, 0) >= cap:
            _drop(report, item, cost)
            continue
        if budget >= 0 and used + cost > budget:
            _drop(report, item, cost)
            continue
        item.tokens = cost
        admitted.append(item)
        used += cost
        per_kind_used[item.kind] = per_kind_used.get(item.kind, 0) + 1
        report.spend_by_kind[item.kind] = report.spend_by_kind.get(item.kind, 0) + cost

    report.admitted = len(admitted)
    report.tokens_approx = used
    report.truncated = bool(report.dropped)
    return admitted, report


def _drop(report: BudgetReport, item: BudgetItem, cost: int) -> None:
    report.dropped[item.kind] = report.dropped.get(item.kind, 0) + 1
    report.dropped_tokens[item.kind] = report.dropped_tokens.get(item.kind, 0) + cost
    report.dropped_by_priority[item.priority] = report.dropped_by_priority.get(item.priority, 0) + 1


def context_order(cfg: Any) -> list[str]:
    """Assembly order for get_context is CONFIG, not code."""
    order = cfg.get("budget.context_order.order")
    if isinstance(order, list):
        return [str(x) for x in order]
    return [
        "exception",
        "side_effect",
        "invariant",
        "security_note",
        "test_expectation",
        "call",
        "documentation",
        "keyword",
        "type_use",
    ]


def per_kind_caps(cfg: Any) -> dict[str, int]:
    return {k: int(v) for k, v in (cfg.section("budget.per_kind_caps") or {}).items()}
