from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.uploads import UploadValidationError, deduplication_key, save_validated_pdf
from backend.core.request_policy import RequestPolicy, retry_after_seconds
from backend.llm.client import MockLLMClient
from backend.llm.prompt_loader import PromptTemplateLoader
from pydantic import BaseModel


class Output(BaseModel):
    mock: bool
    prompt_preview: str


def upload(filename: str, content: bytes, content_type: str = "application/pdf"):
    from starlette.datastructures import UploadFile
    import io
    return UploadFile(io.BytesIO(content), filename=filename, headers={"content-type": content_type})


def test_pdf_validation_hash_and_partial_cleanup(tmp_path: Path):
    target = tmp_path / "task_test.pdf"
    saved = save_validated_pdf(upload("paper.pdf", b"%PDF-1.4 body"), target, 100)
    assert saved.sha256 and target.exists()
    with pytest.raises(UploadValidationError):
        save_validated_pdf(upload("fake.pdf", b"not pdf"), target, 100)
    assert not target.exists()


def test_dedup_key_normalizes_query():
    assert deduplication_key("a", "  Explain   This ", "zh") == deduplication_key("a", "explain this", "zh")


def test_retry_after_and_non_retryable(monkeypatch):
    exc = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "3"}))
    assert retry_after_seconds(exc) == 3
    policy = RequestPolicy(10, 2, 0, 0)
    calls = 0
    class BadRequest(Exception):
        status_code = 400

    def fail():
        nonlocal calls
        calls += 1
        raise BadRequest()

    with pytest.raises(Exception):
        policy.call(fail)
    assert calls == 1


def test_structured_output_statistics_first_attempt():
    client = MockLLMClient()
    client.generate_pydantic("test", Output)
    assert client.structured_output_stats["total_calls"] == 1
    assert client.structured_output_stats["first_attempt_successes"] == 1


def test_prompt_hash_is_stable(tmp_path: Path):
    (tmp_path / "sample.md").write_text("Hello {name}", encoding="utf-8")
    loader = PromptTemplateLoader(tmp_path)
    assert loader.template_hash("sample.md") == loader.template_hashes()["sample.md"]
