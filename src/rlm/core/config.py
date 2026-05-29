"""Snipara Sandbox configuration management."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ExecutionProfile:
    """Predefined execution profile with resource limits."""

    timeout: int  # Seconds
    memory: str  # Docker memory limit (e.g., "512m", "2g")
    description: str


# Built-in execution profiles
EXECUTION_PROFILES: dict[str, ExecutionProfile] = {
    "quick": ExecutionProfile(
        timeout=5,
        memory="128m",
        description="Fast operations: simple math, string manipulation",
    ),
    "default": ExecutionProfile(
        timeout=30,
        memory="512m",
        description="Standard operations: data processing, algorithms",
    ),
    "analysis": ExecutionProfile(
        timeout=120,
        memory="2g",
        description="Heavy computation: large datasets, complex algorithms",
    ),
    "extended": ExecutionProfile(
        timeout=300,
        memory="4g",
        description="Long-running tasks: batch processing, extensive analysis",
    ),
}


def get_profile(name: str) -> ExecutionProfile:
    """Get an execution profile by name.

    Args:
        name: Profile name (quick, default, analysis, extended)

    Returns:
        ExecutionProfile with timeout and memory settings

    Raises:
        ValueError: If profile name is unknown
    """
    if name not in EXECUTION_PROFILES:
        available = ", ".join(EXECUTION_PROFILES.keys())
        raise ValueError(f"Unknown profile '{name}'. Available: {available}")
    return EXECUTION_PROFILES[name]


def _sandbox_env(name: str) -> AliasChoices:
    """Return the Snipara Sandbox env var name plus the legacy RLM fallback."""
    return AliasChoices(f"SNIPARA_SANDBOX_{name}", f"RLM_{name}")


class RLMConfig(BaseSettings):
    """Snipara Sandbox runtime configuration.

    Configuration can be set via:
    1. Environment variables (SNIPARA_SANDBOX_* prefix, RLM_* legacy fallback)
    2. snipara-sandbox.toml or legacy rlm.toml config file
    3. Direct instantiation
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Backend settings
    backend: str = Field(default="litellm", validation_alias=_sandbox_env("BACKEND"))
    model: str = Field(default="gpt-4o-mini", validation_alias=_sandbox_env("MODEL"))
    temperature: float = Field(default=0.0, validation_alias=_sandbox_env("TEMPERATURE"))
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SNIPARA_SANDBOX_LLM_API_KEY", "RLM_API_KEY"),
    )  # For direct OpenAI/Anthropic

    # Environment settings
    environment: str = Field(default="local", validation_alias=_sandbox_env("ENVIRONMENT"))
    trust_level: str = Field(
        default="sandboxed", validation_alias=_sandbox_env("TRUST_LEVEL")
    )  # sandboxed | docker | local
    docker_image: str = Field(
        default="python:3.11-slim", validation_alias=_sandbox_env("DOCKER_IMAGE")
    )
    docker_cpus: float = Field(default=1.0, validation_alias=_sandbox_env("DOCKER_CPUS"))
    docker_memory: str = Field(default="512m", validation_alias=_sandbox_env("DOCKER_MEMORY"))
    docker_network_disabled: bool = Field(
        default=True, validation_alias=_sandbox_env("DOCKER_NETWORK_DISABLED")
    )
    docker_mount_workspace: bool = Field(
        default=True, validation_alias=_sandbox_env("DOCKER_MOUNT_WORKSPACE")
    )
    docker_workspace_path: Path | None = Field(
        default=None, validation_alias=_sandbox_env("DOCKER_WORKSPACE_PATH")
    )
    docker_workspace_setup: str = Field(
        default="none", validation_alias=_sandbox_env("DOCKER_WORKSPACE_SETUP")
    )
    docker_workspace_install_command: str | None = Field(
        default=None, validation_alias=_sandbox_env("DOCKER_WORKSPACE_INSTALL_COMMAND")
    )
    docker_workspace_install_extras: list[str] = Field(
        default_factory=list,
        validation_alias=_sandbox_env("DOCKER_WORKSPACE_INSTALL_EXTRAS"),
    )
    docker_timeout: int = Field(default=30, validation_alias=_sandbox_env("DOCKER_TIMEOUT"))

    # Limits
    max_depth: int = Field(default=4, validation_alias=_sandbox_env("MAX_DEPTH"))
    max_subcalls: int = Field(default=12, validation_alias=_sandbox_env("MAX_SUBCALLS"))
    token_budget: int = Field(default=8000, validation_alias=_sandbox_env("TOKEN_BUDGET"))
    tool_budget: int = Field(default=20, validation_alias=_sandbox_env("TOOL_BUDGET"))
    timeout_seconds: int = Field(
        default=300, validation_alias=_sandbox_env("TIMEOUT_SECONDS")
    )  # 5 minutes default (increased from 2 for agentic tasks)
    parallel_tools: bool = Field(default=False, validation_alias=_sandbox_env("PARALLEL_TOOLS"))
    max_parallel: int = Field(default=5, validation_alias=_sandbox_env("MAX_PARALLEL"))

    # Sub-LLM Orchestration
    sub_calls_enabled: bool = Field(
        default=True, validation_alias=_sandbox_env("SUB_CALLS_ENABLED")
    )
    sub_calls_max_per_turn: int = Field(
        default=5, validation_alias=_sandbox_env("SUB_CALLS_MAX_PER_TURN")
    )
    sub_calls_budget_inheritance: float = Field(
        default=0.5, validation_alias=_sandbox_env("SUB_CALLS_BUDGET_INHERITANCE")
    )  # Fraction of parent's remaining budget
    sub_calls_max_cost_per_session: float = Field(
        default=1.0, validation_alias=_sandbox_env("SUB_CALLS_MAX_COST_PER_SESSION")
    )  # Max dollar cost for sub-calls per session

    # Security: File access restrictions
    # Paths that file tools can access. Empty list means current directory only.
    allowed_paths: list[Path] = Field(
        default_factory=list, validation_alias=_sandbox_env("ALLOWED_PATHS")
    )

    # Logging
    log_dir: Path = Field(
        default_factory=lambda: Path("./logs"), validation_alias=_sandbox_env("LOG_DIR")
    )
    verbose: bool = Field(default=False, validation_alias=_sandbox_env("VERBOSE"))
    log_level: str = Field(default="INFO", validation_alias=_sandbox_env("LOG_LEVEL"))

    # Snipara integration (optional)
    snipara_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SNIPARA_API_KEY",
            "SNIPARA_SANDBOX_SNIPARA_API_KEY",
            "RLM_SNIPARA_API_KEY",
        ),
    )
    snipara_project_slug: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SNIPARA_PROJECT_SLUG",
            "SNIPARA_SANDBOX_SNIPARA_PROJECT_SLUG",
            "RLM_SNIPARA_PROJECT_SLUG",
        ),
    )
    snipara_base_url: str = Field(
        default="https://api.snipara.com/mcp",
        validation_alias=AliasChoices(
            "SNIPARA_BASE_URL",
            "SNIPARA_SANDBOX_SNIPARA_BASE_URL",
            "RLM_SNIPARA_BASE_URL",
        ),
    )
    memory_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SNIPARA_MEMORY_ENABLED",
            "SNIPARA_SANDBOX_MEMORY_ENABLED",
            "RLM_MEMORY_ENABLED",
        ),
    )  # Enable Snipara memory tools.

    @field_validator("docker_workspace_install_extras", mode="before")
    @classmethod
    def _split_install_extras(cls, value: object) -> object:
        """Accept a comma/space-separated string for the install-extras env var."""
        if isinstance(value, str):
            return [token for token in re.split(r"[,\s]+", value.strip()) if token]
        return value

    @property
    def snipara_enabled(self) -> bool:
        """Check if Snipara integration is configured."""
        return bool(self.snipara_api_key and self.snipara_project_slug)

    def get_snipara_url(self) -> str | None:
        """Get the full Snipara MCP URL."""
        if not self.snipara_enabled:
            return None
        return f"{self.snipara_base_url}/{self.snipara_project_slug}"


def load_project_env(base_dir: Path | None = None, override: bool = False) -> Path | None:
    """Load key/value pairs from the nearest `.env` file into `os.environ`.

    This keeps CLI commands and settings loading consistent even when the
    optional `python-dotenv` dependency is not installed.

    Args:
        base_dir: Directory to start searching from. Defaults to the current
            working directory.
        override: Whether to overwrite existing environment variables.

    Returns:
        The `.env` path that was loaded, or `None` if no file was found.
    """
    search_root = (base_dir or Path.cwd()).resolve()

    for candidate_dir in (search_root, *search_root.parents):
        env_path = candidate_dir / ".env"
        if not env_path.exists() or not env_path.is_file():
            if (candidate_dir / ".git").exists():
                break
            continue

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("export "):
                line = line[len("export ") :].lstrip()

            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
            if not tokens:
                continue

            assignment = " ".join(tokens)
            if "=" not in assignment:
                continue

            key, value = assignment.split("=", 1)
            key = key.strip()
            if not key:
                continue

            if override or key not in os.environ:
                os.environ[key] = value

        return env_path

    return None


def load_config(config_path: Path | None = None) -> RLMConfig:
    """Load configuration from file and environment.

    Priority (highest to lowest):
    1. Environment variables
    2. Config file (if provided)
    3. Default values

    Args:
        config_path: Optional path to a Snipara Sandbox config file.

    Returns:
        RLMConfig instance
    """
    config_data: dict[str, Any] = {}

    # Load project-level `.env` support explicitly so the CLI works without
    # requiring python-dotenv to be installed.
    load_project_env(config_path.parent if config_path else Path.cwd())

    # Try to load from config file. Prefer the Snipara Sandbox filename while
    # continuing to read rlm.toml for existing projects.
    if config_path is None:
        config_path = Path("snipara-sandbox.toml")
        if not config_path.exists() and Path("rlm.toml").exists():
            config_path = Path("rlm.toml")

    if config_path.exists():
        try:
            import tomllib

            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
            config_data = toml_data.get("snipara_sandbox", toml_data.get("rlm", {}))
        except ImportError:
            # Python < 3.11, try tomli
            try:
                import tomli

                with open(config_path, "rb") as f:
                    toml_data = tomli.load(f)
                config_data = toml_data.get("snipara_sandbox", toml_data.get("rlm", {}))
            except ImportError:
                pass  # No TOML library available, use defaults

    return RLMConfig(**config_data)


def save_config(config: RLMConfig, config_path: Path) -> None:
    """Save configuration to a TOML file.

    Args:
        config: RLMConfig instance to save
        config_path: Path to save the config file
    """
    lines = [
        "# Snipara Sandbox Configuration",
        "",
        "[snipara_sandbox]",
        f'backend = "{config.backend}"',
        f'model = "{config.model}"',
        f"temperature = {config.temperature}",
        f'environment = "{config.environment}"',
        f"max_depth = {config.max_depth}",
        f"max_subcalls = {config.max_subcalls}",
        f"token_budget = {config.token_budget}",
        f"verbose = {str(config.verbose).lower()}",
        "",
        "# Docker settings",
        f'docker_image = "{config.docker_image}"',
        f"docker_cpus = {config.docker_cpus}",
        f'docker_memory = "{config.docker_memory}"',
        f"docker_network_disabled = {str(config.docker_network_disabled).lower()}",
        f"docker_mount_workspace = {str(config.docker_mount_workspace).lower()}",
        (
            f'docker_workspace_path = "{config.docker_workspace_path}"'
            if config.docker_workspace_path
            else '# docker_workspace_path = "."'
        ),
        f'docker_workspace_setup = "{config.docker_workspace_setup}"',
        (
            f"docker_workspace_install_command = {json.dumps(config.docker_workspace_install_command)}"
            if config.docker_workspace_install_command
            else '# docker_workspace_install_command = "python -m pip install -e \\".[dev]\\""'
        ),
        (
            f"docker_workspace_install_extras = {json.dumps(config.docker_workspace_install_extras)}"
            if config.docker_workspace_install_extras
            else '# docker_workspace_install_extras = ["fastapi", "pydantic-settings"]'
        ),
        "",
        "# Security: File access restrictions",
        "# Paths that file tools can access. Empty list means current directory only.",
        f"allowed_paths = {[str(p) for p in config.allowed_paths]}",
        "",
        "# Snipara integration (optional)",
        "# Get your API key at https://snipara.com/dashboard",
    ]

    if config.snipara_api_key:
        lines.append(f'snipara_api_key = "{config.snipara_api_key}"')
    else:
        lines.append('# snipara_api_key = "snp-..."')

    if config.snipara_project_slug:
        lines.append(f'snipara_project_slug = "{config.snipara_project_slug}"')
    else:
        lines.append('# snipara_project_slug = "your-project"')

    config_path.write_text("\n".join(lines) + "\n")
