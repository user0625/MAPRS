from pathlib import Path

import pytest

from backend.llm.prompt_loader import PromptTemplateError, PromptTemplateLoader


def test_prompt_loader_loads_template(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    template_path = prompt_dir / "test.md"
    template_path.write_text("Hello {name}", encoding="utf-8")

    loader = PromptTemplateLoader(prompt_dir=prompt_dir)

    template = loader.load("test.md")

    assert template == "Hello {name}"


def test_prompt_loader_renders_template(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    template_path = prompt_dir / "test.md"
    template_path.write_text("Hello {name}", encoding="utf-8")

    loader = PromptTemplateLoader(prompt_dir=prompt_dir)

    rendered = loader.render("test.md", name="Agent")

    assert rendered == "Hello Agent"


def test_prompt_loader_rejects_missing_template(tmp_path):
    loader = PromptTemplateLoader(prompt_dir=tmp_path)

    with pytest.raises(PromptTemplateError):
        loader.load("missing.md")


def test_prompt_loader_rejects_missing_variable(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    template_path = prompt_dir / "test.md"
    template_path.write_text("Hello {name}", encoding="utf-8")

    loader = PromptTemplateLoader(prompt_dir=prompt_dir)

    with pytest.raises(PromptTemplateError):
        loader.render("test.md")


def test_prompt_loader_formats_list(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    template_path = prompt_dir / "test.md"
    template_path.write_text("Items:\n{items}", encoding="utf-8")

    loader = PromptTemplateLoader(prompt_dir=prompt_dir)

    rendered = loader.render("test.md", items=["A", "B"])

    assert "- A" in rendered
    assert "- B" in rendered