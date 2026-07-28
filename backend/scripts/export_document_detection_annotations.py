"""Export confirmed PDF/document region annotations for detector training."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.feedback.store import export_document_detection_annotations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document-result",
        action="append",
        required=True,
        help="Path to a document_result.json file. May be provided more than once.",
    )
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument(
        "--root",
        default=None,
        help="Root used to make page image paths relative. Defaults to the output file directory.",
    )
    parser.add_argument(
        "--include-unconfirmed",
        action="store_true",
        help="Include unconfirmed boxes. The default exports only human-confirmed boxes.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        document_results = []
        for value in args.document_result:
            path = Path(value).expanduser().resolve()
            document_results.append(json.loads(path.read_text(encoding="utf-8")))
        result = export_document_detection_annotations(
            document_results,
            args.output,
            root=args.root,
            include_unconfirmed=args.include_unconfirmed,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "success", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
