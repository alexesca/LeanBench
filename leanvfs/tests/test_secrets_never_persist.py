"""Product invariant 5: secrets never persist.

This test may not be skipped or marked flaky. A leak here is not a quality problem, it
is a disclosure: the index is a file on disk, and anything that reaches it can be read
back by any process, shipped in a backup, or handed to a model.

The mechanism it guards is a choke point, not a policy -- every fact value passes through
exactly one `redact()` call before it can reach SQLite, and the fact-insert API accepts
only a `Redacted` wrapper. The test's job is to prove there is no second path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from leanvfs.config import load_config
from leanvfs.indexer import Indexer
from leanvfs.queries import QueryEngine
from leanvfs.redact import Redacted, Redactor
from leanvfs.store import Store

#: Each entry is (filename, file body, the exact strings that must never survive).
#: The identifier is expected to survive; only the value must not.
PLANTED = {
    "settings.py": (
        'STRIPE_API_KEY = "sk_live_51H8xQwLkdIwHu7ixaBcDeFgHiJkLmNoPqRsTuVwXyZ012345"\n'
        'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
        'GITHUB_TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"\n'
        'DATABASE_URL = "postgres://admin:hunter2SuperSecret@db.internal:5432/prod"\n'
        'PASSWORD = "correct-horse-battery-staple-9917"\n',
        [
            "sk_live_51H8xQwLkdIwHu7ixaBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
            "hunter2SuperSecret",
            "correct-horse-battery-staple-9917",
        ],
    ),
    "auth.py": (
        '"""Service auth.\n\n'
        "Example token:\n"
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
        'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\n"""\n\n'
        "def login():\n"
        '    return "ok"\n',
        [
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        ],
    ),
    "deploy.env.example": (
        "SERVICE_ACCOUNT_KEY=-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAx7Zq9vQdKp2mNfLbTgHyRwXcVuEaBnMkPjSdWqZrTyUiOpAs\n"
        "-----END RSA PRIVATE KEY-----\n"
        "SESSION_SECRET=Zq3vB8nK1pL9wXeR7tYu2iOa5sDf0gHj\n",
        [
            "MIIEowIBAAKCAQEAx7Zq9vQdKp2mNfLbTgHyRwXcVuEaBnMkPjSdWqZrTyUiOpAs",
            "Zq3vB8nK1pL9wXeR7tYu2iOa5sDf0gHj",
        ],
    ),
}

#: Identifiers SHOULD survive -- an index that hides the existence of DATABASE_URL is
#: less useful and no safer, because the name is not the secret.
SURVIVING_IDENTIFIERS = ["STRIPE_API_KEY", "DATABASE_URL", "SESSION_SECRET"]


@pytest.fixture()
def indexed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, (body, _secrets) in PLANTED.items():
        (repo / name).write_text(body, encoding="utf-8")
    cfg = load_config(repo, skip_user_layers=True)
    store = Store(tmp_path / "index.sqlite")
    Indexer(repo, store, cfg).full_sync()
    return repo, store, cfg


def all_secrets() -> list[str]:
    return [s for _body, secrets in PLANTED.values() for s in secrets]


def test_no_secret_reaches_any_sqlite_text_column(indexed) -> None:
    """Full scan of every text column of every table -- not just the ones we expect."""
    _repo, store, _cfg = indexed
    leaks: list[str] = []
    for table, column in store.text_columns():
        rows = store.conn.execute(f'SELECT "{column}" FROM "{table}"').fetchall()
        blob = "\n".join(str(r[0]) for r in rows if r[0] is not None)
        for secret in all_secrets():
            if secret in blob:
                leaks.append(f"{table}.{column} contains {secret[:12]}...")
    assert not leaks, "secret values reached SQLite:\n" + "\n".join(leaks)


def test_no_secret_reaches_the_fts_index(indexed) -> None:
    """FTS is a separate storage domain and a separate way to leak."""
    _repo, store, _cfg = indexed
    rows = store.conn.execute(
        "SELECT path, symbol, qualified, signature, keywords, facts FROM search_fts"
    ).fetchall()
    blob = "\n".join(" ".join(str(v) for v in tuple(r) if v is not None) for r in rows)
    for secret in all_secrets():
        assert secret not in blob, f"FTS index contains {secret[:12]}..."


def test_no_secret_reaches_a_protocol_response(indexed) -> None:
    """The path an agent actually sees."""
    _repo, store, cfg = indexed
    engine = QueryEngine(store, cfg)
    payloads: list[str] = []
    for query in ("key", "token", "secret", "password", "private", "session", "database"):
        payloads.append(str(engine.search(query, 25)))
        payloads.append(str(engine.get_docs(query, 10)))
    for row in store.conn.execute("SELECT qualified_name FROM symbols"):
        payloads.append(str(engine.get_context(row["qualified_name"], 4000)))
    blob = "\n".join(payloads)
    for secret in all_secrets():
        assert secret not in blob, f"a protocol response contains {secret[:12]}..."


def test_no_secret_reaches_rendered_output(indexed) -> None:
    from leanvfs.render.compact import CompactRenderer
    from leanvfs.render.debug import DebugRenderer
    from leanvfs.views import build_file_view

    _repo, store, cfg = indexed
    blob = ""
    for name in PLANTED:
        view = build_file_view(store, name, cfg)
        if view is None:
            continue
        # Both renderers, at a budget generous enough that nothing is hidden merely by
        # being truncated -- a secret that only survives because the budget cut it off
        # would still be a leak at a larger budget.
        blob += CompactRenderer().render_file(view, 100_000, cfg)[0] + "\n"
        blob += DebugRenderer().render_file(view, 100_000, cfg)[0] + "\n"
    for secret in all_secrets():
        assert secret not in blob, f"rendered output contains {secret[:12]}..."


def test_identifiers_survive_so_the_index_stays_useful(indexed) -> None:
    """Redaction must remove the value, not the fact that a setting exists."""
    _repo, store, _cfg = indexed
    blob = ""
    for table, column in store.text_columns():
        rows = store.conn.execute(f'SELECT "{column}" FROM "{table}"').fetchall()
        blob += "\n".join(str(r[0]) for r in rows if r[0] is not None)
    found = [name for name in SURVIVING_IDENTIFIERS if name in blob]
    assert found, (
        "no configuration identifier survived redaction; the redactor is discarding "
        "useful structure, not just secrets"
    )


def test_fact_insertion_refuses_unredacted_values(tmp_path: Path) -> None:
    """The choke point is structural: there is no second path to the facts table."""
    from leanvfs.model import Fact

    store = Store(tmp_path / "i.sqlite")
    with pytest.raises(TypeError):
        store.insert_facts([Fact(kind="keyword", value="plain")], 1, {}, 1)  # type: ignore[list-item]


@pytest.mark.parametrize(
    "token",
    [
        "sk_live_51H8xQwLkdIwHu7ixaBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "Zq3vB8nK1pL9wXeR7tYu2iOa5sDf0gHj",
    ],
)
def test_detector_recognises_credential_shapes(token: str) -> None:
    from leanvfs.config import load_config as _load

    redactor = Redactor(_load(None, skip_user_layers=True))
    assert redactor.is_secret_token(token), f"{token[:10]}... not detected"


def test_ordinary_identifiers_are_not_redacted() -> None:
    """A detector that flags everything is as useless as one that flags nothing."""
    from leanvfs.config import load_config as _load

    redactor = Redactor(_load(None, skip_user_layers=True))
    for benign in ("get_user_by_email", "MAX_RETRIES", "http://localhost:8000", "utf-8"):
        assert not redactor.is_secret_token(benign), benign


def test_redacted_wrapper_cannot_be_forged() -> None:
    """`Redacted` is a capability: only the redactor may mint one."""
    from leanvfs.model import Fact
    from leanvfs.redact import RedactionError

    with pytest.raises(RedactionError):
        Redacted(Fact(kind="keyword", value="x"), False)
