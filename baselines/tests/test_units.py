"""Unit tests for the pieces that are not process-level: tagger, AST extractor,
ignore rules and the token estimator."""

from __future__ import annotations

from pathlib import Path

from leanbench_baselines.common.ignores import IgnoreIndex
from leanbench_baselines.common.payload import Item, Payload, render
from leanbench_baselines.common.text import block_end, read_signature, split_identifier
from leanbench_baselines.common.tokens import approx_tokens
from leanbench_baselines.ctags.tagger import generate_tags
from leanbench_baselines.minast.parser import AstExtractor

SAMPLE = '''"""Module docstring with a fake def not_a_tag(): inside."""

import typing
import os.path as osp
from .errors import TooManyRetries, Other as Aliased

MAX = 3


class Engine(Base):
    """Doc."""

    limit: int = 5

    def send(
        self,
        request: str,
        *,
        retries: int | None = None,
    ) -> str:
        """Send it."""
        local_only = 1
        raise TooManyRetries(local_only)


def build(timeout: float = 5.0) -> Engine:
    return Engine()
'''


def _tags():
    return generate_tags("pkg/core.py", tuple(SAMPLE.splitlines()))


def test_tagger_kinds_and_scopes():
    tags = {(tag.qualified, tag.kind) for tag in _tags()}
    assert ("Engine", "c") in tags
    assert ("Engine.send", "m") in tags
    assert ("build", "f") in tags
    assert ("MAX", "v") in tags
    assert ("Engine.limit", "v") in tags
    # locals are not tagged (ctags kind `l` is off by default)
    assert not any(name.endswith("local_only") for name, _ in tags)
    # a `def` mentioned inside a docstring is not a tag
    assert not any(name == "not_a_tag" for name, _ in tags)


def test_tagger_imports():
    tags = {(tag.name, tag.kind) for tag in _tags()}
    assert ("typing", "i") in tags
    assert ("osp", "I") in tags
    assert ("TooManyRetries", "x") in tags
    assert ("Aliased", "x") in tags


def test_tagger_signature_and_typeref():
    send = next(tag for tag in _tags() if tag.qualified == "Engine.send")
    assert send.signature == "(self, request: str, *, retries: int | None = None)"
    assert send.typeref == "str"
    assert send.line < send.end
    assert send.scope == "Engine"
    assert send.scope_kind == "class"
    assert "signature:" in send.as_ctags_line()


def test_ast_extractor_signature_and_nesting():
    extractor = AstExtractor()
    result = extractor.parse_file("pkg/core.py", SAMPLE)
    by_name = {symbol.qualified: symbol for symbol in result.symbols}
    send = by_name["Engine.send"]
    assert send.kind == "method"
    assert send.return_type == "str"
    assert [param.name for param in send.params] == ["self", "request", "*", "retries"]
    assert send.params[1].annotation == "str"
    assert send.params[3].default == "None"
    assert send.doc == "Send it."
    assert by_name["Engine"].bases == ("Base",)
    assert by_name["build"].kind == "function"


def test_ast_extractor_imports():
    extractor = AstExtractor()
    result = extractor.parse_file("pkg/core.py", SAMPLE)
    rendered = {record.render() for record in result.imports}
    assert "import typing" in rendered
    assert "import os.path as osp" in rendered
    assert "from  import TooManyRetries" in rendered or any(
        record.name == "TooManyRetries" and record.level == 1 for record in result.imports
    )


def test_ignore_rules(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("build/\n*.log\n!keep.log\n", encoding="utf-8")
    index = IgnoreIndex.for_repo(tmp_path)
    assert index.is_ignored("build", is_dir=True)
    assert index.is_ignored("a/b.log", is_dir=False)
    assert not index.is_ignored("keep.log", is_dir=False)
    assert index.is_ignored(".git", is_dir=True)
    assert not index.is_ignored("pkg/core.py", is_dir=False)


def test_block_end_skips_multiline_signature():
    lines = tuple(SAMPLE.splitlines())
    start = next(i for i, line in enumerate(lines, start=1) if line.strip().startswith("def send"))
    assert block_end(lines, start) > start + 6
    assert read_signature(lines, start).startswith("def send( self, request: str")


def test_split_identifier():
    assert split_identifier("Client._send_handling_redirects") == [
        "client",
        "send",
        "handling",
        "redirects",
    ]
    assert split_identifier("TooManyRedirects") == ["too", "many", "redirects"]


def test_token_estimator_is_monotonic_and_deterministic():
    assert approx_tokens("") == 0
    assert approx_tokens("abc") == 1
    assert approx_tokens("abcdefgh") == 2
    assert approx_tokens("a b c") == 3
    assert approx_tokens("x" * 400) == approx_tokens("x" * 400)


def test_render_drops_a_deterministic_suffix():
    payload = Payload(
        header={"hits": []},
        header_text="header",
        items=[Item("hits", "hit", {"n": index}, f"line {index}") for index in range(10)],
        list_fields=("hits",),
    )
    full = render(payload, "compact", None)
    assert full.truncated is False
    small = render(payload, "compact", 12)
    assert small.truncated is True
    assert small.dropped["hit"] > 0
    assert full.result["text"].startswith(small.result["text"])
    again = render(payload, "compact", 12)
    assert again.result == small.result and again.dropped == small.dropped
