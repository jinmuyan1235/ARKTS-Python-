"""Compare two fixed OCSR release benchmark directories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.release_compare import compare_release_dirs, write_comparison_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True, help="Current release directory, e.g. benchmark/releases/v0.2.")
    parser.add_argument("--previous", required=True, help="Previous release directory, e.g. benchmark/releases/v0.1.")
    parser.add_argument("--output", default=None, help="Markdown report path. Defaults to <current>/comparison_to_previous.md.")
    parser.add_argument("--json-output", default=None, help="Optional JSON comparison output path.")
    parser.add_argument("--rate-tolerance", type=float, default=0.0)
    parser.add_argument("--latency-tolerance-ms", type=float, default=0.0)
    parser.add_argument("--allow-regression", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = Path(args.current).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else current / "comparison_to_previous.md"
    comparison = compare_release_dirs(
        current,
        args.previous,
        rate_tolerance=args.rate_tolerance,
        latency_tolerance_ms=args.latency_tolerance_ms,
    )
    write_comparison_report(output, comparison)
    if args.json_output:
        Path(args.json_output).expanduser().resolve().write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps({"passed": comparison["passed"], "report": str(output)}, ensure_ascii=False, indent=2))
    if not comparison["passed"] and not args.allow_regression:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
