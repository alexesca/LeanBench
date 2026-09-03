"""Wire models for PROTOCOL.md §2, §3 and §5. Validation here is the *only* place a
candidate response is admitted; anything that fails is `invalid_response`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from leanbench.schemas.common import ErrorCode, IndexState
from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1


class Request(BaseModel):
    """PROTOCOL.md §2 request envelope."""

    model_config = ConfigDict(extra="forbid")

    id: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)
    token_budget: int | None = None
    format: Literal["compact", "json"] = "compact"


class ResponseMeta(BaseModel):
    """PROTOCOL.md §3 success `meta`."""

    model_config = ConfigDict(extra="allow")

    tokens_approx: int
    truncated: bool
    index_state: IndexState
    dropped: dict[str, int] | None = None
    generation: int | None = None
    idf_generation: int | None = None
    elapsed_ms: float | None = None


class OkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["ok"]
    result: dict[str, Any]
    meta: ResponseMeta


class ProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["indexing"]
    progress: float = Field(ge=0.0, le=1.0)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["error"]
    code: ErrorCode
    message: str = ""
    retryable: bool = False


Response = Annotated[
    OkResponse | ProgressResponse | ErrorResponse,
    Field(discriminator="status"),
]


class ResponseEnvelope(BaseModel):
    """Wrapper so that a single `model_validate` drives the discriminated union."""

    model_config = ConfigDict(extra="forbid")

    response: Response


# --- PROTOCOL.md §5 manifest ---------------------------------------------------


class CandidateInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "0.0.0"


class RuntimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class TimeoutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startup_s: float = 10.0
    prepare_s: float = 600.0
    op_s: float = 30.0
    shutdown_s: float = 5.0


class CandidateManifest(BaseModel):
    """`leanbench-candidate.toml` (PROTOCOL.md §5)."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    candidate: CandidateInfo
    runtime: RuntimeSpec
    timeouts: TimeoutSpec = Field(default_factory=TimeoutSpec)
    capabilities: dict[str, bool] = Field(default_factory=dict)

    #: Absolute path the manifest was loaded from; populated by the loader, not TOML.
    manifest_path: str | None = None

    @property
    def declared_capabilities(self) -> frozenset[str]:
        return frozenset(sorted(k for k, v in self.capabilities.items() if v))


class CandidateDigests(BaseModel):
    """PROTOCOL.md §6."""

    model_config = ConfigDict(extra="forbid")

    binary_digest: str
    manifest_digest: str
    config_digest: str | None = None
