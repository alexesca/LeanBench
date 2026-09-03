"""Discovery and classification.

Filesystem robustness lives here: symlink loops, repo escape, permission errors,
oversized files, non-UTF8 bytes, files that vanish mid-walk.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .globs import any_match, glob_match


@dataclass
class Discovered:
    rel_path: str
    abs_path: str
    byte_size: int
    language: str
    file_class: str
    is_binary: bool
    skip_reason: str = ""


def _language_for(rel_path: str, cfg: Any) -> str:
    ext = os.path.splitext(rel_path)[1].lower()
    mapping: dict[str, str] = cfg.get("languages.map", {}) or {}
    if ext in mapping:
        return mapping[ext]
    base = os.path.basename(rel_path)
    if base in ("Makefile", "Dockerfile"):
        return "text"
    return "unknown"


def classify(rel_path: str, cfg: Any, head: bytes = b"") -> str:
    """First matching rule wins; generated-marker sniffing overrides."""
    markers = cfg.get("classification.generated_markers", []) or []
    if head:
        # `errors="replace"` cannot raise, so no guard is needed here.
        text = head.decode("utf-8", "replace")
        if any(m in text for m in markers):
            return "generated"
    rules = cfg.get("classification.rules", []) or []
    matched = "unknown"
    for entry in rules:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        pattern, klass = entry
        if glob_match(rel_path, pattern):
            matched = klass
            break
    if matched in ("test.unit", "test.integration", "test.e2e"):
        low = rel_path.lower()
        if any(m in low for m in cfg.get("classification.e2e_markers", []) or []):
            return "test.e2e"
        if any(m in low for m in cfg.get("classification.integration_markers", []) or []):
            return "test.integration"
    return matched


def sniff_binary(head: bytes) -> bool:
    return b"\x00" in head


def discover(repo_root: Path, cfg: Any) -> list[Discovered]:
    """Deterministic, sorted discovery. Never escapes the repository root."""
    root = repo_root.resolve()
    include: list[str] = cfg.get("discovery.include", ["**/*"]) or ["**/*"]
    exclude: list[str] = cfg.get("discovery.exclude", []) or []
    max_bytes = int(cfg.get("discovery.max_file_bytes", 2 << 20))
    follow = bool(cfg.get("discovery.follow_symlinks", False))
    max_depth = int(cfg.get("discovery.max_depth", 32))
    sniff_bytes = int(cfg.get("discovery.binary_sniff_bytes", 4096))
    gen_bytes = int(cfg.get("classification.generated_scan_bytes", 2048))
    head_bytes = max(sniff_bytes, gen_bytes)

    out: list[Discovered] = []
    seen_dirs: set[tuple[int, int]] = set()

    for abs_path, rel_path in _walk(root, follow, max_depth, seen_dirs, exclude):
        if not any_match(rel_path, include):
            continue
        if any_match(rel_path, exclude):
            continue
        try:
            st = os.stat(abs_path)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        size = st.st_size
        head = b""
        skip = ""
        if size > max_bytes:
            skip = "oversized"
        else:
            try:
                with open(abs_path, "rb") as fh:
                    head = fh.read(head_bytes)
            except (FileNotFoundError, PermissionError, OSError) as exc:
                skip = f"unreadable:{type(exc).__name__}"
        binary = sniff_binary(head) if head else False
        language = _language_for(rel_path, cfg)
        file_class = classify(rel_path, cfg, head)
        if binary:
            file_class = "binary"
        out.append(
            Discovered(
                rel_path=rel_path,
                abs_path=str(abs_path),
                byte_size=size,
                language=language,
                file_class=file_class,
                is_binary=binary,
                skip_reason=skip,
            )
        )
    out.sort(key=lambda d: d.rel_path)
    return out


def _walk(
    root: Path,
    follow: bool,
    max_depth: int,
    seen_dirs: set[tuple[int, int]],
    exclude: list[str],
) -> Iterator[tuple[Path, str]]:
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - defensive
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=follow)
                is_file = entry.is_file(follow_symlinks=follow)
                is_link = entry.is_symlink()
            except OSError:
                continue
            if is_link and not follow:
                continue
            if is_dir:
                if any_match(rel, exclude) or any_match(rel + "/x", exclude):
                    continue
                try:
                    st = entry.stat(follow_symlinks=follow)
                    key = (st.st_dev, st.st_ino)
                except OSError:
                    continue
                if key in seen_dirs:  # symlink loop / hardlinked dir
                    continue
                seen_dirs.add(key)
                # repo escape guard
                try:
                    real = path.resolve()
                    real.relative_to(root)
                except (ValueError, OSError):
                    continue
                stack.append((path, depth + 1))
            elif is_file:
                yield path, rel


def read_source(abs_path: str, max_bytes: int) -> tuple[bytes, str, str]:
    """Return (raw_bytes, decoded_text, encoding). Never raises on bad bytes."""
    with open(abs_path, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        return raw[:max_bytes], "", "oversized"
    try:
        return raw, raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw, raw.decode("utf-8", "replace"), "utf-8-replace"


def line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
