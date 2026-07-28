"""Apply document region edits outside the Streamlit server process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.documents.processor import DocumentOCSRProcessor
from src.export.json_exporter import save_json
from src.storage.analysis_repository import record_result_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-result", required=True, help="Existing document_result.json path.")
    parser.add_argument("--edits-json", required=True, help="JSON list of region edits.")
    parser.add_argument("--backend", default=config.OCSR_BACKEND, help="OCSR backend: demo, molscribe, decimer, ensemble.")
    parser.add_argument("--rerun-ocsr", action="store_true", help="Re-run OCSR for edited molecule regions.")
    parser.add_argument("--molscribe-device", default=None, help="Runtime MolScribe device override.")
    parser.add_argument("--decimer-device", default=None, help="Runtime DECIMER device override.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for TensorFlow/DECIMER.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        document_path = Path(args.document_result).expanduser().resolve()
        document_result = json.loads(document_path.read_text(encoding="utf-8"))
        edits = json.loads(args.edits_json)
        if not isinstance(edits, list):
            raise ValueError("edits-json must decode to a list.")
        runtime_config = {
            "molscribe_device": args.molscribe_device,
            "decimer_device": args.decimer_device,
            "visible_gpu_index": args.visible_gpu_index,
        }
        output_dir = Path(document_result.get("output_dir") or config.DOCUMENT_OUTPUT_DIR).expanduser().resolve()
        processor = DocumentOCSRProcessor(
            backend=args.backend,
            output_dir=output_dir,
            runtime_config=runtime_config,
            review_output_dir=config.DATA_DIR,
        )
        def progress(stage: str, current: int, total: int, region_id: str) -> None:
            print(
                "DOCUMENT_REGION_PROGRESS_JSON="
                + json.dumps(
                    {
                        "stage": stage,
                        "current": current,
                        "total": total,
                        "region_id": region_id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        updated = processor.apply_edits(
            document_result,
            edits,
            rerun_ocsr=args.rerun_ocsr,
            progress_callback=progress if args.rerun_ocsr else None,
        )
        result_path = Path(updated["exports"].get("json") or output_dir / "document_result.json")
        if not result_path.is_file():
            result_path = Path(save_json(updated, output_dir / "document_result.json"))
        record_result_payload(updated, result_path)
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "status": "success",
        "document_id": updated.get("document_id"),
        "summary": updated.get("summary"),
        "result_path": str(result_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
