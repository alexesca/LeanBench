"""LeanBench reference baselines.

Four standalone candidate processes speaking the PROTOCOL.md v1 wire protocol:

* ``raw``     -- RawRepository: no index at all.
* ``ripgrep`` -- real ``rg`` invocations, no index.
* ``ctags``   -- a line-oriented tag table (universal-ctags equivalent).
* ``minast``  -- tree-sitter AST symbol table.

Each rung is deliberately capped at the capability ceiling of its category.
"""

__version__ = "0.1.0"
