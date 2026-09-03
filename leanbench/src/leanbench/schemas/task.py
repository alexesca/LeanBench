"""TASKS.md §1 task/gold schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from leanbench.schemas.common import CATEGORIES, DIFFICULTIES, PARAPHRASE_IDS


class GoldRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class Gold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    ranges: list[GoldRange] = Field(default_factory=list)
    relationships: list[list[str]] = Field(default_factory=list)
    justification: str = ""


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paraphrase_id: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tool_calls: int | None = None
    max_repository_tokens: int | None = None
    wall_clock_s: float | None = None


class Task(BaseModel):
    """A single task file. Field-level validity only; cross-field/gold-resolution rules
    live in the pure validator (`leanbench.scoring.task_rules`)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = 1
    repository: str
    commit: str
    category: str
    difficulty: str
    authored_by: str = ""
    reviewed_by: str = ""
    authored_at: str = ""
    tags: list[str] = Field(default_factory=list)
    prompt: str = ""
    probes: list[Probe] = Field(default_factory=list)
    gold: Gold = Field(default_factory=Gold)
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)

    #: Absolute source path, populated by the loader.
    source_path: str | None = None


VALID_CATEGORIES = frozenset(CATEGORIES)
VALID_DIFFICULTIES = frozenset(DIFFICULTIES)
VALID_PARAPHRASE_IDS = frozenset(PARAPHRASE_IDS)


class TaskIssue(BaseModel):
    """One validation finding. `severity` 'error' blocks the task from a run."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    severity: str
    code: str
    message: str


class SuiteSpec(BaseModel):
    """`suite.toml` for a suite directory."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "0"
    description: str = ""
    released: bool = False
    #: Corpus repository id and the exact commit every task in the suite pins.
    repository: str = ""
    commit: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    repository_root: str | None = None
    #: Suite gate (TASKS.md section 5), reported in every run summary.
    informative_task_rate_threshold: float = 0.60
    #: Versioned aggregate weights. Lives in suite config, never in code, so that
    #: `leanbench rescore` can apply a new formula to an old run (docs/scoring.md).
    scoring: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
