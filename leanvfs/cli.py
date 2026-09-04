"""LeanVFS command line.

Deliberately plain argparse: the CLI is a thin shell over the engine, and every
consequential parameter is a config key rather than a flag, so that the benchmark can
sweep the experiment surface without the CLI growing a flag per tunable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import load_config, parse_cli_override, schema
from .indexer import Indexer
from .queries import QueryEngine
from .registry import FactRegistry
from .render.compact import CompactRenderer
from .state import load_state, state_dir_for
from .store import Store
from .views import build_file_view


def _open(repo: Path, overrides: list[str], *, fresh: bool = False):
    cfg = load_config(repo, dict(parse_cli_override(o) for o in overrides))
    state_dir = state_dir_for(repo, cfg.get("general.state_dir") or None)
    state_dir.mkdir(parents=True, exist_ok=True)
    db = state_dir / "index.sqlite"
    if fresh and db.exists():
        db.unlink()
    return cfg, state_dir, Store(db)


def _open_for_query(repo: Path, overrides: list[str], *, quiet: bool = False):
    """Open the index for reading, bringing it up to date first.

    An agent that edits a file and then asks a question must not be answered from the
    previous state. Making every query self-healing is the difference between a tool that
    is occasionally, silently wrong and one that can be trusted without ceremony -- and
    it is cheap, because an unchanged file costs one hash and nothing else (~50 ms across
    125 files). Disable with `--set general.auto_sync=false` for a frozen index.
    """
    cfg, state_dir, store = _open(repo, overrides)
    if not bool(cfg.get("general.auto_sync", True)):
        return cfg, state_dir, store

    indexer = Indexer(repo, store, cfg, state_dir=state_dir)
    started = time.perf_counter()
    if store.count("files") == 0:
        # Never indexed. Do it now rather than returning an empty result set that looks
        # like "nothing matches" instead of "nothing is indexed".
        result = indexer.full_sync()
        store.set_meta("source_bytes", str(result.source_bytes))
        store.set_meta("cold_index_ms", str((time.perf_counter() - started) * 1000.0))
        if not quiet:
            print(
                f"[leanvfs] first run: indexed {result.files} files "
                f"in {(time.perf_counter() - started) * 1000:.0f} ms",
                file=sys.stderr,
            )
    else:
        result = indexer.incremental_sync()
        if result.reparsed and not quiet:
            print(
                f"[leanvfs] refreshed {result.reparsed} changed file(s) "
                f"in {(time.perf_counter() - started) * 1000:.0f} ms",
                file=sys.stderr,
            )
    return cfg, state_dir, store


def cmd_sync(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, state_dir, store = _open(repo, args.set, fresh=True)
    started = time.perf_counter()
    result = Indexer(repo, store, cfg, state_dir=state_dir).full_sync()
    elapsed = time.perf_counter() - started
    store.set_meta("cold_index_ms", str(elapsed * 1000.0))
    store.set_meta("source_bytes", str(result.source_bytes))
    kbps = (result.source_bytes / 1024.0) / max(elapsed, 1e-9)
    print(json.dumps(result.as_dict(), indent=2))
    print(
        f"throughput {kbps:.0f} KB/s   index {store.size_bytes()} bytes "
        f"({store.size_bytes() / max(result.source_bytes, 1):.2f}x source)"
    )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open_for_query(repo, args.set)
    engine = QueryEngine(store, cfg)
    from .render.compact import render_hits

    print(render_hits(engine.search(args.query, args.limit)))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open_for_query(repo, args.set)
    from .render.compact import render_context

    engine = QueryEngine(store, cfg)
    print(render_context(engine.get_context(args.symbol, args.budget)))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open_for_query(repo, args.set)
    view = build_file_view(store, args.path, cfg)
    if view is None:
        print(f"not indexed: {args.path}", file=sys.stderr)
        return 1
    print(CompactRenderer(cfg).render(view))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open(repo, args.set)
    print(json.dumps(QueryEngine(store, cfg).get_stats(), indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, state_dir, store = _open(repo, args.set)
    state = load_state(state_dir)
    from .keywords import drift

    value = drift(state.idf_doc_count, state.files_added_since_idf, state.files_removed_since_idf)
    threshold = float(cfg.get("keywords.idf_drift_threshold", 0.15))
    print(
        json.dumps(
            {
                "generation": store.generation(),
                "idf_generation": store.idf_generation(),
                "index_state": store.index_state(),
                "idf_drift": round(value, 4),
                "idf_drift_threshold": threshold,
                # Reported, never acted on: an automatic resync would make runs
                # nondeterministic, so the benchmark must opt into it knowingly.
                "advice": "sync advisable" if value > threshold else "ok",
            },
            indent=2,
        )
    )
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve(), dict(parse_cli_override(o) for o in args.set))
    for key in sorted(cfg.values):
        print(f"{key} = {cfg.values[key]!r}   [{cfg.source_of(key)}]")
    return 0


def cmd_config_explain(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve(), dict(parse_cli_override(o) for o in args.set))
    if args.key not in cfg:
        print(f"unknown key {args.key}", file=sys.stderr)
        return 1
    print(f"{args.key} = {cfg.get(args.key)!r}   from {cfg.source_of(args.key)}")
    return 0


def cmd_config_schema(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve())
    print(json.dumps(schema(cfg), indent=2, sort_keys=True))
    return 0


def cmd_facts_kinds(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve())
    print(json.dumps(FactRegistry(cfg).describe(), indent=2, sort_keys=True))
    return 0


def cmd_explain_budget(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open(repo, args.set)
    engine = QueryEngine(store, cfg)
    context = engine.get_context(args.symbol, args.budget)
    print(json.dumps(context.get("budget_report", {}), indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify

    repo = Path(args.repo).resolve()
    cfg, _sd, store = _open(repo, args.set)
    report = verify(repo, store, cfg)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


def cmd_update(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg, state_dir, store = _open(repo, args.set)
    result = Indexer(repo, store, cfg, state_dir=state_dir).incremental_sync()
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def cmd_server(args: argparse.Namespace) -> int:
    from .server import Server

    return Server().serve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leanvfs")
    parser.add_argument("--repo", default=".", help="repository root")
    parser.add_argument(
        "--set", action="append", default=[], help="config override, key=value (TOML-parsed)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="index the repository from clean").set_defaults(fn=cmd_sync)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("context")
    p.add_argument("symbol")
    p.add_argument("--budget", type=int, default=None)
    p.set_defaults(fn=cmd_context)

    p = sub.add_parser("render")
    p.add_argument("path")
    p.set_defaults(fn=cmd_render)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("update", help="incremental sync of changed files").set_defaults(fn=cmd_update)
    v = sub.add_parser("verify", help="assert incremental state equals a clean rebuild")
    v.add_argument("--pin-idf", action="store_true", default=True)
    v.set_defaults(fn=cmd_verify)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("explain-budget")
    p.add_argument("symbol")
    p.add_argument("--budget", type=int, default=None)
    p.set_defaults(fn=cmd_explain_budget)

    cfg = sub.add_parser("config").add_subparsers(dest="sub", required=True)
    cfg.add_parser("show").set_defaults(fn=cmd_config_show)
    c = cfg.add_parser("explain")
    c.add_argument("key")
    c.set_defaults(fn=cmd_config_explain)
    s = cfg.add_parser("schema")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_config_schema)

    facts = sub.add_parser("facts").add_subparsers(dest="sub", required=True)
    facts.add_parser("kinds").set_defaults(fn=cmd_facts_kinds)

    sub.add_parser("benchmark-server", help="LeanBench candidate protocol over stdio").set_defaults(
        fn=cmd_server
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))
