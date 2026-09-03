"""tree-sitter plumbing and the structural hash.

`structure_hash` is deliberately invariant under whitespace changes and comment
*movement*, which is what makes the second row of the invalidation matrix
("formatting/comment-position only") safe: if it is unchanged we update source ranges
and recompute nothing.
"""

from __future__ import annotations

import threading
from typing import Any

from ..hashing import digest_parts

_local = threading.local()


def ts_language() -> Any:
    lang = getattr(_local, "language", None)
    if lang is None:
        from tree_sitter import Language
        import tree_sitter_python

        lang = Language(tree_sitter_python.language())
        _local.language = lang
    return lang


def ts_parser() -> Any:
    parser = getattr(_local, "parser", None)
    if parser is None:
        from tree_sitter import Parser

        parser = Parser(ts_language())
        _local.parser = parser
    return parser


def leaf_tokens(src: bytes, root: Any) -> tuple[list[str], list[str]]:
    """(code tokens in order, comment texts) — whitespace never appears."""
    code: list[str] = []
    comments: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            comments.append(src[node.start_byte : node.end_byte].decode("utf-8", "replace"))
            continue
        if not node.children:
            text = src[node.start_byte : node.end_byte].decode("utf-8", "replace")
            if text.strip():
                code.append(f"{node.type}\x1f{text}")
            continue
        stack.extend(reversed(node.children))
    return code, comments


def structure_hash_from_tree(src: bytes, root: Any) -> str:
    code, comments = leaf_tokens(src, root)
    return digest_parts(["structure", *code, "\x1e", *sorted(comments)])


def structure_hash_generic(text: str) -> str:
    """Fallback for non-tree-sitter languages: normalized non-empty lines."""
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    return digest_parts(["structure-generic", *[ln for ln in lines if ln]])
