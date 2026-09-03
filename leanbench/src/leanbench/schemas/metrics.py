"""Metric models. Everything here is *deterministic* — no timing, no wall clock.

Latency lives in `events.jsonl` / `summary.json` on purpose: `metrics.json` must be
byte-identical across repeated runs of the same candidate (gate P4-A).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProbeMetrics(BaseModel):
    """TASKS.md §4.1, computed per probe."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    paraphrase_id: str
    op: str
    recall_at_k: dict[str, float] = Field(default_factory=dict)
    precision_at_k: dict[str, float] = Field(default_factory=dict)
    symbol_recall_at_k: dict[str, float] = Field(default_factory=dict)
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    tokens_returned: int = 0
    results_returned: int = 0
    relevant_returned: int = 0
    failed: bool = False
    classification: str | None = None


class RetrievalTaskMetrics(BaseModel):
    """Mean AND worst-case across paraphrases (TASKS.md §4.1)."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    probe_count: int
    mean: dict[str, float] = Field(default_factory=dict)
    worst: dict[str, float] = Field(default_factory=dict)
    tokens_returned_mean: float = 0.0
    tokens_returned_total: int = 0
    brittle: bool = False
    failed_probes: int = 0


class AgentTaskMetrics(BaseModel):
    """TASKS.md §4.2."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    correctness: float = 0.0
    repository_context_tokens: int = 0
    tool_calls: int = 0
    context_efficiency: float = 0.0
    effective_context_efficiency: float = 0.0
    failed: bool = False
    classification: str | None = None


class RunMetrics(BaseModel):
    """`metrics.json`. Deterministic by construction."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite: str
    candidate: str
    track: str
    tokenizer: str
    tokenizer_approximate: bool
    retrieval_tasks: list[RetrievalTaskMetrics] = Field(default_factory=list)
    agent_tasks: list[AgentTaskMetrics] = Field(default_factory=list)
    retrieval_aggregate: dict[str, float] = Field(default_factory=dict)
    agent_aggregate: dict[str, float] = Field(default_factory=dict)
    probe_metrics: list[ProbeMetrics] = Field(default_factory=list)


class TokenUsageTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    repository_tokens: int = 0
    prompt_tokens: int = 0
    bytes_returned: int = 0
    interactions: int = 0


class TokenUsage(BaseModel):
    """`token-usage.json`. Reconstructible from `events.jsonl` alone (gate P5)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    tokenizer: str
    approximate: bool
    #: The headline denominator: repository tokens only.
    total_repository_tokens: int = 0
    #: Reported separately and EXCLUDED from the metric (build spec §8.2).
    total_prompt_tokens: int = 0
    total_bytes: int = 0
    total_interactions: int = 0
    per_task: list[TokenUsageTask] = Field(default_factory=list)
    per_tool: dict[str, int] = Field(default_factory=dict)
