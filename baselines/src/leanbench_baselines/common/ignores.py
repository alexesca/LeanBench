"""A self-contained ``.gitignore``-style matcher.

Candidates MUST NOT run git (PROTOCOL.md §4.1), so ignore handling is reimplemented here.
Supported subset: comments, blank lines, negation (``!``), directory-only patterns
(trailing ``/``), anchoring (leading or embedded ``/``), ``*``, ``?``, ``**`` and
character classes. Later rules win, matching git's semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Always skipped, regardless of any ``.gitignore``.
DEFAULT_IGNORES: tuple[str, ...] = (
    ".git/",
    ".hg/",
    ".svn/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".venv/",
    "node_modules/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
)


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool
    base: str  # repo-relative posix dir the rule was declared in ("" for root)


def _translate(pattern: str) -> str:
    """Translate a gitignore glob body into a regex body (no anchors)."""
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if char == "?":
            out.append("[^/]")
            i += 1
            continue
        if char == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(char))
                i += 1
                continue
            body = pattern[i + 1 : close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = close + 1
            continue
        out.append(re.escape(char))
        i += 1
    return "".join(out)


def _compile(raw: str, base: str) -> _Rule | None:
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    negate = line.startswith("!")
    if negate:
        line = line[1:]
    if line.startswith("\\"):
        line = line[1:]
    line = line.rstrip()
    if not line:
        return None
    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]
    anchored = "/" in line
    line = line.removeprefix("/")
    body = _translate(line)
    prefix = "^" if anchored else "^(?:.*/)?"
    return _Rule(re.compile(prefix + body + "$"), negate, dir_only, base)


class IgnoreIndex:
    """Ordered ignore rules gathered from the repository's ``.gitignore`` files."""

    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def for_repo(cls, root: Path) -> IgnoreIndex:
        rules: list[_Rule] = []
        for pattern in DEFAULT_IGNORES:
            rule = _compile(pattern, "")
            if rule is not None:
                rules.append(rule)
        for gitignore in sorted(root.rglob(".gitignore")):
            base = gitignore.parent.relative_to(root).as_posix()
            base = "" if base == "." else base
            try:
                text = gitignore.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                rule = _compile(raw, base)
                if rule is not None:
                    rules.append(rule)
        return cls(rules)

    def is_ignored(self, rel_path: str, *, is_dir: bool) -> bool:
        ignored = False
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.base:
                prefix = rule.base + "/"
                if not rel_path.startswith(prefix):
                    continue
                candidate = rel_path[len(prefix) :]
            else:
                candidate = rel_path
            if rule.regex.match(candidate):
                ignored = not rule.negate
        return ignored

    def add_pattern(self, pattern: str) -> None:
        rule = _compile(pattern, "")
        if rule is not None:
            self._rules.append(rule)
