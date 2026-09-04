"""Phase 8 gate: a known effect is detected, a known non-effect is not.

Both directions matter equally. A statistics layer that never says "no conclusion" is
just an opinion generator with decimal places.
"""

from __future__ import annotations

import pytest
from leanbench.noise import (
    STABLE,
    UNUSABLE,
    cliffs_delta,
    dispersion,
    effect_magnitude,
    gate_raw_vs_semantic,
    minimum_detectable_effect,
    profile_key,
    separation_matrix,
)

BANDS = {"noisy_above": 0.05, "unusable_above": 0.20}


def test_zero_variance_is_stable_with_zero_mde() -> None:
    """The retrieval track must land here; anything else is our own nondeterminism."""
    result = dispersion("m", [0.5] * 10, **BANDS)
    assert result.stdev == 0.0
    assert result.cv == 0.0
    assert result.classification == STABLE
    assert all(v == 0.0 for v in result.mde_at.values())


def test_wild_variance_is_unusable() -> None:
    assert dispersion("m", [0.1, 0.9, 0.2, 0.8, 0.5], **BANDS).classification == UNUSABLE


def test_mde_shrinks_with_more_repetitions() -> None:
    result = dispersion("m", [0.5, 0.6, 0.4, 0.55, 0.45], **BANDS)
    assert result.mde_at[1] > result.mde_at[3] > result.mde_at[10]


def test_mde_is_the_documented_formula() -> None:
    # (z_alpha + z_power) * sd / sqrt(n), two-sided alpha=0.05, 80% power.
    assert minimum_detectable_effect(1.0, 4) == pytest.approx(2.8016 / 2, rel=1e-3)


def test_a_ten_percent_regression_is_a_large_effect() -> None:
    """Inject a known regression: it must register as detectable, not as noise."""
    before = [100.0 + i for i in range(20)]
    after = [x * 1.10 for x in before]
    delta = cliffs_delta(after, before)
    assert delta > 0.474
    assert effect_magnitude(delta) == "large"


def test_a_half_percent_change_is_not_a_large_effect() -> None:
    """The mirror image: a change this small must never be reported as an improvement."""
    before = [100.0 + i for i in range(20)]
    after = [x * 1.005 for x in before]
    assert effect_magnitude(cliffs_delta(after, before)) in {"negligible", "small"}


def test_identical_distributions_have_zero_effect() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert cliffs_delta(values, list(values)) == 0.0
    assert effect_magnitude(0.0) == "negligible"


def test_separation_matrix_is_antisymmetric_and_total() -> None:
    scores = {
        "raw": {"t1": 0.1, "t2": 0.2, "t3": 0.15},
        "ast": {"t1": 0.8, "t2": 0.9, "t3": 0.85},
        "mid": {"t1": 0.4, "t2": 0.5, "t3": 0.45},
    }
    matrix = separation_matrix(scores)
    assert matrix["candidates"] == ["ast", "mid", "raw"]
    for left in matrix["candidates"]:
        for right in matrix["candidates"]:
            if left != right:
                assert matrix["delta"][left][right] == pytest.approx(
                    -matrix["delta"][right][left]
                )


def test_discrimination_gate_fails_when_baselines_are_indistinguishable() -> None:
    """The Type II guard: if raw file reading and an AST index score the same, the suite
    is measuring the agent rather than the candidate, and must refuse to certify."""
    same = {"raw": {"t1": 0.5, "t2": 0.5}, "ast": {"t1": 0.5, "t2": 0.5}}
    gate = gate_raw_vs_semantic(
        separation_matrix(same), raw="raw", semantic="ast", threshold=0.474
    )
    assert gate["passed"] is False
    assert gate["magnitude"] == "negligible"


def test_discrimination_gate_passes_on_clear_separation() -> None:
    apart = {"raw": {"t1": 0.1, "t2": 0.2}, "ast": {"t1": 0.9, "t2": 0.95}}
    gate = gate_raw_vs_semantic(
        separation_matrix(apart), raw="raw", semantic="ast", threshold=0.474
    )
    assert gate["passed"] is True
    assert abs(gate["delta"]) == pytest.approx(1.0)


def test_profile_key_is_stable_and_configuration_sensitive() -> None:
    base = {"suite": "httpx", "harness": "retrieval", "model": "none", "model_settings": "d"}
    assert profile_key(**base) == profile_key(**base)
    assert profile_key(**{**base, "model": "other"}) != profile_key(**base)
