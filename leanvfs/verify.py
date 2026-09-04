"""`leanvfs verify` — the direct test of "incremental converges to clean".

The comparison is pinned to the current `idf_generation`. Without that pin the test is
rigged: a corpus-wide statistic legitimately changes on every edit, so incremental and
rebuilt state would differ for a reason that has nothing to do with correctness.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .indexer import Indexer
from .store import Store


@dataclass
class Divergence:
    kind: str
    detail: str


@dataclass
class VerifyReport:
    ok: bool
    checked: dict[str, int] = field(default_factory=dict)
    divergences: list[Divergence] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "divergences": [{"kind": d.kind, "detail": d.detail} for d in self.divergences],
        }


def _symbol_keys(store: Store) -> set[str]:
    return {r["stable_key"] for r in store.conn.execute("SELECT stable_key FROM symbols")}


def _fact_tuples(store: Store) -> set[tuple[str, str, str]]:
    rows = store.conn.execute(
        "SELECT COALESCE(s.stable_key,'') k, f.kind, f.value FROM facts f "
        "LEFT JOIN symbols s ON s.id = f.symbol_id"
    )
    return {(r["k"], r["kind"], r["value"]) for r in rows}


def _edge_tuples(store: Store) -> set[tuple[str, str, str]]:
    rows = store.conn.execute(
        "SELECT COALESCE(src.stable_key,'') s, r.kind, "
        "COALESCE(tgt.stable_key, r.target_external, '') t FROM relationships r "
        "LEFT JOIN symbols src ON src.id = r.source_symbol_id "
        "LEFT JOIN symbols tgt ON tgt.id = r.target_symbol_id"
    )
    return {(r["s"], r["kind"], r["t"]) for r in rows}


def verify(repo_root: Path, store: Store, cfg: Any, *, max_report: int = 10) -> VerifyReport:
    """Rebuild from clean into a scratch database and diff the canonical model."""
    report = VerifyReport(ok=True)
    with tempfile.TemporaryDirectory(prefix="lvfs-verify-") as tmp:
        scratch = Store(Path(tmp) / "rebuild.sqlite")
        # Pin the snapshot before rebuilding: equivalence is defined at a fixed
        # idf_generation, not across two different ones.
        scratch.set_meta("idf_generation", str(max(store.idf_generation() - 1, 0)))
        Indexer(repo_root, scratch, cfg).full_sync()

        for name, live, rebuilt in (
            ("symbols", _symbol_keys(store), _symbol_keys(scratch)),
            ("facts", _fact_tuples(store), _fact_tuples(scratch)),
            ("relationships", _edge_tuples(store), _edge_tuples(scratch)),
        ):
            report.checked[name] = len(rebuilt)
            missing = sorted(rebuilt - live)[:max_report]
            extra = sorted(live - rebuilt)[:max_report]
            for item in missing:
                report.ok = False
                report.divergences.append(Divergence(f"{name}:missing", str(item)))
            for item in extra:
                report.ok = False
                # Stale state surviving an edit is the bug class this whole check exists
                # to catch: the index answers confidently and the answer is wrong.
                report.divergences.append(Divergence(f"{name}:stale", str(item)))
        scratch.close()
    return report
