"""Real repositories contain files that are broken, huge, binary, or unreadable.

Indexing must survive all of them: a tool that crashes on one bad file in a 5,000-file
tree is not usable, and one that silently drops files is worse than one that crashes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from leanvfs.config import load_config
from leanvfs.indexer import Indexer
from leanvfs.queries import QueryEngine
from leanvfs.store import Store


@pytest.fixture()
def hostile(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deep" / "a").mkdir(parents=True)
    (repo / "good.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (repo / "broken.py").write_text("def broken(:\n  not python\n", encoding="utf-8")
    (repo / "binary.py").write_bytes(b"\xff\xfe\x00\x01binary\x00\x00")
    (repo / "crlf.py").write_text("x = 1\r\ny = 2\r\n", encoding="utf-8")
    (repo / "unicode.py").write_text('café = "x"\ndef été(): pass\n', encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    (repo / "data.json").write_text('{"not": "python"}\n', encoding="utf-8")
    (repo / "README.md").write_text("# Title\n\nDocs.\n", encoding="utf-8")
    (repo / "huge.py").write_text("X = 1\n" * 400_000, encoding="utf-8")
    return repo


def _index(repo: Path, tmp_path: Path):
    cfg = load_config(repo, skip_user_layers=True)
    store = Store(tmp_path / "index.sqlite")
    indexer = Indexer(repo, store, cfg)
    return cfg, store, indexer, indexer.full_sync()


def test_hostile_repository_indexes_without_crashing(hostile, tmp_path) -> None:
    _cfg, store, _ix, result = _index(hostile, tmp_path)
    assert result.files >= 8
    assert store.index_state() == "ok"


def test_a_broken_file_does_not_lose_its_neighbours(hostile, tmp_path) -> None:
    """The failure is contained to the file, and the good symbol is still findable."""
    cfg, store, _ix, _r = _index(hostile, tmp_path)
    hits = QueryEngine(store, cfg).search("ok", 10)["hits"]
    assert any(h["path"] == "good.py" for h in hits)


def test_parse_failures_are_recorded_not_swallowed(hostile, tmp_path) -> None:
    _cfg, store, _ix, _r = _index(hostile, tmp_path)
    states = {r["path"]: r["parse_state"] for r in store.all_files()}
    assert states.get("broken.py") in {"partial", "failed"}, states
    # And the file still exists in the index with honest metadata rather than vanishing.
    assert "broken.py" in states


def test_oversized_file_is_capped_not_skipped(hostile, tmp_path) -> None:
    _cfg, store, _ix, _r = _index(hostile, tmp_path)
    row = store.file_by_path("huge.py")
    assert row is not None
    cap = 2 * 1024 * 1024
    assert row["byte_size"] <= cap


def test_repeat_sync_does_no_work_at_all(hostile, tmp_path) -> None:
    """Regression: an oversized file was hashed over max_bytes on write and max_bytes+1
    on the incremental check, so it never compared equal and was re-parsed on EVERY
    query for the lifetime of the index. Steady state must be zero work."""
    _cfg, _store, indexer, _r = _index(hostile, tmp_path)
    first = indexer.incremental_sync()
    second = indexer.incremental_sync()
    assert first.reparsed == 0, "a file churns on the first no-op sync"
    assert second.reparsed == 0, "a file churns on every sync"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_unreadable_file_does_not_churn_forever(hostile, tmp_path) -> None:
    """Same bug class: an unreadable file stored an empty hash, which never equals a
    real digest, so it was re-parsed forever."""
    secret = hostile / "locked.py"
    secret.write_text("def hidden(): pass\n", encoding="utf-8")
    secret.chmod(0o000)
    try:
        _cfg, _store, indexer, _r = _index(hostile, tmp_path)
        assert indexer.incremental_sync().reparsed == 0
    finally:
        secret.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_escaping_the_repository_is_not_indexed(hostile, tmp_path) -> None:
    """A symlink pointing outside the tree must not pull foreign content into the index.
    Following it would leak whatever the link targets into a file an agent can read."""
    outside = tmp_path / "outside_secret.py"
    outside.write_text("OUTSIDE_MARKER = 'do-not-index-me'\n", encoding="utf-8")
    (hostile / "escape.py").symlink_to(outside)

    _cfg, store, _ix, _r = _index(hostile, tmp_path)
    paths = {r["path"] for r in store.all_files()}
    assert "escape.py" not in paths

    blob = ""
    for table, column in store.text_columns():
        rows = store.conn.execute(f'SELECT "{column}" FROM "{table}"').fetchall()
        blob += "\n".join(str(r[0]) for r in rows if r[0] is not None)
    assert "OUTSIDE_MARKER" not in blob


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_loop_terminates(hostile, tmp_path) -> None:
    (hostile / "deep" / "a" / "loop").symlink_to(hostile / "deep")
    _cfg, _store, _ix, result = _index(hostile, tmp_path)
    assert result.files >= 8


def test_non_python_files_still_yield_metadata(hostile, tmp_path) -> None:
    """The fallback ladder: an unsupported language is still a file with a class."""
    _cfg, store, _ix, _r = _index(hostile, tmp_path)
    paths = {r["path"] for r in store.all_files()}
    assert {"data.json", "README.md"} <= paths


def test_empty_repository_is_not_an_error(tmp_path: Path) -> None:
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    cfg = load_config(repo, skip_user_layers=True)
    store = Store(tmp_path / "i.sqlite")
    result = Indexer(repo, store, cfg).full_sync()
    assert result.files == 0
    assert QueryEngine(store, cfg).search("anything", 5)["hits"] == []
