from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """
    Global application settings.

    This class reads configuration from environment variables and .env file.
    Business modules should import settings from get_settings(), rather than
    reading environment variables directly.
    """

    model_config = SettingsConfigDict(
        env_file="backend/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Basic app config
    app_name: str = "Multi-Agent Paper Reader System"
    app_env: Literal["development", "testing", "production"] = "development"
    debug: bool = True

    # LLM config
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_vendor: Literal["mock", "qwen", "deepseek", "openai", "custom"] = "mock"
    llm_model: str = "mock-llm"

    # Embedding config
    embedding_provider: Literal["mock", "openai_compatible"] = "mock"
    embedding_vendor: Literal["mock", "qwen", "openai", "custom"] = "mock"
    embedding_model: str = "mock-embedding"

    # OpenAI-compatible API config
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None

    # Paths
    project_root: Path = Field(default_factory=lambda: Path.cwd())
    data_dir: Path = Path("backend/data")
    raw_data_dir: Path = Path("backend/data/raw")
    processed_data_dir: Path = Path("backend/data/processed")
    output_dir: Path = Path("backend/outputs")
    report_dir: Path = Path("backend/outputs/reports")
    log_dir: Path = Path("backend/outputs/logs")

    # Runtime config
    default_top_k: int = Field(default=5, ge=1, le=50)
    chunk_size: int = Field(default=1200, ge=100)
    chunk_overlap: int = Field(default=150, ge=0)

    @field_validator(
        "project_root",
        "data_dir",
        "raw_data_dir",
        "processed_data_dir",
        "output_dir",
        "report_dir",
        "log_dir",
        mode="before",
    )
    @classmethod
    def convert_to_path(cls, value: str | Path) -> Path:
        return Path(value)

    @field_validator(
        "llm_api_key",
        "llm_base_url",
        "embedding_api_key",
        "embedding_base_url",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def resolve_path(self, path: str | Path) -> Path:
        """
        Resolve a relative path against project_root.
        """

        path = Path(path)
        if path.is_absolute():
            return path
        return self.project_root / path

    def ensure_directories(self) -> None:
        """
        Create project runtime directories if they do not exist.
        """

        for directory in [
            self.data_dir,
            self.raw_data_dir,
            self.processed_data_dir,
            self.output_dir,
            self.report_dir,
            self.log_dir,
        ]:
            self.resolve_path(directory).mkdir(parents=True, exist_ok=True)

    @property
    def use_mock_llm(self) -> bool:
        """Return whether the system should use mock LLM."""
        return self.llm_provider == "mock"

    @property
    def use_real_llm(self) -> bool:
        """Return whether the system should use a real OpenAI-compatible LLM."""
        return self.llm_provider == "openai_compatible"

    @property
    def use_mock_embedding(self) -> bool:
        """Return whether the system should use mock embedding."""
        return self.embedding_provider == "mock"

    @property
    def use_real_embedding(self) -> bool:
        """Return whether the system should use a real OpenAI-compatible embedding API."""
        return self.embedding_provider == "openai_compatible"


@lru_cache
def get_settings() -> AppSettings:
    """
    Return cached global settings.
    """

    settings = AppSettings()
    settings.ensure_directories()
    return settings