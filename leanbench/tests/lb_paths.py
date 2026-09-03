"""Shared locations for the LeanBench test suite.

Kept out of `conftest.py` because pytest imports conftest by that bare name, and this
workspace has more than one test root — importing `from conftest import ...` binds to
whichever one was collected first.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "leanbench" / "src"
FIXTURES = REPO_ROOT / "leanbench" / "fixtures"
FAKE_MANIFESTS = FIXTURES / "fake-candidate" / "manifests"
MINI_SUITE = FIXTURES / "mini-suite"
MINI_REPO = FIXTURES / "mini-repo"
