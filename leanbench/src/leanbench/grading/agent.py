"""Agent-track structural grader (TASKS.md §4.2).

`correctness` is the weighted fraction of gold elements the answer demonstrably
identified, where "demonstrably" means the element appears in the answer text under the
§4.1 normalization, or the agent read the exact gold range.
"""

from __future__ import annotations

import re
from typing import Any

from leanbench.kernel.registry import register
from leanbench.schemas.config import ResolvedConfig
from leanbench.schemas.metrics import AgentTaskMetrics
from leanbench.schemas.task import Task
from leanbench.scoring.aggregate import context_efficiency, effective_context_efficiency
from leanbench.scoring.normalize import normalize_path, symbol_matches

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./\\-]*")


def _mentions_symbol(answer_tokens: list[str], gold_symbol: str) -> bool:
    return any(symbol_matches(gold_symbol, token) for token in answer_tokens)


def _mentions_path(answer_tokens: list[str], gold_path: str) -> bool:
    normalized = normalize_path(gold_path)
    for token in answer_tokens:
        candidate = normalize_path(token)
        if candidate == normalized or candidate.endswith("/" + normalized):
            return True
    return False


def _range_read(read_ranges: list[tuple[str, int, int]], path: str, start: int, end: int) -> bool:
    target = normalize_path(path)
    return any(normalize_path(p) == target and s <= start and e >= end for p, s, e in read_ranges)


class AgentGrader:
    def __init__(self, config: ResolvedConfig) -> None:
        self.weights = {
            "symbols": config.get_float("agent.weight_symbols"),
            "files": config.get_float("agent.weight_files"),
            "tests": config.get_float("agent.weight_tests"),
            "relationships": config.get_float("agent.weight_relationships"),
        }
        self.precision = config.get_int("report.float_precision")

    def correctness(
        self, task: Task, answer: str, read_ranges: list[tuple[str, int, int]]
    ) -> float:
        tokens = TOKEN_RE.findall(answer or "")
        found: dict[str, float] = {}
        categories: dict[str, list[bool]] = {}

        if task.gold.symbols:
            categories["symbols"] = [_mentions_symbol(tokens, s) for s in task.gold.symbols]
        if task.gold.files:
            hits = []
            for path in task.gold.files:
                mentioned = _mentions_path(tokens, path)
                if not mentioned:
                    mentioned = any(
                        _range_read(read_ranges, r.path, r.start, r.end)
                        for r in task.gold.ranges
                        if normalize_path(r.path) == normalize_path(path)
                    )
                hits.append(mentioned)
            categories["files"] = hits
        if task.gold.tests:
            categories["tests"] = [_mentions_path(tokens, p) for p in task.gold.tests]
        if task.gold.relationships:
            categories["relationships"] = [
                _mentions_symbol(tokens, rel[0]) and _mentions_symbol(tokens, rel[2])
                for rel in task.gold.relationships
                if len(rel) == 3
            ]

        if not categories:
            return 0.0
        total_weight = sum(self.weights[name] for name in categories)
        if total_weight <= 0:
            return 0.0
        for name, hits in categories.items():
            found[name] = (sum(1 for h in hits if h) / len(hits)) if hits else 0.0
        score = sum(self.weights[name] * found[name] for name in categories) / total_weight
        return round(score, self.precision)

    def grade(self, task: Task, observations: dict[str, Any]) -> dict[str, Any]:
        answer = str(observations.get("answer", ""))
        read_ranges = [tuple(r) for r in observations.get("read_ranges", [])]
        correctness = self.correctness(task, answer, read_ranges)  # type: ignore[arg-type]
        tokens = int(observations.get("repository_context_tokens", 0))
        baseline = int(observations.get("baseline_tokens", 0))
        efficiency = round(context_efficiency(baseline, tokens), self.precision)
        return {
            "task_metrics": AgentTaskMetrics(
                task_id=task.id,
                correctness=correctness,
                repository_context_tokens=tokens,
                tool_calls=int(observations.get("tool_calls", 0)),
                context_efficiency=efficiency,
                effective_context_efficiency=round(
                    effective_context_efficiency(correctness, efficiency), self.precision
                ),
                failed=bool(observations.get("failed")),
                classification=observations.get("classification"),
            )
        }


register("grader", "agent", AgentGrader)
