"""Versioned, provider-independent repository validation contract."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA = "snipara.sandbox.validation.v1"


class ValidationRequest(BaseModel):
    """A deterministic command in an explicit, disposable project snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["snipara.sandbox.validation.v1"] = SCHEMA
    workspace: str
    argv: list[str] = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1, max_length=512)
    profile: Literal["python", "node", "mixed"] = "python"
    cwd: str = "."
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    cpus: float = Field(default=2.0, gt=0, le=8)
    memory_mb: int = Field(default=2048, ge=64, le=8192)
    pids: int = Field(default=128, ge=16, le=512)
    disk_mb: int = Field(default=2048, ge=16, le=4096)
    artifacts: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("workspace")
    @classmethod
    def absolute_workspace(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("workspace must be absolute")
        return value

    @field_validator("image")
    @classmethod
    def image_reference(cls, value: str) -> str:
        if value.startswith("-") or any(c.isspace() for c in value):
            raise ValueError("invalid image reference")
        return value

    @field_validator("cwd")
    @classmethod
    def safe_cwd(cls, value: str) -> str:
        return safe_relative_path(value)

    @field_validator("artifacts")
    @classmethod
    def safe_artifacts(cls, values: list[str]) -> list[str]:
        return [safe_relative_path(value, allow_dot=False) for value in values]

    @field_validator("argv")
    @classmethod
    def bounded_argv(cls, values: list[str]) -> list[str]:
        if any(not value or "\x00" in value or len(value) > 4096 for value in values):
            raise ValueError("argv contains an empty, oversized or NUL argument")
        if sum(map(len, values)) > 16384:
            raise ValueError("argv is too large")
        return values


def safe_relative_path(value: str, *, allow_dot: bool = True) -> str:
    """Reject traversal and portable path ambiguities before any filesystem I/O."""
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or path.is_absolute()
        or ".." in path.parts
        or (not allow_dot and value == ".")
    ):
        raise ValueError("expected a safe relative path")
    return path.as_posix()
