"""Process a PDF, page image, or ZIP image collection through document OCSR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.documents.input_loader import DocumentInputError, OptionalDependencyError
from src.documents.processor import DocumentOCSRProcessor
from src.storage.analysis_repository import record_result_payload


DOCUMENT_PROGRESS_MARKER = "DOCUMENT_PROGRESS_JSON="
DOCUMENT_RESULT_MARKER = "DOCUMENT_RESULT_JSON="


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="PDF, PNG/JPG/JPEG page image, or ZIP of page images.")
    parser.add_argument("--backend", default=config.OCSR_BACKEND, help="OCSR backend: demo, molscribe, decimer, ensemble.")
    parser.add_argument("--output", default=str(config.DOCUMENT_OUTPUT_DIR), help="Output directory for document runs.")
    parser.add_argument("--detect-only", action="store_true", help="Detect and crop regions without running OCSR.")
    parser.add_argument("--molscribe-device", default=None, help="Runtime MolScribe device override.")
    parser.add_argument("--decimer-device", default=None, help="Runtime DECIMER device override.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for TensorFlow/DECIMER.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        runtime_config = {
            "molscribe_device": args.molscribe_device,
            "decimer_device": args.decimer_device,
            "visible_gpu_index": args.visible_gpu_index,
        }
        processor = DocumentOCSRProcessor(
            backend=args.backend,
            output_dir=args.output,
            runtime_config=runtime_config,
            review_output_dir=config.DATA_DIR,
        )
        def progress(stage: str, current: int, total: int, detail: str) -> None:
            print(
                DOCUMENT_PROGRESS_MARKER
                + json.dumps(
                    {"stage": stage, "current": current, "total": total, "detail": detail},
                    ensure_ascii=False,
                ),
                flush=True,
            )

        result = processor.process(
            args.input,
            run_ocsr=not args.detect_only,
            document_progress_callback=progress,
        )
        record_result_payload(result, result.get("exports", {}).get("json"))
    except OptionalDependencyError as exc:
        print(json.dumps({"status": "unavailable", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except (DocumentInputError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    final_payload = {
        "status": "success",
        "document_id": result["document_id"],
        "summary": result["summary"],
        "exports": result["exports"],
        "result_path": result["exports"].get("json"),
    }
    print(DOCUMENT_RESULT_MARKER + json.dumps(final_payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
