"""Layered configuration: defaults -> global -> repo -> suite -> CLI.

Every resolved value remembers which layer produced it, which is what
`leanbench config show` prints (gate P1).
"""

from __future__ import annotations

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from leanbench.kernel.errors import ConfigError
from leanbench.schemas.config import LAYERS, ConfigValue, ResolvedConfig

DEFAULTS_PATH = Path(__file__).with_name("defaults.toml")
GLOBAL_CONFIG_ENV = "LEANBENCH_CONFIG"
GLOBAL_CONFIG_PATH = Path.home() / ".config" / "leanbench" / "config.toml"
REPO_CONFIG_NAME = "leanbench.toml"


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested tables -> dotted keys. Lists are leaves (a K-list is one setting)."""
    out: dict[str, Any] = {}
    for key in sorted(data):
        value = data[key]
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from exc


def load_defaults() -> dict[str, Any]:
    return _flatten(_read_toml(DEFAULTS_PATH))


def _layer_from_file(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None or not path.is_file():
        return {}, None
    return _flatten(_read_toml(path)), str(path)


def find_repo_config(start: Path) -> Path | None:
    """Nearest `leanbench.toml` walking up from `start`."""
    current = start.resolve()
    for candidate_dir in [current, *current.parents]:
        candidate = candidate_dir / REPO_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def resolve_config(
    *,
    global_path: Path | None = None,
    repo_path: Path | None = None,
    suite_overrides: Mapping[str, Any] | None = None,
    suite_origin: str | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    search_from: Path | None = None,
    use_env: bool = True,
) -> ResolvedConfig:
    """Resolve all layers. Unknown keys in any non-default layer are an error: a typo in
    a config file must not silently do nothing."""
    defaults = load_defaults()

    if global_path is None and use_env:
        env_value = os.environ.get(GLOBAL_CONFIG_ENV)
        if env_value:
            global_path = Path(env_value)
        elif GLOBAL_CONFIG_PATH.is_file():
            global_path = GLOBAL_CONFIG_PATH
    if repo_path is None and search_from is not None:
        repo_path = find_repo_config(search_from)

    global_layer, global_origin = _layer_from_file(global_path)
    repo_layer, repo_origin = _layer_from_file(repo_path)
    suite_layer = _flatten(dict(suite_overrides or {}))
    cli_layer = {k: v for k, v in dict(cli_overrides or {}).items() if v is not None}

    layers: list[tuple[str, dict[str, Any], str | None]] = [
        ("defaults", defaults, str(DEFAULTS_PATH)),
        ("global", global_layer, global_origin),
        ("repo", repo_layer, repo_origin),
        ("suite", suite_layer, suite_origin),
        ("cli", cli_layer, "<cli>"),
    ]

    values: dict[str, ConfigValue] = {}
    for layer_name, layer, origin in layers:
        for key in sorted(layer):
            if layer_name != "defaults" and key not in defaults:
                raise ConfigError(
                    f"unknown config key {key!r} set by layer {layer_name!r}"
                    f" ({origin or 'inline'}); it has no default"
                )
            values[key] = ConfigValue(key=key, value=layer[key], source=layer_name, origin=origin)
    return ResolvedConfig(values=values)


def parse_cli_overrides(pairs: Iterable[str]) -> dict[str, Any]:
    """`--set key=value` pairs. Values are parsed as TOML fragments so that
    `retrieval.recall_k=[1,3]` and `run.log_level=DEBUG` both work."""
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConfigError(f"override {pair!r} is not of the form key=value")
        key, raw = pair.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        try:
            parsed = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            parsed = raw
        out[key] = parsed
    return out


__all__ = ["LAYERS", "parse_cli_overrides", "resolve_config"]
