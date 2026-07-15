from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.evaluation.real_dataset import (
    DatasetValidationError,
    ValidationPolicy,
    freeze_test_split,
    validate_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or freeze a private paper evaluation dataset.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--profile", choices=("production", "reviewed-demo", "fixture"), default="production")
    parser.add_argument("--freeze-test", action="store_true")
    parser.add_argument("--skip-state-files", action="store_true", help="For schema-only checks on exported metadata.")
    args = parser.parse_args()
    policy = (
        ValidationPolicy.fixture() if args.profile == "fixture" else
        ValidationPolicy.reviewed_demo() if args.profile == "reviewed-demo" else
        ValidationPolicy()
    )
    try:
        if args.freeze_test:
            result = freeze_test_split(args.dataset, policy)
        else:
            result = validate_dataset(args.dataset, policy, verify_state_files=not args.skip_state_files)
    except (DatasetValidationError, ValueError, OSError) as exc:
        print(json.dumps({"valid": False, "errors": getattr(exc, "errors", [str(exc)])}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
