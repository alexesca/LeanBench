"""Identifier normalization (TASKS.md §4.1). Pure functions, stdlib only."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_path(path: str) -> str:
    """POSIX separators, repo-relative, no leading `./`, no trailing slash."""
    if not path:
        return ""
    text = path.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        parts.append(part)
    return "/".join(parts)


def normalize_symbol(symbol: str) -> str:
    """Trim, collapse `::` to `.`, drop a leading dot. Case is preserved: Python
    identifiers are case sensitive and folding would create false positives."""
    if not symbol:
        return ""
    text = symbol.strip().replace("::", ".").replace("#", ".")
    text = text.strip(".")
    while ".." in text:
        text = text.replace("..", ".")
    return text


def symbol_matches(gold_symbol: str, candidate_symbol: str) -> bool:
    """Suffix-qualified equality (TASKS.md §4.1).

    Gold `Client.send` matches `httpx._client.Client.send` and `Client.send`, but not
    bare `send`. Gold bare `send` matches `Client.send` (gold is itself unqualified).
    """
    gold = normalize_symbol(gold_symbol)
    cand = normalize_symbol(candidate_symbol)
    if not gold or not cand:
        return False
    if cand == gold:
        return True
    return cand.endswith("." + gold)


def match_gold_symbol(gold_symbols: Iterable[str], candidate_symbol: str) -> str | None:
    """The gold symbol a candidate symbol satisfies, or None.

    Deterministic: on multiple matches the most specific (longest normalized) gold wins,
    ties broken lexicographically.
    """
    matches = [g for g in gold_symbols if symbol_matches(g, candidate_symbol)]
    if not matches:
        return None
    return sorted(matches, key=lambda g: (-len(normalize_symbol(g)), normalize_symbol(g)))[0]


_FILE_SUFFIXES = frozenset({"py", "md", "txt", "cfg", "ini", "toml", "rst", "yml", "yaml", "json"})


def is_path_like(identifier: str) -> bool:
    """A gold-comparable identifier is a path if it has a separator or a file suffix."""
    if "/" in identifier:
        return True
    head, _, tail = identifier.rpartition(".")
    return bool(head) and tail.lower() in _FILE_SUFFIXES
