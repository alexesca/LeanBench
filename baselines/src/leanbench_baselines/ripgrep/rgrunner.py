"""Thin, honest wrapper around the real ``rg`` binary.

Every search this baseline performs is an actual ``rg`` subprocess with ``--json`` so the
results are structured rather than screen-scraped. Ignore policy is owned by
:class:`~leanbench_baselines.common.repo.Repository`: we run ``rg --no-ignore`` and then
drop any hit whose path the repository walker considers ignored, so the ripgrep rung and
the other three rungs see byte-identical file sets.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from leanbench_baselines.common.server import OpError

RG_TIMEOUT_S = 25.0
MAX_PATTERNS = 16


@dataclass(frozen=True)
class RgMatch:
    path: str  # repository-relative POSIX
    line: int
    text: str
    matched: tuple[str, ...]  # submatch texts, in rg order


def rg_binary() -> str:
    found = shutil.which("rg")
    if found is None:
        raise OpError("index_error", "ripgrep binary 'rg' not found on PATH")
    return found


def rg_version() -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            [rg_binary(), "--version"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpError("index_error", f"cannot run rg: {exc}") from exc
    return completed.stdout.splitlines()[0].strip() if completed.stdout else "unknown"


def run_rg(
    root: Path,
    patterns: list[str],
    *,
    ignore_case: bool = True,
    word: bool = False,
    fixed: bool = False,
    globs: tuple[str, ...] = (),
    paths: list[str] | None = None,
    max_count: int | None = None,
) -> list[RgMatch]:
    """Run one ``rg --json`` invocation and return its matches."""
    if not patterns:
        return []
    argv = [
        rg_binary(),
        "--json",
        "--no-config",
        "--no-messages",
        "--color",
        "never",
        "--no-ignore",
        "--hidden",
        "--max-filesize",
        "2M",
        "--max-columns",
        "500",
        "--glob",
        "!.git",
        "--glob",
        "!__pycache__",
        "--glob",
        "!*.pyc",
    ]
    if ignore_case:
        argv.append("-i")
    if word:
        argv.append("-w")
    if fixed:
        argv.append("-F")
    if max_count is not None:
        argv.extend(["--max-count", str(max_count)])
    for glob in globs:
        argv.extend(["--glob", glob])
    for pattern in patterns[:MAX_PATTERNS]:
        argv.extend(["-e", pattern])
    argv.append("--")
    if paths:
        argv.extend(str(root / path) for path in paths)
    else:
        argv.append(str(root))

    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=RG_TIMEOUT_S,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise OpError("timeout", f"rg exceeded {RG_TIMEOUT_S}s", retryable=True) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpError("index_error", f"rg invocation failed: {exc}") from exc

    if completed.returncode not in (0, 1):
        message = (completed.stderr or "").strip().splitlines()
        detail = message[0] if message else f"exit {completed.returncode}"
        raise OpError("index_error", f"rg failed: {detail}")

    matches: list[RgMatch] = []
    for raw in completed.stdout.splitlines():
        if not raw.startswith('{"type":"match"'):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        data = event.get("data", {})
        path_text = (data.get("path") or {}).get("text")
        line_number = data.get("line_number")
        if not isinstance(path_text, str) or not isinstance(line_number, int):
            continue
        prefix = f"{root.as_posix()}/"
        if path_text.startswith(prefix):
            rel = path_text[len(prefix) :]
        else:
            try:
                rel = Path(path_text).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
        text = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
        submatches = tuple(
            (sub.get("match") or {}).get("text", "")
            for sub in data.get("submatches", [])
            if isinstance(sub, dict)
        )
        matches.append(RgMatch(path=rel, line=line_number, text=text, matched=submatches))
    return matches
