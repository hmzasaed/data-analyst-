"""Runtime settings.

Defaults are deliberately restrictive: no network for sandboxed code, small
limits, third-party LLM calls disabled unless explicitly consented, and dataset
content redacted before it can leave the process.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSIGHTFLOW_SANDBOX_", extra="ignore")

    enabled: bool = True
    timeout_seconds: int = Field(default=120, ge=5, le=1800)
    memory_limit_bytes: int = Field(default=2 * 1024**3, ge=64 * 1024**2)
    cpu_seconds: int = Field(default=120, ge=1, le=1800)
    max_processes: int = Field(default=64, ge=1, le=1024)
    max_output_bytes: int = Field(default=1_000_000, ge=1024)
    max_file_descriptors: int = Field(default=64, ge=8)
    allow_network: bool = False
    allow_generated_code: bool = True
    container_runtime: str | None = None
    allowed_imports: tuple[str, ...] = Field(
        default=(
            "math",
            "statistics",
            "datetime",
            "json",
            "re",
            "collections",
            "itertools",
            "functools",
            "decimal",
            "fractions",
            "numpy",
            "polars",
            "pandas",
            "scipy",
            "matplotlib",
        )
    )

    @field_validator("allowed_imports")
    @classmethod
    def _as_tuple(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)  # type: ignore[arg-type]


class UploadSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSIGHTFLOW_UPLOAD_", extra="ignore")

    max_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    max_rows: int = Field(default=5_000_000, ge=1)
    max_columns: int = Field(default=1_000, ge=1)
    allowed_extensions: tuple[str, ...] = (".csv", ".tsv", ".txt", ".xlsx")
    allowed_media_types: tuple[str, ...] = (
        "text/csv",
        "text/plain",
        "text/tab-separated-values",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/octet-stream",
    )


class LLMSettings(BaseSettings):
    """Model routing. Model ids are configuration, never hardcoded in agents."""

    model_config = SettingsConfigDict(env_prefix="INSIGHTFLOW_LLM_", extra="ignore")

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    fast_model: str = "gpt-4o-mini"
    reasoning_model: str = "gpt-4o"
    request_timeout_seconds: float = Field(default=60.0, ge=1.0)
    max_output_tokens: int = Field(default=4096, ge=256)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    fast_reasoning_effort: str = "low"
    reasoning_effort: str = "medium"
    max_dataset_rows_to_model: int = Field(default=50, ge=0)
    max_tokens_per_request: int = Field(default=120_000, ge=1_000)

    def route_for(self, *, complex_reasoning: bool) -> str:
        return self.reasoning_model if complex_reasoning else self.fast_model


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSIGHTFLOW_", extra="ignore")

    app_name: str = "InsightFlow"
    environment: str = "local"
    artifact_root: Path = Path("artifacts")
    state_database_url: str = "sqlite:///./artifacts/insightflow.db"
    log_level: str = "INFO"
    retention_days: int = Field(default=7, ge=1, le=365)
    api_prefix: str = "/api/v1"
    enable_websockets: bool = True

    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    def artifact_path(self, *parts: str) -> Path:
        return self.artifact_root.joinpath(*parts)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings singleton."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Testing hook: drop the cached settings so env changes take effect."""
    global _settings  # noqa: PLW0603
    _settings = None
