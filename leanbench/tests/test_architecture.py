"""Architectural invariants, enforced rather than documented.

These are cheap greps, and that is the point: an invariant nobody checks is a comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lb_paths import SRC

PACKAGE = SRC / "leanbench"


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


#: ADR-001. The benchmark treats the indexer as just another candidate; the only channel
#: between them is the wire protocol. An import here would be a privileged side channel.
FORBIDDEN_ANYWHERE = ("leanvfs", "leanbench_baselines", "docker", "pi")


def test_leanbench_never_imports_a_candidate() -> None:
    offenders = []
    for module in _modules():
        for name in _imports(module):
            root = name.split(".")[0]
            if root in FORBIDDEN_ANYWHERE:
                offenders.append(f"{module.relative_to(SRC)} imports {name}")
    assert not offenders, (
        "ADR-001 violated: the benchmark must reach a candidate only through "
        "PROTOCOL.md.\n" + "\n".join(offenders)
    )


def test_kernel_does_not_import_implementations() -> None:
    """schemas -> kernel -> ports -> implementations -> composition. The kernel sits
    above every implementation and may not reach back down into one."""
    banned = {
        "leanbench.harness",
        "leanbench.candidate",
        "leanbench.grading",
        "leanbench.evaluator",
        "leanbench.cli",
        "leanbench.repository",
        "leanbench.store",
        "leanbench.artifacts",
    }
    offenders = []
    for module in sorted((PACKAGE / "kernel").rglob("*.py")):
        for name in _imports(module):
            if any(name == b or name.startswith(b + ".") for b in banned):
                offenders.append(f"{module.name} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_scoring_is_pure_and_does_no_io() -> None:
    """The functional core: scoring, aggregation, normalization and statistics are pure
    functions over data. If any of them could touch a disk or a clock, a score would
    stop being reproducible from artifacts."""
    banned = {
        "pathlib",
        "sqlite3",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "os",
        "time",
        "datetime",
        "random",
    }
    offenders = []
    for module in sorted((PACKAGE / "scoring").rglob("*.py")):
        for name in _imports(module):
            if name.split(".")[0] in banned:
                offenders.append(f"scoring/{module.name} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_metrics_do_not_import_harness_implementations() -> None:
    offenders = []
    for module in _modules():
        rel = module.relative_to(PACKAGE).as_posix()
        if not rel.startswith(("scoring/", "schemas/")):
            continue
        for name in _imports(module):
            if name.startswith("leanbench.harness"):
                offenders.append(f"{rel} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_no_bare_except_anywhere() -> None:
    """Ground rule 3: never silently swallow. Every handled failure is classified."""
    offenders = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{module.relative_to(SRC)}:{node.lineno}")
    assert not offenders, "bare except found at: " + ", ".join(offenders)


def test_every_config_key_used_in_code_has_a_default() -> None:
    """Ground rule 4: config over constants. A key with no default is a typo waiting to
    silently do nothing, so `get` raises on unknown keys — this proves the defaults file
    actually covers what the code asks for."""
    from leanbench.config import load_defaults

    defaults = load_defaults()
    missing: list[str] = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not node.func.attr.startswith("get"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            key = node.args[0].value
            # Only flag strings that look like config keys of a known section.
            known_sections = {s.split(".")[0] for s in defaults}
            if (
                isinstance(key, str)
                and "." in key
                and key not in defaults
                and key.split(".")[0] in known_sections
            ):
                missing.append(f"{module.relative_to(SRC)}: {key}")
    assert not missing, "config keys with no default:\n" + "\n".join(sorted(set(missing)))
