"""Phase 6 gate: the agent track runs end to end, deterministically, with no LLM.

The two policies are the MockHarness of the design. They are not a model; they are two
contrasting strategies for spending a token budget -- read everything, or ask the index
first and read only what it points at. That contrast is precisely what the signature
metric exists to discriminate, so it can be measured in CI for free.
"""

from __future__ import annotations

import json

import pytest
from lb_paths import FAKE_MANIFESTS, MINI_SUITE
from leanbench.evaluator import evaluate
from leanbench.harness.agent import POLICIES, _identifiers_from


def _run(config, tmp_path, policy: str, seed: str):
    return evaluate(
        config=config,
        manifest_path=FAKE_MANIFESTS / "normal.toml",
        suite_path=MINI_SUITE,
        runs_dir=tmp_path,
        track="agent",
        run_id_seed=seed,
        agent_policy=policy,
    )


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_agent_track_runs_and_reports_the_signature_metric(config, tmp_path, policy) -> None:
    result = _run(config, tmp_path, policy, f"agent-{policy}")
    aggregate = result.metrics.agent_aggregate
    for key in (
        "correctness",
        "repository_tokens_to_correct_solution",
        "repository_context_tokens_mean",
        "effective_context_efficiency",
    ):
        assert key in aggregate, key
    assert result.metrics.agent_tasks


def test_agent_track_is_deterministic(config, tmp_path) -> None:
    """No LLM is involved, so two runs must agree exactly. If they ever stop agreeing,
    the nondeterminism is ours."""
    a = _run(config, tmp_path, "candidate-guided", "det-a")
    b = _run(config, tmp_path, "candidate-guided", "det-b")
    assert json.dumps(a.metrics.agent_aggregate, sort_keys=True) == json.dumps(
        b.metrics.agent_aggregate, sort_keys=True
    )


def test_reading_everything_costs_more_than_asking_the_index(config, tmp_path) -> None:
    """The whole thesis in one assertion."""
    read_all = _run(config, tmp_path, "read-all", "cost-read")
    guided = _run(config, tmp_path, "candidate-guided", "cost-guided")
    assert (
        guided.metrics.agent_aggregate["repository_context_tokens_mean"]
        < read_all.metrics.agent_aggregate["repository_context_tokens_mean"]
    )


def test_effective_efficiency_is_zero_when_nothing_is_answered() -> None:
    """A candidate that returns nothing must score zero, not infinity."""
    from leanbench.scoring.aggregate import effective_context_efficiency

    assert effective_context_efficiency(0.0, 1.0) == 0.0


def test_compact_results_are_parsed_like_a_model_would() -> None:
    """Candidates answer in compact form because that is the token-efficient path the
    metric measures; the policy must read it rather than demanding JSON."""
    compact = {
        "text": (
            "14.23 shopcart/checkout.py\n"
            "     module shopcart.checkout L1-80\n"
            "10.68 shopcart/inventory.py\n"
            "     method InventoryRepository.reserve L22-30"
        )
    }
    found = _identifiers_from(compact)
    assert "shopcart/checkout.py" in found
    assert "InventoryRepository.reserve" in found


def test_structured_results_still_work() -> None:
    structured = {"hits": [{"path": "a.py", "symbol": "A.b"}]}
    found = _identifiers_from(structured)
    assert found == ["A.b", "a.py"]


def test_gateway_has_no_bypass_path(config, tmp_path) -> None:
    """The agent touches the repository only through the gateway; the private
    name-mangled attributes are the mechanism that makes that structural."""
    from leanbench.gateway import ToolGateway

    gateway = ToolGateway(
        repository=object(), candidate=None, recorder=None, config=config, run_id="lb_TEST"
    )
    assert not hasattr(gateway, "repository")
    assert not hasattr(gateway, "candidate")
    exposed = [a for a in vars(gateway) if "repository" in a and not a.startswith("_ToolGateway")]
    assert exposed == []
