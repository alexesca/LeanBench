"""The hardest invariant: incremental state converges to a clean rebuild.

This is where repository-intelligence systems quietly break -- the index answers
confidently and the answer is stale. A passing rate here means nothing unless the check
can actually fail, so `test_stale_symbol_is_caught` deliberately breaks it.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest

from leanvfs.config import load_config
from leanvfs.indexer import Indexer
from leanvfs.store import Store
from leanvfs.verify import verify

FIXTURE = Path(__file__).resolve().parents[2] / "leanbench" / "fixtures" / "mini-repo"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    return root


def _open(root: Path, tmp_path: Path):
    cfg = load_config(root, skip_user_layers=True)
    store = Store(tmp_path / "index.sqlite")
    return cfg, store


def test_no_change_means_no_work(workspace: Path, tmp_path: Path) -> None:
    """Product invariant: bounded work per edit. Nothing changed, so nothing is parsed."""
    cfg, store = _open(workspace, tmp_path)
    indexer = Indexer(workspace, store, cfg)
    indexer.full_sync()
    result = indexer.incremental_sync()
    assert result.reparsed == 0
    assert result.skipped > 0


def test_incremental_equals_clean_after_a_body_edit(workspace: Path, tmp_path: Path) -> None:
    cfg, store = _open(workspace, tmp_path)
    indexer = Indexer(workspace, store, cfg)
    indexer.full_sync()

    target = workspace / "shopcart" / "pricing.py"
    target.write_text(
        target.read_text().replace("def rate_for", "def rate_for_customer"), encoding="utf-8"
    )
    indexer.incremental_sync()

    report = verify(workspace, store, cfg)
    assert report.ok, report.as_dict()["divergences"]


def test_incremental_equals_clean_after_add_and_delete(workspace: Path, tmp_path: Path) -> None:
    """Adds and deletes are the cases a per-file pipeline silently gets wrong: a new file
    can resolve references that were dangling elsewhere, and a deleted one dangles
    references that were resolved."""
    cfg, store = _open(workspace, tmp_path)
    indexer = Indexer(workspace, store, cfg)
    indexer.full_sync()

    (workspace / "shopcart" / "shipping.py").write_text(
        "from .models import Money\n\n\n"
        "class ShippingCalculator:\n"
        '    """Flat-rate shipping."""\n\n'
        "    def quote(self, weight_grams: int) -> Money:\n"
        "        return Money(minor_units=500, currency='GBP')\n",
        encoding="utf-8",
    )
    (workspace / "shopcart" / "discounts.py").unlink()
    indexer.incremental_sync()

    report = verify(workspace, store, cfg)
    assert report.ok, report.as_dict()["divergences"]


def test_randomized_edit_sequence_converges(workspace: Path, tmp_path: Path) -> None:
    """A seeded sequence of mixed operations. Reproducible, so a failure can be debugged
    rather than merely observed once."""
    cfg, store = _open(workspace, tmp_path)
    indexer = Indexer(workspace, store, cfg)
    indexer.full_sync()

    rng = random.Random(20260903)
    sources = sorted((workspace / "shopcart").glob("*.py"))
    for step in range(25):
        target = rng.choice(sources)
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8")
        choice = rng.randrange(4)
        if choice == 0:
            text += f"\n\n# note {step}\n"                      # comment only
        elif choice == 1:
            text += f"\n\nCONSTANT_{step} = {step}\n"           # new symbol
        elif choice == 2:
            text = text.replace("    ", "    ", 1) + "\n"       # whitespace
        else:
            text += f"\n\ndef helper_{step}(value: int) -> int:\n    return value + {step}\n"
        target.write_text(text, encoding="utf-8")
        indexer.incremental_sync()

    report = verify(workspace, store, cfg)
    assert report.ok, report.as_dict()["divergences"][:5]


def test_stale_symbol_is_caught(workspace: Path, tmp_path: Path) -> None:
    """The negative test that proves the check works.

    A check that never fails and a check that cannot fail are indistinguishable from the
    outside, so break incremental deliberately: rename a symbol on disk but do NOT tell
    the index. `verify` must notice the stale entry.
    """
    cfg, store = _open(workspace, tmp_path)
    indexer = Indexer(workspace, store, cfg)
    indexer.full_sync()

    target = workspace / "shopcart" / "payments.py"
    target.write_text(
        target.read_text().replace("def refund", "def reverse_charge"), encoding="utf-8"
    )
    # Deliberately skip incremental_sync() — this is the broken implementation.

    report = verify(workspace, store, cfg)
    assert not report.ok
    kinds = {d.kind for d in report.divergences}
    assert any(k.endswith(("stale", "missing")) for k in kinds), kinds
