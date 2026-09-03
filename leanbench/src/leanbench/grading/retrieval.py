"""Retrieval grader. Knows gold and TASKS.md §4.1; knows nothing about candidates —
it is handed already-reduced observations and never touches a subprocess or a socket.
"""

from __future__ import annotations

from typing import Any

from leanbench.kernel.registry import register
from leanbench.schemas.config import ResolvedConfig
from leanbench.schemas.metrics import ProbeMetrics, RetrievalTaskMetrics
from leanbench.schemas.task import Task
from leanbench.scoring.aggregate import aggregate_probes, brittleness
from leanbench.scoring.retrieval import build_gold_set, results_from_op, score_probe


class RetrievalGrader:
    def __init__(self, config: ResolvedConfig) -> None:
        self.recall_ks = tuple(int(k) for k in sorted(config.get_list("retrieval.recall_k")))
        self.precision_ks = tuple(int(k) for k in sorted(config.get_list("retrieval.precision_k")))
        self.ndcg_k = config.get_int("retrieval.ndcg_k")
        self.precision = config.get_int("report.float_precision")
        self.brittleness_gap = config.get_float("retrieval.brittleness_gap")
        self.primary_metric = config.get_str("retrieval.primary_metric")

    def grade(self, task: Task, observations: dict[str, Any]) -> dict[str, Any]:
        """`observations["probes"]` is a list of
        `{paraphrase_id, op, result, tokens_returned, failed, classification}`."""
        gold = build_gold_set(
            symbols=task.gold.symbols,
            files=task.gold.files,
            tests=task.gold.tests,
            docs=task.gold.docs,
            ranges=[(r.path, r.start, r.end) for r in task.gold.ranges],
        )
        scored: list[tuple[tuple[str, str], ProbeMetrics, dict[str, float]]] = []
        for probe in observations.get("probes", []):
            op = probe.get("op", "search")
            failed = bool(probe.get("failed"))
            results = [] if failed else results_from_op(op, probe.get("result") or {})
            score = score_probe(
                results,
                gold,
                recall_ks=self.recall_ks,
                precision_ks=self.precision_ks,
                ndcg_k=self.ndcg_k,
            )
            rounded = {k: round(v, self.precision) for k, v in score.metrics.items()}
            metrics = ProbeMetrics(
                task_id=task.id,
                paraphrase_id=probe.get("paraphrase_id", ""),
                op=op,
                recall_at_k={k: round(v, self.precision) for k, v in score.recall_at_k.items()},
                precision_at_k={
                    k: round(v, self.precision) for k, v in score.precision_at_k.items()
                },
                symbol_recall_at_k={
                    k: round(v, self.precision) for k, v in score.symbol_recall_at_k.items()
                },
                mrr=rounded["mrr"],
                ndcg_at_10=rounded[f"ndcg_at_{self.ndcg_k}"],
                tokens_returned=int(probe.get("tokens_returned", 0)),
                results_returned=score.results_returned,
                relevant_returned=score.relevant_returned,
                failed=failed,
                classification=probe.get("classification"),
            )
            scored.append(((metrics.paraphrase_id, metrics.op), metrics, rounded))

        # Sorted by (paraphrase_id, op): probe order must never depend on arrival order.
        scored.sort(key=lambda row: row[0])
        probe_metrics = [row[1] for row in scored]
        means, worst = aggregate_probes([row[2] for row in scored], precision=self.precision)
        tokens = [p.tokens_returned for p in probe_metrics]
        task_metrics = RetrievalTaskMetrics(
            task_id=task.id,
            probe_count=len(probe_metrics),
            mean=means,
            worst=worst,
            tokens_returned_mean=round(sum(tokens) / len(tokens), self.precision)
            if tokens
            else 0.0,
            tokens_returned_total=sum(tokens),
            brittle=brittleness(means, worst, metric=self.primary_metric, gap=self.brittleness_gap),
            failed_probes=sum(1 for p in probe_metrics if p.failed),
        )
        return {"probe_metrics": probe_metrics, "task_metrics": task_metrics}


register("grader", "retrieval", RetrievalGrader)
