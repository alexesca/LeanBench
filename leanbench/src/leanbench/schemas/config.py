"""Config models. Every resolved value carries the layer it came from (gate P1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

#: Precedence, lowest to highest (build spec: defaults -> global -> repo -> suite -> CLI).
LAYERS: tuple[str, ...] = ("defaults", "global", "repo", "suite", "cli")


class ConfigValue(BaseModel):
    """A single resolved setting plus its provenance."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any
    source: str
    origin: str | None = None

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.key} = {self.value!r}  [{self.source}]"


class ResolvedConfig(BaseModel):
    """Flat dotted-key map of ConfigValue. Accessed via `get`, never by attribute, so
    that no call site can silently invent a key that has no default."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, ConfigValue] = {}

    def get(self, key: str) -> Any:
        if key not in self.values:
            raise KeyError(f"unknown config key {key!r}; add it to config/defaults.toml")
        return self.values[key].value

    def source_of(self, key: str) -> str:
        if key not in self.values:
            raise KeyError(f"unknown config key {key!r}")
        return self.values[key].source

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_str(self, key: str) -> str:
        return str(self.get(key))

    def get_list(self, key: str) -> list[Any]:
        value = self.get(key)
        if not isinstance(value, list):
            raise TypeError(f"config key {key!r} is not a list")
        return list(value)

    def as_plain_dict(self) -> dict[str, Any]:
        return {k: self.values[k].value for k in sorted(self.values)}

    def as_provenance_dict(self) -> dict[str, dict[str, Any]]:
        return {
            k: {"value": self.values[k].value, "source": self.values[k].source}
            for k in sorted(self.values)
        }
