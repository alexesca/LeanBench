from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from client import BASELINES, CandidateProcess, manifest_for  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "corpus" / "httpx"

MINI_FILES: dict[str, str] = {
    ".gitignore": "build/\n*.log\n!keep.log\n",
    "pkg/__init__.py": "from .core import Engine\n\n__all__ = ['Engine']\n",
    "pkg/core.py": '''"""Core engine module."""

from __future__ import annotations

import typing

from .errors import TooManyRetries

DEFAULT_RETRIES = 3


class Engine:
    """Drives requests with a retry budget."""

    max_retries: int = DEFAULT_RETRIES

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def send(self, request: str, *, retries: int | None = None) -> str:
        """Send a request and follow retries."""
        attempts = retries if retries is not None else self.max_retries
        for _ in range(attempts):
            result = self._attempt(request)
            if result:
                return result
        raise TooManyRetries(request)

    def _attempt(self, request: str) -> str:
        return request.upper()


def build_engine(timeout: float = 5.0) -> Engine:
    """Factory for :class:`Engine`."""
    return Engine(timeout=timeout)


ALIASES: typing.Final = {"default": build_engine}
''',
    "pkg/errors.py": '''class TooManyRetries(Exception):
    """Raised when the retry budget is exhausted."""
''',
    "tests/test_core.py": """from pkg.core import Engine


def test_send_retries():
    engine = Engine()
    assert engine.send("hi") == "HI"
""",
    "docs/guide.md": "# Guide\n\n## Retries\n\nThe engine retries requests up to `max_retries` times.\n",
    "build/generated.py": "SHOULD_NOT_BE_INDEXED = 1\n",
    "noise.log": "ignored\n",
    "keep.log": "kept\n",
}


@pytest.fixture(scope="session")
def mini_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mini-repo")
    for relative, content in MINI_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def corpus() -> Path:
    if not (CORPUS / "httpx" / "_client.py").is_file():
        pytest.skip("corpus/httpx is not checked out")
    return CORPUS


def spawn(name: str) -> CandidateProcess:
    return CandidateProcess(manifest_for(name))


@pytest.fixture(params=BASELINES, scope="module")
def candidate(request: pytest.FixtureRequest, mini_repo: Path) -> Iterator[CandidateProcess]:
    process = spawn(request.param)
    process.prepare(mini_repo)
    yield process
    process.close()
