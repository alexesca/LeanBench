"""The whole plugin story: one dict. No entry points, no discovery, no versions."""

from __future__ import annotations

from typing import Any

from leanbench.kernel.errors import BenchmarkInfrastructureError

KINDS: tuple[str, ...] = (
    "repository",
    "candidate",
    "harness",
    "grader",
    "metric",
    "reporter",
    "mutation",
    "token_counter",
)

REGISTRY: dict[str, dict[str, type]] = {kind: {} for kind in KINDS}


def register(kind: str, name: str, impl: type) -> type:
    """Register `impl` under `kind`/`name`. Re-registering the same object is a no-op;
    re-registering a *different* object under a taken name is an error, because silent
    shadowing would make a run unexplainable."""
    if kind not in REGISTRY:
        raise BenchmarkInfrastructureError(
            f"unknown registry kind {kind!r}; known: {', '.join(KINDS)}"
        )
    existing = REGISTRY[kind].get(name)
    if existing is not None and existing is not impl:
        raise BenchmarkInfrastructureError(
            f"{kind}/{name} already registered as {existing!r}; refusing to shadow"
        )
    REGISTRY[kind][name] = impl
    return impl


def lookup(kind: str, name: str) -> type:
    if kind not in REGISTRY:
        raise BenchmarkInfrastructureError(f"unknown registry kind {kind!r}")
    impl = REGISTRY[kind].get(name)
    if impl is None:
        known = ", ".join(sorted(REGISTRY[kind])) or "<none>"
        raise BenchmarkInfrastructureError(f"no {kind} named {name!r}; known: {known}")
    return impl


def names(kind: str) -> list[str]:
    if kind not in REGISTRY:
        raise BenchmarkInfrastructureError(f"unknown registry kind {kind!r}")
    return sorted(REGISTRY[kind])


def snapshot() -> dict[str, list[str]]:
    return {kind: sorted(REGISTRY[kind]) for kind in sorted(REGISTRY)}


def clear(kind: str | None = None) -> None:
    """Test hook. Production code never calls this."""
    for k in [kind] if kind else list(REGISTRY):
        REGISTRY[k].clear()


def build(kind: str, name: str, *args: Any, **kwargs: Any) -> Any:
    return lookup(kind, name)(*args, **kwargs)
