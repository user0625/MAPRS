from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisModeRun(BaseModel):
    """Aggregate from one serial/parallel pass over the same frozen reviewed set."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["analysis-mode-run-v1"] = "analysis-mode-run-v1"
    mode: Literal["serial", "parallel"]
    dataset_version: str
    frozen_test_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(gt=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    evidence_recall: float = Field(ge=0, le=1)
    report_quality: float = Field(ge=0, le=1)
    coverage_gap_rate: float = Field(default=0, ge=0, le=1)


def compare_modes(
    serial: AnalysisModeRun, parallel: AnalysisModeRun, *, quality_tolerance: float = 0.0,
    result_status: str = "unverified_local_run",
) -> dict[str, Any]:
    if serial.mode != "serial" or parallel.mode != "parallel":
        raise ValueError("comparison requires serial and parallel artifacts")
    for field in ("dataset_version", "frozen_test_sha256", "configuration_sha256", "case_count"):
        if getattr(serial, field) != getattr(parallel, field):
            raise ValueError(f"serial/parallel {field} mismatch")
    quality_ok = (
        parallel.evidence_recall + quality_tolerance >= serial.evidence_recall
        and parallel.report_quality + quality_tolerance >= serial.report_quality
        and parallel.coverage_gap_rate == 0
    )
    return {
        "schema_version": "analysis-mode-comparison-v1",
        "result_status": result_status,
        "dataset_version": serial.dataset_version,
        "frozen_test_sha256": serial.frozen_test_sha256,
        "configuration_sha256": serial.configuration_sha256,
        "case_count": serial.case_count,
        "quality_tolerance": quality_tolerance,
        "serial": serial.model_dump(mode="json"),
        "parallel": parallel.model_dump(mode="json"),
        "deltas": {
            "latency_p50_ms": parallel.latency_p50_ms - serial.latency_p50_ms,
            "latency_p95_ms": parallel.latency_p95_ms - serial.latency_p95_ms,
            "p95_speedup": serial.latency_p95_ms / parallel.latency_p95_ms
            if parallel.latency_p95_ms else None,
            "input_tokens": parallel.total_input_tokens - serial.total_input_tokens,
            "output_tokens": parallel.total_output_tokens - serial.total_output_tokens,
            "estimated_cost_usd": parallel.estimated_cost_usd - serial.estimated_cost_usd,
            "evidence_recall": parallel.evidence_recall - serial.evidence_recall,
            "report_quality": parallel.report_quality - serial.report_quality,
            "coverage_gap_rate": parallel.coverage_gap_rate - serial.coverage_gap_rate,
        },
        "quality_not_degraded": quality_ok,
        "default_mode_recommendation": "parallel" if quality_ok else "serial",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare serial and parallel Agent runs on one frozen set.")
    parser.add_argument("--serial", type=Path, required=True)
    parser.add_argument("--parallel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality-tolerance", type=float, default=0.0)
    parser.add_argument("--frozen-reviewed-run", action="store_true")
    args = parser.parse_args()
    serial = AnalysisModeRun.model_validate_json(args.serial.read_text(encoding="utf-8"))
    parallel = AnalysisModeRun.model_validate_json(args.parallel.read_text(encoding="utf-8"))
    report = compare_modes(
        serial, parallel, quality_tolerance=args.quality_tolerance,
        result_status="frozen_reviewed_run" if args.frozen_reviewed_run else "unverified_local_run",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
