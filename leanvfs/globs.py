"""Deterministic glob matching over repo-relative POSIX paths.

`fnmatch` treats `*` as matching `/`, which makes `**` meaningless. This module
implements the gitignore-style semantics the config relies on.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=2048)
def compile_glob(pattern: str) -> re.Pattern[str]:
    i = 0
    n = len(pattern)
    out = ["^"]
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**/", i):
                out.append("(?:[^/]+/)*")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    if compile_glob(pattern).match(path):
        return True
    # A bare directory prefix pattern like "tests/**" should also match "tests/a.py"
    # even when written without a trailing slash form.
    if pattern.endswith("/**") and (path == pattern[:-3] or path.startswith(pattern[:-2])):
        return True
    return False


def any_match(path: str, patterns: list[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)
