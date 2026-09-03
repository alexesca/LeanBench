"""Phase 5 gate: every interaction is in the artifacts, and the headline number is
reconstructible from raw events alone.

This is the second ground rule made executable: if a published figure cannot be
recomputed from `events.jsonl`, it does not ship. Passing this test simultaneously
proves the event stream is complete and that the summary is *derived* rather than
independently (and possibly inconsistently) computed.
"""

from __future__ import annotations

import json

from lb_paths import FAKE_MANIFESTS, MINI_SUITE
from leanbench.artifacts import read_json, read_jsonl
from leanbench.evaluator import evaluate
from leanbench.instrumentation import reconstruct_token_usage


def _run(config, tmp_path, seed="tokens"):
    return evaluate(
        config=config,
        manifest_path=FAKE_MANIFESTS / "normal.toml",
        suite_path=MINI_SUITE,
        runs_dir=tmp_path,
        run_id_seed=seed,
    )


def test_tokens_reconstruct_exactly_from_events(config, tmp_path) -> None:
    result = _run(config, tmp_path)
    events = read_jsonl(result.run_dir, "events.jsonl")
    recorded = read_json(result.run_dir, "token-usage.json")

    rebuilt = reconstruct_token_usage(
        events,
        run_id=recorded["run_id"],
        tokenizer=recorded["tokenizer"],
        approximate=recorded["approximate"],
    ).model_dump()

    assert rebuilt["total_repository_tokens"] == recorded["total_repository_tokens"]
    assert rebuilt["total_interactions"] == recorded["total_interactions"]
    assert json.dumps(rebuilt, sort_keys=True) == json.dumps(recorded, sort_keys=True)


def test_every_candidate_call_appears_in_the_event_stream(config, tmp_path) -> None:
    result = _run(config, tmp_path, seed="events")
    events = read_jsonl(result.run_dir, "events.jsonl")
    completed = [e for e in events if e.get("kind") == "tool_completed"]
    probes = sum(len(t.probe_count * [0]) for t in result.metrics.retrieval_tasks)
    assert len(completed) == probes, (
        f"{probes} probes were issued but {len(completed)} completions were recorded"
    )


def test_event_sequence_numbers_are_unique_and_monotonic(config, tmp_path) -> None:
    result = _run(config, tmp_path, seed="seq")
    seqs = [e["seq"] for e in read_jsonl(result.run_dir, "events.jsonl")]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def test_repository_tokens_exclude_candidate_internal_reads(config) -> None:
    """Build spec §8.2: a candidate's own file reads are a system metric, never part of
    the agent's context budget. Counting them would punish a candidate for indexing."""
    from leanbench.scoring.tokens import counted_in_metric

    assert counted_in_metric("repository", candidate_internal=False) is True
    assert counted_in_metric("repository", candidate_internal=True) is False
    assert counted_in_metric("prompt", candidate_internal=False) is False


def test_tokenizer_identity_is_recorded_and_cross_tokenizer_is_rejected() -> None:
    import pytest
    from leanbench.scoring.tokens import CrossTokenizerComparison, assert_same_tokenizer

    assert assert_same_tokenizer(["approximate:4", "approximate:4"]) == "approximate:4"
    with pytest.raises(CrossTokenizerComparison):
        assert_same_tokenizer(["approximate:4", "o200k_base"])
