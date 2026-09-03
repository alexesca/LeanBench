"""Deterministic, offline, approximate token counter.

PROTOCOL.md §2: ``token_budget`` is advisory in units but binding in behaviour. The
candidate must use *its own* approximate counter; LeanBench's tokenizer is authoritative
for scoring. We therefore refuse to depend on a downloadable BPE vocabulary (network at
candidate start-up would be both slow and non-hermetic) and use a stable heuristic that
tracks GPT-style BPE closely enough for budgeting:

* every word / punctuation run is at least one token,
* words longer than four characters cost ``ceil(len / 4)`` tokens,
* newlines cost one token each.

The counter is a pure function of the string: identical input always yields an identical
count, which is what makes budget truncation reproducible.
"""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def approx_tokens(text: str) -> int:
    """Approximate the number of BPE tokens in *text*. Deterministic."""
    total = text.count("\n")
    for match in _TOKEN_RE.finditer(text):
        length = len(match.group())
        total += 1 if length <= 4 else (length + 3) // 4
    return total


def canonical_json(obj: Any) -> str:
    """Canonical, newline-free JSON serialization used for every wire payload."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def approx_tokens_json(obj: Any) -> int:
    return approx_tokens(canonical_json(obj))
