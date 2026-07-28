"""Run batch OCSR outside the Streamlit server process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.analysis.batch_analyzer import BatchAnalyzer
from src.export.json_exporter import to_json_text
from src.runtime.batch_job_store import BatchJobStore
from src.storage.analysis_repository import record_result_payload
from src.utils.file_utils import ensure_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Folder containing PNG/JPG/JPEG images.")
    parser.add_argument("--backend", default=config.OCSR_BACKEND, help="OCSR backend: demo, molscribe, decimer, ensemble.")
    parser.add_argument("--output", default=str(config.OUTPUT_DIR / "batch_runs"), help="Output directory for batch runs.")
    parser.add_argument("--molscribe-device", default=None, help="Runtime MolScribe device override.")
    parser.add_argument("--decimer-device", default=None, help="Runtime DECIMER device override.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for TensorFlow/DECIMER.")
    parser.add_argument("--job-id", default=None, help="Background job id for progress persistence.")
    parser.add_argument("--job-store-dir", default=None, help="Directory containing background job state.")
    parser.add_argument("--checkpoint", default=None, help="Persistent per-file checkpoint JSON.")
    parser.add_argument("--cache-dir", default=None, help="Content-hash result cache shared by batch jobs.")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh OCSR for explicit retry tasks.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = BatchJobStore(args.job_store_dir) if args.job_id and args.job_store_dir else None
    try:
        runtime_config = {
            "molscribe_device": args.molscribe_device,
            "decimer_device": args.decimer_device,
            "visible_gpu_index": args.visible_gpu_index,
        }
        output_dir = ensure_directory(args.output)
        result = BatchAnalyzer(
            args.backend,
            output_dir,
            runtime_config=runtime_config,
            cache_dir=None if args.no_cache else args.cache_dir,
        ).analyze_folder(
            args.input,
            progress_callback=(lambda payload: store.update_progress(args.job_id, payload)) if store and args.job_id else None,
            cancel_requested=(lambda: store.cancel_requested(args.job_id)) if store and args.job_id else None,
            skip_requested=(lambda _path: store.consume_skip_request(args.job_id)) if store and args.job_id else None,
            pause_requested=(lambda: store.pause_requested(args.job_id)) if store and args.job_id else None,
            checkpoint_path=args.checkpoint,
        )
        ui_result = {
            "summary": result["summary"],
            "rows": result["rows"],
            "reports": result["reports"],
            "exports": result["exports"],
        }
        result_path = output_dir / "batch_ui_result.json"
        result_path.write_text(to_json_text(ui_result), encoding="utf-8")
        record_result_payload(ui_result, result_path)
        if store and args.job_id:
            if ui_result["summary"].get("cancelled"):
                store.update_progress(args.job_id, {
                    "status": "cancelled",
                    "total": ui_result["summary"].get("total"),
                    "completed": ui_result["summary"].get("completed"),
                    "summary": ui_result["summary"],
                    "result_path": str(result_path),
                    "exports": ui_result["exports"],
                })
            else:
                store.complete(args.job_id, result_path, ui_result["exports"], ui_result["summary"])
    except Exception as exc:
        if store and args.job_id:
            store.fail(args.job_id, str(exc))
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    status = "cancelled" if ui_result["summary"].get("cancelled") else "success"
    print(json.dumps({
        "status": status,
        "summary": ui_result["summary"],
        "exports": ui_result["exports"],
        "result_path": str(result_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
