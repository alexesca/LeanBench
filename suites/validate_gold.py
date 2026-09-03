#!/usr/bin/env python3
"""Standalone gold validator for LeanBench task suites.

Checks every `suites/<suite>/tasks/*.toml` against the normative contract in
`TASKS.md`, and verifies that every gold reference actually resolves in the
pinned corpus checkout.

Depends only on the standard library (`tomllib` on 3.11+, `tomli` otherwise).
It deliberately does NOT import `leanbench`, so it can run before/independently
of the harness.

Usage:
    python3 suites/validate_gold.py [suite ...]

Exits 0 if clean, 1 if any violation was found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        sys.stderr.write(
            "validate_gold.py needs tomllib (Python 3.11+) or the 'tomli' package.\n"
        )
        raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITES_DIR = REPO_ROOT / "suites"
CORPUS_DIR = REPO_ROOT / "corpus"

CATEGORIES = {
    "symbol_location",
    "api_contract",
    "architecture",
    "behavior",
    "tests",
    "documentation",
    "comments",
    "configuration",
    "change_impact",
    "incremental",
}
DIFFICULTIES = {"L1", "L2", "L3", "L4", "L5"}
PARAPHRASE_IDS = {"literal", "intent", "symptom"}
CAPABILITIES = {
    "search",
    "symbols",
    "context",
    "dependencies",
    "references",
    "tests",
    "docs",
    "incremental",
}
OPS = {
    "prepare_repository",
    "search",
    "get_symbol",
    "get_context",
    "get_dependencies",
    "get_references",
    "get_tests",
    "get_docs",
    "update_repository",
    "get_stats",
}

REQUIRED_TOP_LEVEL = [
    "id",
    "version",
    "repository",
    "commit",
    "category",
    "difficulty",
    "authored_by",
    "reviewed_by",
    "authored_at",
    "prompt",
    "required_capabilities",
]

DEF_RE = "^[ \t]*(?:async[ \t]+)?def[ \t]+{name}\\b"
CLASS_RE = "^[ \t]*class[ \t]+{name}\\b"
ASSIGN_RE = "^[ \t]*{name}[ \t]*(?::[^=\n]+)?=[^=]"
CONSTRUCT_LINE_RE = re.compile(
    r"^[ \t]*(?:@|(?:async[ \t]+)?def[ \t]|class[ \t]|[A-Za-z_][A-Za-z_0-9]*[ \t]*(?::[^=\n]+)?=[^=])"
)


class Report:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.checked = 0

    def bad(self, where: str, message: str) -> None:
        self.violations.append(f"{where}: {message}")


_file_cache: dict[Path, list[str]] = {}


def read_lines(path: Path) -> list[str]:
    if path not in _file_cache:
        _file_cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return _file_cache[path]


def corpus_root(repository: str) -> Path:
    return CORPUS_DIR / repository


def head_commit(repo_dir: Path) -> str | None:
    head = repo_dir / ".git" / "HEAD"
    if not head.exists():
        return None
    raw = head.read_text(encoding="utf-8").strip()
    if raw.startswith("ref:"):
        ref = (repo_dir / ".git" / raw.split(" ", 1)[1].strip()).resolve()
        if ref.exists():
            return ref.read_text(encoding="utf-8").strip()
        packed = repo_dir / ".git" / "packed-refs"
        if packed.exists():
            want = raw.split(" ", 1)[1].strip()
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + want):
                    return line.split(" ", 1)[0]
        return None
    return raw


def symbol_defined_in(lines: list[str], name: str) -> bool:
    """True if `name` is defined in `lines` as a def/class/assignment."""
    escaped = re.escape(name)
    for pattern in (DEF_RE, CLASS_RE, ASSIGN_RE):
        rx = re.compile(pattern.format(name=escaped), re.MULTILINE)
        for line in lines:
            if rx.match(line):
                return True
    return False


def check_range(rep: Report, where: str, root: Path, path: str, start: int, end: int,
                allowed_paths: set[str]) -> None:
    if path not in allowed_paths:
        rep.bad(where, f"range path {path!r} is not listed in gold files/tests/docs")
    target = root / path
    if not target.exists():
        rep.bad(where, f"range path {path!r} does not exist in the corpus")
        return
    lines = read_lines(target)
    if start < 1 or end < start:
        rep.bad(where, f"range {path}:{start}-{end} is malformed")
        return
    if end > len(lines):
        rep.bad(where, f"range {path}:{start}-{end} exceeds file length ({len(lines)})")
        return
    if not path.endswith(".py"):
        return
    window = lines[start - 1: end]
    if any(CONSTRUCT_LINE_RE.match(line) for line in window):
        return
    if start == 1:
        return  # module header / docstring
    first = next((line.strip() for line in window if line.strip()), "")
    if first.startswith(('"""', "'''", "#")):
        return
    rep.bad(
        where,
        f"range {path}:{start}-{end} does not bracket a plausible construct "
        f"(no def/class/assignment/comment start inside it)",
    )


def check_task(rep: Report, suite: str, task_path: Path) -> None:
    where = f"{suite}/{task_path.name}"
    try:
        data = tomllib.loads(task_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.bad(where, f"is not valid TOML: {exc}")
        return
    rep.checked += 1

    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            rep.bad(where, f"missing required field {key!r}")

    tid = data.get("id", "")
    if tid != task_path.stem:
        rep.bad(where, f"id {tid!r} does not match filename stem {task_path.stem!r}")
    if data.get("category") not in CATEGORIES:
        rep.bad(where, f"category {data.get('category')!r} is not a known category")
    if data.get("difficulty") not in DIFFICULTIES:
        rep.bad(where, f"difficulty {data.get('difficulty')!r} is not L1..L5")
    if not str(data.get("prompt", "")).strip():
        rep.bad(where, "prompt is empty")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("authored_at", ""))):
        rep.bad(where, f"authored_at {data.get('authored_at')!r} is not YYYY-MM-DD")
    if "reviewed_by" not in data:
        rep.bad(where, "reviewed_by must be present (empty string until reviewed)")
    if not isinstance(data.get("version"), int):
        rep.bad(where, "version must be an integer")

    caps = data.get("required_capabilities", [])
    if not isinstance(caps, list) or not caps:
        rep.bad(where, "required_capabilities must be a non-empty array")
    else:
        unknown = sorted(set(caps) - CAPABILITIES)
        if unknown:
            rep.bad(where, f"unknown required_capabilities: {unknown}")

    limits = data.get("limits", {})
    if not isinstance(limits, dict) or not limits:
        rep.bad(where, "missing [limits] table")
    else:
        for key in ("max_tool_calls", "max_repository_tokens", "wall_clock_s"):
            value = limits.get(key)
            if not isinstance(value, (int, float)) or value <= 0:
                rep.bad(where, f"limits.{key} must be a positive number")

    # --- probes -----------------------------------------------------------
    probes = data.get("probes", [])
    if not isinstance(probes, list) or len(probes) < 3:
        rep.bad(where, f"needs >= 3 probes, found {len(probes) if isinstance(probes, list) else 0}")
        probes = probes if isinstance(probes, list) else []
    seen_ids = set()
    for i, probe in enumerate(probes):
        pid = probe.get("paraphrase_id")
        if pid not in PARAPHRASE_IDS:
            rep.bad(where, f"probe[{i}] paraphrase_id {pid!r} not in {sorted(PARAPHRASE_IDS)}")
        else:
            seen_ids.add(pid)
        if probe.get("op") not in OPS:
            rep.bad(where, f"probe[{i}] op {probe.get('op')!r} is not a protocol operation")
        if not isinstance(probe.get("args"), dict):
            rep.bad(where, f"probe[{i}] args must be a table")
    if len(seen_ids) < 3:
        rep.bad(where, f"needs >= 3 distinct paraphrase_id values, found {sorted(seen_ids)}")

    # --- corpus resolution -------------------------------------------------
    repository = data.get("repository", "")
    root = corpus_root(repository)
    if not root.is_dir():
        rep.bad(where, f"repository {repository!r} has no corpus checkout at {root}")
        return
    actual = head_commit(root)
    if actual is not None and data.get("commit") and actual != data.get("commit"):
        rep.bad(
            where,
            f"commit {data['commit'][:12]} does not match corpus HEAD {actual[:12]}",
        )

    # --- gold --------------------------------------------------------------
    gold = data.get("gold")
    if not isinstance(gold, dict):
        rep.bad(where, "missing [gold] table")
        return
    files = gold.get("files", []) or []
    symbols = gold.get("symbols", []) or []
    tests = gold.get("tests", []) or []
    docs = gold.get("docs", []) or []
    if not files and not symbols:
        rep.bad(where, "gold must have at least one of files/symbols")
    if not str(gold.get("justification", "")).strip():
        rep.bad(where, "gold.justification is required and must be non-empty")

    path_lists = {"files": files, "tests": tests, "docs": docs}
    existing: list[Path] = []
    for label, entries in path_lists.items():
        for entry in entries:
            rel = str(entry).split("::", 1)[0]
            if rel.startswith("./") or rel.startswith("/") or "\\" in rel:
                rep.bad(where, f"gold.{label} entry {entry!r} is not a clean repo-relative POSIX path")
            target = root / rel
            if not target.exists():
                rep.bad(where, f"gold.{label} entry {entry!r} does not exist at the pinned commit")
            elif label == "files":
                existing.append(target)

    py_files = [p for p in existing if p.suffix == ".py"]
    for symbol in symbols:
        leaf = str(symbol).split(".")[-1]
        parent = str(symbol).split(".")[-2] if "." in str(symbol) else None
        if not py_files:
            rep.bad(where, f"gold.symbols has {symbol!r} but no python file is listed in gold.files")
            continue
        if not any(symbol_defined_in(read_lines(p), leaf) for p in py_files):
            rep.bad(
                where,
                f"gold symbol {symbol!r} is not defined in any listed gold file "
                f"({', '.join(str(p.relative_to(root)) for p in py_files)})",
            )
            continue
        if parent and parent[:1].isupper():
            if not any(symbol_defined_in(read_lines(p), parent) for p in py_files):
                rep.bad(where, f"gold symbol {symbol!r}: owning class {parent!r} not found in gold files")

    allowed_paths = {str(p).split("::", 1)[0] for group in path_lists.values() for p in group}
    for entry in gold.get("ranges", []) or []:
        if not isinstance(entry, dict) or not {"path", "start", "end"} <= set(entry):
            rep.bad(where, f"malformed range entry {entry!r}")
            continue
        check_range(rep, where, root, str(entry["path"]), int(entry["start"]),
                    int(entry["end"]), allowed_paths)

    for rel in gold.get("relationships", []) or []:
        if not isinstance(rel, list) or len(rel) != 3 or not all(isinstance(x, str) for x in rel):
            rep.bad(where, f"relationship {rel!r} must be a [source, KIND, target] triple of strings")
        elif not rel[1].isupper():
            rep.bad(where, f"relationship kind {rel[1]!r} should be upper case")


def check_suite(rep: Report, suite_dir: Path) -> None:
    suite = suite_dir.name
    tasks_dir = suite_dir / "tasks"
    if not tasks_dir.is_dir():
        rep.bad(suite, "has no tasks/ directory")
        return
    task_files = sorted(tasks_dir.glob("*.toml"))
    if not task_files:
        rep.bad(suite, "has no task files")
    seen: dict[str, str] = {}
    for task_path in task_files:
        stem = task_path.stem
        if stem in seen:
            rep.bad(f"{suite}/{task_path.name}", f"duplicate task id {stem!r}")
        seen[stem] = task_path.name
        check_task(rep, suite, task_path)

    suite_toml = suite_dir / "suite.toml"
    if not suite_toml.exists():
        rep.bad(suite, "missing suite.toml")
        return
    try:
        meta = tomllib.loads(suite_toml.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.bad(f"{suite}/suite.toml", f"is not valid TOML: {exc}")
        return
    for key in ("name", "version", "repository", "commit", "required_capabilities"):
        if key not in meta:
            rep.bad(f"{suite}/suite.toml", f"missing required field {key!r}")
    unknown = sorted(set(meta.get("required_capabilities", [])) - CAPABILITIES)
    if unknown:
        rep.bad(f"{suite}/suite.toml", f"unknown required_capabilities: {unknown}")


def main(argv: list[str]) -> int:
    if argv:
        suite_dirs = [SUITES_DIR / name for name in argv]
    else:
        suite_dirs = sorted(p for p in SUITES_DIR.iterdir() if p.is_dir() and (p / "tasks").is_dir())
    rep = Report()
    for suite_dir in suite_dirs:
        if not suite_dir.is_dir():
            rep.bad(suite_dir.name, "suite directory not found")
            continue
        check_suite(rep, suite_dir)

    suites = ", ".join(d.name for d in suite_dirs)
    if rep.violations:
        print(f"FAIL: {len(rep.violations)} violation(s) across {rep.checked} task(s) [{suites}]")
        for violation in rep.violations:
            print(f"  - {violation}")
        return 1
    print(f"OK: {rep.checked} task(s) validated across suites: {suites}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
