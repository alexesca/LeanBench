"""Token arithmetic (build spec §8.2). Pure: no tokenizer, no I/O — just the rules
about *what counts*, applied to already-counted integers.

Normative rules encoded here:
  * count candidate output + raw source + tool-result JSON envelopes and field names +
    error strings + truncation notices;
  * do NOT count candidate-internal reads;
  * report system/task prompt separately and EXCLUDE it from the metric;
  * one tokenizer per run, identical for all candidates — cross-tokenizer comparison
    is rejected, not silently normalized;
  * count the exact serialized string handed to the model;
  * cumulative per task.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from leanbench.schemas.events import TOKEN_BUCKET_PROMPT, TOKEN_BUCKET_REPOSITORY


class CrossTokenizerComparison(ValueError):
    """Raised rather than rescaling: two runs counted with different tokenizers are not
    comparable and pretending otherwise is the whole failure mode this benchmark exists
    to avoid."""


def counted_in_metric(bucket: str, *, candidate_internal: bool = False) -> bool:
    """The single predicate deciding whether an interaction feeds the headline metric."""
    if candidate_internal:
        return False
    return bucket == TOKEN_BUCKET_REPOSITORY


def cumulative_by_task(
    entries: Iterable[Mapping[str, object]],
) -> dict[str, dict[str, int]]:
    """Fold cost-ledger-shaped rows into per-task cumulative totals.

    Each row needs `task_id`, `bucket`, `tokens`, `bytes`, `counted_in_metric`.
    """
    out: dict[str, dict[str, int]] = {}
    for entry in entries:
        task_id = str(entry.get("task_id") or "")
        bucket = str(entry.get("bucket") or TOKEN_BUCKET_REPOSITORY)
        tokens = int(entry.get("tokens") or 0)
        n_bytes = int(entry.get("bytes") or 0)
        counted = bool(entry.get("counted_in_metric"))
        row = out.setdefault(
            task_id,
            {"repository_tokens": 0, "prompt_tokens": 0, "bytes_returned": 0, "interactions": 0},
        )
        row["interactions"] += 1
        row["bytes_returned"] += n_bytes
        if counted and bucket == TOKEN_BUCKET_REPOSITORY:
            row["repository_tokens"] += tokens
        elif bucket == TOKEN_BUCKET_PROMPT:
            row["prompt_tokens"] += tokens
    return {task_id: out[task_id] for task_id in sorted(out)}


def total_repository_tokens(per_task: Mapping[str, Mapping[str, int]]) -> int:
    return sum(int(row.get("repository_tokens", 0)) for row in per_task.values())


def total_prompt_tokens(per_task: Mapping[str, Mapping[str, int]]) -> int:
    return sum(int(row.get("prompt_tokens", 0)) for row in per_task.values())


def per_tool_totals(entries: Iterable[Mapping[str, object]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in entries:
        if not entry.get("counted_in_metric"):
            continue
        tool = str(entry.get("tool") or "unknown")
        out[tool] = out.get(tool, 0) + int(entry.get("tokens") or 0)
    return dict(sorted(out.items()))


def assert_same_tokenizer(tokenizers: Sequence[str]) -> str:
    """One tokenizer per run, identical for all candidates."""
    distinct = sorted(set(tokenizers))
    if len(distinct) > 1:
        raise CrossTokenizerComparison(
            "refusing to compare token counts produced by different tokenizers: "
            + ", ".join(distinct)
        )
    return distinct[0] if distinct else ""


def label(value: float | int, *, approximate: bool, marker: str = "~") -> str:
    """Approximate figures are labelled in every report; exact ones never are."""
    return f"{marker}{value}" if approximate else f"{value}"
