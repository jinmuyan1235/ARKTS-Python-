"""Compare two complete page OCSR routing evaluation runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.page_ocsr_routing_compare import compare_page_ocsr_routing_runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--workflow-regressions-passed",
        action="store_true",
        help="Assert that the complete document layout regression suite passed for this Git SHA.",
    )
    args = parser.parse_args()
    result = compare_page_ocsr_routing_runs(
        Path(args.baseline),
        Path(args.candidate),
        Path(args.output),
        workflow_regressions_passed=args.workflow_regressions_passed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
