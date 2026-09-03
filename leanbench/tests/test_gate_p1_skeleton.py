"""Phase 1 gate: the CLI runs, and `config show` traces every value to its layer."""

from __future__ import annotations

import subprocess
import sys

from lb_paths import REPO_ROOT, SRC


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "leanbench", *args],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_help_works() -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    for command in ("evaluate", "doctor", "report", "compare", "tasks", "config"):
        assert command in result.stdout


def test_config_show_reports_precedence_source_for_every_value() -> None:
    result = _run("config", "show")
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if " = " in line]
    assert lines, "config show printed nothing"
    # Every line must name the layer the value came from; a value with no traceable
    # source is exactly the thing this gate exists to forbid.
    for line in lines:
        assert line.rstrip().endswith("]"), line
        assert any(
            layer in line for layer in ("[defaults]", "[global]", "[repo]", "[suite]", "[cli]")
        ), line


def test_cli_override_changes_the_source_layer() -> None:
    result = _run("config", "show", "--set", "retrieval.ndcg_k=5")
    assert result.returncode == 0, result.stderr
    line = next(row for row in result.stdout.splitlines() if row.startswith("retrieval.ndcg_k"))
    assert "5" in line and "[cli]" in line


def test_unknown_config_key_is_rejected_rather_than_silently_ignored(config) -> None:
    import pytest
    from leanbench.config import resolve_config
    from leanbench.kernel.errors import ConfigError

    with pytest.raises(ConfigError):
        resolve_config(cli_overrides={"retrieval.no_such_key": 1}, use_env=False)
