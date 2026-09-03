"""`MutationPort`: controlled edits to a working copy, for incremental-update tasks.

Edits are recorded so `revert` can restore the tree exactly; a run never leaves the
corpus modified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leanbench.kernel.errors import GatewayError
from leanbench.kernel.registry import register
from leanbench.scoring.normalize import normalize_path


class FileMutation:
    """Whole-file or line-range replacement, with an in-memory undo log."""

    def __init__(self) -> None:
        self._undo: list[tuple[Path, str | None]] = []

    def apply(self, root: Any, spec: dict[str, Any]) -> dict[str, list[str]]:
        base = Path(root).resolve()
        rel = normalize_path(str(spec["path"]))
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            raise GatewayError(f"patch path {rel!r} escapes the repository root")
        existed = target.is_file()
        original = target.read_text(encoding="utf-8") if existed else None
        self._undo.append((target, original))

        content = str(spec["content"])
        start, end = spec.get("start"), spec.get("end")
        if start is None or end is None or original is None:
            new_text = content
        else:
            lines = original.splitlines()
            start_i, end_i = int(start), int(end)
            if start_i < 1 or end_i < start_i:
                raise GatewayError(f"invalid patch range {start_i}-{end_i}")
            lines[start_i - 1 : end_i] = content.splitlines()
            new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_text, encoding="utf-8")
        return {
            "changed": [rel] if existed else [],
            "added": [] if existed else [rel],
            "removed": [],
        }

    def revert(self, root: Any) -> None:
        for target, original in reversed(self._undo):
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(original, encoding="utf-8")
        self._undo.clear()


register("mutation", "file", FileMutation)
