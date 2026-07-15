import json

import pytest

from backend.api.routes.evaluation import load_public_report


def report(**updates):
    value = {
        "schema_version": "public-paper-benchmark-v1",
        "benchmark": "QASPER",
        "scenarios": [],
    }
    value.update(updates)
    return value


def test_evaluation_report_loader_uses_latest_compatible_content_free_artifact(tmp_path):
    (tmp_path / "older.json").write_text(json.dumps(report(marker="older")), encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps(report(marker="latest")), encoding="utf-8")
    assert load_public_report(tmp_path)["marker"] == "latest"


def test_evaluation_report_loader_rejects_nested_sensitive_fields(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps(report(scenarios=[{
        "questions": ["complete private question"]
    }])), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_public_report(tmp_path)


def test_evaluation_report_loader_accepts_v2_and_rejects_singular_content_fields(tmp_path):
    (tmp_path / "real.json").write_text(json.dumps(report(
        schema_version="public-paper-benchmark-v2", run_level="pilot",
    )), encoding="utf-8")
    assert load_public_report(tmp_path)["run_level"] == "pilot"
    (tmp_path / "leaky.json").write_text(json.dumps(report(
        schema_version="public-paper-benchmark-v2", question="private question",
    )), encoding="utf-8")
    assert load_public_report(tmp_path)["run_level"] == "pilot"
