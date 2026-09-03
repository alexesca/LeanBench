"""Query tokenization and small textual helpers.

Nothing here builds an index; these are pure string utilities that every rung is
entitled to (a human typing ``grep`` does the same thing in their head).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does",
        "for", "from", "get", "gets", "has", "have", "how", "i", "if", "in", "into", "is",
        "it", "its", "of", "on", "or", "s", "that", "the", "their", "them", "then",
        "there", "these", "this", "to", "use", "used", "using", "was", "we", "what",
        "when", "where", "which", "who", "why", "will", "with", "you", "your",
    }
)


def split_identifier(name: str) -> list[str]:
    """``Client._send_handling_redirects`` -> ``['client', 'send', 'handling', ...]``."""
    parts: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name):
        if not chunk:
            continue
        parts.extend(piece.lower() for piece in _CAMEL_RE.findall(chunk))
    return parts


def query_terms(query: str, *, keep_stopwords: bool = False) -> list[str]:
    """Ordered, de-duplicated, lower-cased content terms of a natural-language query."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _WORD_RE.findall(query):
        term = raw.lower()
        if not keep_stopwords and (term in STOPWORDS or len(term) < 2):
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def query_symbol_candidates(query: str) -> list[str]:
    """Literal identifier-looking tokens in a query, e.g. ``Client.send`` or ``URL``."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", query):
        if "." in raw or "_" in raw or (raw[:1].isupper() and raw.lower() != raw):
            if raw not in seen:
                seen.add(raw)
                out.append(raw)
    return out


def identifier_variants(terms: list[str]) -> list[str]:
    """Code-shaped spellings of a multi-word query: ``connection pooling`` ->
    ``connection_pool``, ``ConnectionPool``. Deterministic, order-preserving."""
    out: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    for index in range(len(terms) - 1):
        left, right = terms[index], terms[index + 1]
        stem_r = _stem(right)
        push(f"{left}_{stem_r}")
        push(f"{left.capitalize()}{stem_r.capitalize()}")
    return out


def _stem(word: str) -> str:
    """Crude English de-inflection so ``pooling`` also finds ``pool``."""
    for suffix in ("ing", "ers", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def stems(terms: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        stem = _stem(term)
        if stem not in seen:
            seen.add(stem)
            out.append(stem)
    return out


def dotted_parts(name: str) -> tuple[str | None, str]:
    """``Client.send`` -> ``('Client', 'send')``; ``send`` -> ``(None, 'send')``."""
    if "." in name:
        scope, _, leaf = name.rpartition(".")
        return scope, leaf
    return None, name


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def block_end(lines: tuple[str, ...], start_line: int) -> int:
    """End of the indented block opened at 1-based ``start_line``, by indentation.

    Purely textual -- the technique a human uses when reading with ``less``.
    """
    if not (1 <= start_line <= len(lines)):
        return start_line
    base = indent_of(lines[start_line - 1])
    # Skip the (possibly multi-line) definition header first: its closing bracket line
    # shares the header's indentation and would otherwise end the block immediately.
    header_end = start_line
    depth = 0
    for number in range(start_line, min(len(lines), start_line + 60) + 1):
        row = lines[number - 1]
        depth += row.count("(") + row.count("[") - row.count(")") - row.count("]")
        if depth <= 0 and row.rstrip().endswith(":"):
            header_end = number
            break
    last = header_end
    for number in range(header_end + 1, len(lines) + 1):
        row = lines[number - 1]
        if not row.strip():
            continue
        if indent_of(row) <= base:
            break
        last = number
    return last


DEF_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<async>async)[ \t]+)?(?P<kind>def|class)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)


def parse_def_line(line: str) -> tuple[str, str, int] | None:
    """``    def send(...)`` -> ``('function', 'send', 4)``; ``None`` if not a definition."""
    match = DEF_LINE_RE.match(line)
    if match is None:
        return None
    kind = "class" if match.group("kind") == "class" else "function"
    return kind, match.group("name"), len(match.group("indent").expandtabs(4))


def read_signature(lines: tuple[str, ...], line_number: int, max_lines: int = 40) -> str:
    """Join a definition header across continuation lines until brackets balance."""
    if not (1 <= line_number <= len(lines)):
        return ""
    parts: list[str] = []
    depth = 0
    for number in range(line_number, min(len(lines), line_number + max_lines) + 1):
        row = lines[number - 1]
        parts.append(row.strip())
        depth += row.count("(") + row.count("[") - row.count(")") - row.count("]")
        if depth <= 0 and row.rstrip().endswith(":"):
            break
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


_RETURN_RE = re.compile(r"->\s*(.+?)\s*:\s*$")


def return_annotation(signature: str) -> str | None:
    match = _RETURN_RE.search(signature)
    return match.group(1) if match else None


def clean_line(line: str, limit: int = 220) -> str:
    """One-line, newline-free, length-capped rendering for compact output."""
    text = line.replace("\t", "    ").rstrip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
