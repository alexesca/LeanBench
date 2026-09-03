"""Config layering with per-key source provenance.

Layer order (highest wins):
    built-in defaults -> /etc/leanvfs/config.toml -> ~/.config/leanvfs/config.toml
    -> <repo>/.leanvfs.toml -> <repo>/.leanvfs.local.toml -> CLI flags

Every consequential parameter is a config key. Nothing consequential is a Python
constant. `leanvfs config schema --json` enumerates the whole tunable surface so an
optimization agent can walk it programmatically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.10
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULTS_PATH = Path(__file__).with_name("defaults.toml")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]*$")

#: Keys whose value is a list of tables / non-identifier mapping; never flattened.
DEFAULT_PATH_RULES: list[dict[str, str]] = []

SOURCE_BUILTIN = "builtin-defaults"
SOURCE_CLI = "cli-flags"

#: Human documentation for the tunable surface. Anything not listed still appears in
#: the schema; this only enriches it.
SCHEMA_DOC: dict[str, str] = {
    "general.profile": "Active config profile preset (balanced|minimal|aggressive|rich|tests).",
    "general.workers": "Parallel parse workers. Ordering-sensitive work stays sequential.",
    "discovery.max_file_bytes": "Files larger than this are classified but not parsed.",
    "keywords.idf_refresh": "When the frozen IDF snapshot may be recomputed: sync|never|drift.",
    "keywords.idf_drift_threshold": "Fraction of corpus churn above which status warns about IDF drift.",
    "keywords.max_per_symbol": "Maximum keywords retained per symbol.",
    "keywords.max_per_file": "Maximum keywords retained per file.",
    "resolution.max_ambiguous": "Max R3 candidate edges emitted for an ambiguous name.",
    "budget.default_context_tokens": "Default token budget for get_context.",
    "budget.tokens_per_char": "Approximate-token-counter calibration (tokens per character).",
    "affected.max_depth": "Maximum reverse-edge hops for impact analysis.",
    "redaction.entropy_threshold": "Shannon entropy (bits/char) above which a long value is redacted.",
    "render.renderer": "Default renderer seam: compact|debug|json.",
    "mirror.enabled": "Write an on-disk mirror of rendered files (off by default).",
}


def _flattenable(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and _IDENT.match(k) for k in value
    )


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested tables into dotted keys, stopping at non-identifier maps."""
    out: dict[str, Any] = {}
    for key, value in obj.items():
        dotted = f"{prefix}{key}"
        if _flattenable(value) and value:
            out.update(flatten(value, dotted + "."))
        else:
            out[dotted] = value
    return out


def _load_toml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return None
    except OSError:
        return None


@dataclass
class Config:
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    path_rules: list[dict[str, str]] = field(default_factory=list)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)

    # -- access ---------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key not in self.values:
            raise KeyError(f"unknown config key: {key}")
        return self.values[key]

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def source_of(self, key: str) -> str:
        return self.sources.get(key, "unset")

    def section(self, prefix: str) -> dict[str, Any]:
        p = prefix.rstrip(".") + "."
        return {k[len(p) :]: v for k, v in sorted(self.values.items()) if k.startswith(p)}

    def float_(self, key: str) -> float:
        return float(self[key])

    def int_(self, key: str) -> int:
        return int(self[key])

    # -- derivation -----------------------------------------------------
    def overlay(self, updates: dict[str, Any], source: str) -> Config:
        new = Config(
            values=dict(self.values),
            sources=dict(self.sources),
            path_rules=list(self.path_rules),
            profiles=self.profiles,
            layers=self.layers + [source],
        )
        for key, value in updates.items():
            new.values[key] = value
            new.sources[key] = source
        return new

    def for_path(self, rel_path: str) -> Config:
        """Apply the first matching [[rules]] profile overlay for a repo-relative path."""
        for rule in self.path_rules:
            pattern = rule.get("match")
            profile = rule.get("profile")
            if not pattern or not profile:
                continue
            if _glob_match(rel_path, pattern):
                preset = self.profiles.get(profile)
                if preset:
                    return self.overlay(flatten(preset), f"rule:{pattern}->{profile}")
                return self
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.values, sort_keys=True, separators=(",", ":"), default=str)

    def digest(self) -> str:
        from .hashing import digest_text

        return digest_text(self.canonical_json())


def _glob_match(rel_path: str, pattern: str) -> bool:
    from .globs import glob_match

    return glob_match(rel_path, pattern)


def _config_layers(repo_root: Path | None) -> list[tuple[str, Path]]:
    layers: list[tuple[str, Path]] = [("/etc/leanvfs/config.toml", Path("/etc/leanvfs/config.toml"))]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    home_cfg = Path(xdg) / "leanvfs" / "config.toml" if xdg else Path.home() / ".config" / "leanvfs" / "config.toml"
    layers.append((str(home_cfg), home_cfg))
    if repo_root is not None:
        layers.append((f"{repo_root}/.leanvfs.toml", repo_root / ".leanvfs.toml"))
        layers.append((f"{repo_root}/.leanvfs.local.toml", repo_root / ".leanvfs.local.toml"))
    return layers


def load_config(
    repo_root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    *,
    skip_user_layers: bool = False,
) -> Config:
    """Resolve the full config with per-key provenance."""
    raw_defaults = _load_toml(DEFAULTS_PATH)
    if raw_defaults is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"missing built-in defaults at {DEFAULTS_PATH}")
    profiles = raw_defaults.pop("profiles", {})
    base = flatten(raw_defaults)
    base.pop("rules", None)

    cfg = Config(
        values=dict(base),
        sources={k: SOURCE_BUILTIN for k in base},
        path_rules=list(DEFAULT_PATH_RULES),
        profiles=profiles,
        layers=[SOURCE_BUILTIN],
    )

    file_layers: list[tuple[str, dict[str, Any]]] = []
    if not skip_user_layers:
        for label, path in _config_layers(repo_root):
            data = _load_toml(path)
            if data is not None:
                file_layers.append((label, data))
    elif repo_root is not None:
        for label, path in _config_layers(repo_root)[2:]:
            data = _load_toml(path)
            if data is not None:
                file_layers.append((label, data))

    # Determine the effective profile across all layers, then insert the preset just
    # above built-in defaults so explicit user settings still win over the preset.
    profile_name = base.get("general.profile", "balanced")
    profile_source = SOURCE_BUILTIN
    for label, data in file_layers:
        flat = flatten({k: v for k, v in data.items() if k != "rules"})
        if "general.profile" in flat:
            profile_name = flat["general.profile"]
            profile_source = label
    if cli_overrides and "general.profile" in cli_overrides:
        profile_name = cli_overrides["general.profile"]
        profile_source = SOURCE_CLI

    preset = profiles.get(profile_name)
    if preset is None and profile_name != "balanced":
        raise ValueError(f"unknown profile: {profile_name}")
    if preset:
        cfg = cfg.overlay(flatten(preset), f"profile:{profile_name}")
    cfg.values["general.profile"] = profile_name
    cfg.sources["general.profile"] = profile_source

    for label, data in file_layers:
        rules = data.get("rules")
        if isinstance(rules, list):
            cfg.path_rules = [r for r in rules if isinstance(r, dict)]
        flat = flatten({k: v for k, v in data.items() if k != "rules"})
        unknown = sorted(k for k in flat if k not in cfg.values)
        if unknown:
            raise ValueError(f"unknown config keys in {label}: {', '.join(unknown)}")
        cfg = cfg.overlay(flat, label)

    if cli_overrides:
        unknown = sorted(k for k in cli_overrides if k not in cfg.values)
        if unknown:
            raise ValueError(f"unknown config keys from CLI: {', '.join(unknown)}")
        cfg = cfg.overlay(cli_overrides, SOURCE_CLI)

    cfg.layers = [SOURCE_BUILTIN]
    return cfg


def parse_cli_override(text: str) -> tuple[str, Any]:
    """Parse ``key=value`` where value is TOML-ish (json first, then bare string)."""
    if "=" not in text:
        raise ValueError(f"--set expects key=value, got {text!r}")
    key, _, raw = text.partition("=")
    key = key.strip()
    raw = raw.strip()
    try:
        return key, json.loads(raw)
    except ValueError:
        return key, raw


def schema(cfg: Config) -> list[dict[str, Any]]:
    """Full tunable surface: key, type, default, current value, source, doc."""
    raw_defaults = _load_toml(DEFAULTS_PATH) or {}
    raw_defaults.pop("profiles", None)
    base = flatten(raw_defaults)
    out: list[dict[str, Any]] = []
    for key in sorted(cfg.values):
        value = cfg.values[key]
        out.append(
            {
                "key": key,
                "type": type(value).__name__,
                "default": base.get(key),
                "value": value,
                "source": cfg.source_of(key),
                "doc": SCHEMA_DOC.get(key, ""),
            }
        )
    return out
