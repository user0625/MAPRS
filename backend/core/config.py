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
    database_url: str = "sqlite:///backend/data/tasks.db"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    task_heartbeat_seconds: int = Field(default=15, ge=1)
    task_stale_after_seconds: int = Field(default=300, ge=30)
    checkpoint_schema_version: int = Field(default=1, ge=1)
    sse_heartbeat_seconds: int = Field(default=15, ge=1)
    ask_candidate_count: int = Field(default=20, ge=1, le=100)
    ask_evidence_count: int = Field(default=6, ge=1, le=50)
    ask_rrf_k: int = Field(default=60, ge=1, le=1000)
    ask_vector_min_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    ask_reranker_mode: Literal["disabled", "shadow", "enabled"] = "disabled"
    ask_reranker_provider: Literal["openai_compatible"] = "openai_compatible"
    ask_reranker_model: str = ""
    ask_reranker_api_key: str | None = None
    ask_reranker_base_url: str | None = None
    ask_reranker_timeout: float = Field(default=1.0, gt=0, le=30)
    ask_evidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    ask_answerability_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    ask_calibration_version: str = Field(default="uncalibrated", min_length=1, max_length=128)
    ask_rewrite_max_tokens: int = Field(default=160, ge=16, le=512)
    ask_retrieval_cache_size: int = Field(default=8, ge=1, le=128)
    celery_task_max_retries: int = Field(default=3, ge=0)
    celery_visibility_timeout: int = Field(default=3600, ge=60)

    # Runtime config
    default_top_k: int = Field(default=5, ge=1, le=50)
    chunk_size: int = Field(default=1200, ge=100)
    chunk_overlap: int = Field(default=150, ge=0)

    # External request policy
    request_connect_timeout: float = Field(default=10.0, gt=0, le=300)
    request_read_timeout: float = Field(default=60.0, gt=0, le=600)
    request_total_budget: float = Field(default=120.0, gt=0, le=1800)
    request_max_retries: int = Field(default=2, ge=0, le=10)
    request_backoff_base: float = Field(default=1.0, ge=0, le=60)
    request_backoff_max: float = Field(default=8.0, ge=0, le=300)

    # Uploads and lifecycle
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=1024**3)
    file_retention_days: int = Field(default=30, ge=0, le=3650)
    prompt_set_version: str = Field(default="v1", min_length=1, max_length=64)

    # Phase D report quality
    hierarchical_page_threshold: int = Field(default=20, ge=1, le=10000)
    hierarchical_char_threshold: int = Field(default=60000, ge=1000, le=100000000)
    verifier_enabled: bool = True
    quality_pass_score: int = Field(default=75, ge=0, le=100)
    citation_validity_min_score: int = Field(default=80, ge=0, le=100)
    max_custom_sections: int = Field(default=20, ge=1, le=50)

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
        "ask_reranker_api_key",
        "ask_reranker_base_url",
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
