from pathlib import Path

from backend.core.config import AppSettings


def test_app_settings_default_values():
    settings = AppSettings()

    assert settings.app_name == "Multi-Agent Paper Reader System"
    assert settings.llm_provider == "mock"
    assert settings.default_top_k == 5


def test_resolve_relative_path():
    settings = AppSettings(project_root=Path("/tmp/project"))

    resolved = settings.resolve_path("data/raw/example.pdf")

    assert resolved == Path("/tmp/project/data/raw/example.pdf")


def test_use_mock_llm_property():
    settings = AppSettings(llm_provider="mock")

    assert settings.use_mock_llm is True
    assert settings.use_openai_llm is False