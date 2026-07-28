"""Process one molecular image outside the Streamlit server process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.analysis.molecule_report import MoleculeReportGenerator
from src.analysis.correction import sha256_file
from src.runtime.run_store import (
    create_image_run_from_file,
    load_image_run,
    save_run_report,
    write_runtime_metadata,
)
from src.storage.analysis_repository import record_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Input PNG/JPG/JPEG molecular structure image.")
    parser.add_argument("--backend", default=config.OCSR_BACKEND, help="OCSR backend: demo, molscribe, decimer, ensemble.")
    parser.add_argument("--output", default=str(config.RUNS_DIR), help="Root directory for persistent image runs.")
    parser.add_argument("--run-dir", default=None, help="Existing persistent run directory to reuse.")
    parser.add_argument("--analysis-id", default=None, help="Analysis id to use for the persistent run and report.")
    parser.add_argument("--original-filename", default=None, help="User-facing filename to preserve in the report.")
    parser.add_argument("--effective-input", default=None, help="Adjusted image path to use for recognition while preserving the original run input.")
    parser.add_argument("--user-preprocessing-json", default=None, help="Serialized user preprocessing parameters.")
    parser.add_argument("--molscribe-device", default=None, help="Runtime MolScribe device override.")
    parser.add_argument("--decimer-device", default=None, help="Runtime DECIMER device override.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for TensorFlow/DECIMER.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    image_run = None
    try:
        if args.run_dir:
            image_run = load_image_run(args.run_dir, original_filename=args.original_filename, analysis_id=args.analysis_id)
        else:
            image_run = create_image_run_from_file(
                args.input,
                original_filename=args.original_filename,
                runs_root=args.output,
                analysis_id=args.analysis_id,
            )
        runtime_config = {
            "molscribe_device": args.molscribe_device,
            "decimer_device": args.decimer_device,
            "visible_gpu_index": args.visible_gpu_index,
        }
        effective_input = Path(args.effective_input).expanduser().resolve() if args.effective_input else image_run.input_path
        if not effective_input.is_file():
            raise FileNotFoundError(f"有效输入图片不存在：{effective_input}")
        user_preprocessing = {}
        if args.user_preprocessing_json:
            decoded = json.loads(args.user_preprocessing_json)
            if isinstance(decoded, dict):
                user_preprocessing = decoded
        generator = MoleculeReportGenerator(args.backend, image_run.run_dir, runtime_config=runtime_config)
        report = generator.generate(image_path=effective_input, analysis_id=image_run.analysis_id)
        report["user_preprocessing"] = {
            **user_preprocessing,
            "effective_image_path": str(effective_input),
            "effective_image_sha256": sha256_file(effective_input),
        }
        report.setdefault("input", {})
        report["input"]["effective_path"] = str(effective_input)
        report["input"]["effective_image_sha256"] = report["user_preprocessing"]["effective_image_sha256"]
        if effective_input.resolve() != image_run.input_path.resolve():
            preprocessing = report.setdefault("images", {}).setdefault("preprocessing", {})
            preprocessing["uploaded_original"] = str(image_run.input_path.resolve())
            preprocessing["user_adjusted"] = str(effective_input)
        result_path = save_run_report(report, image_run)
        record_report(report, result_path)
    except Exception as exc:
        if image_run is not None:
            write_runtime_metadata(image_run, {"status": "failed", "message": str(exc)})
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "status": "success",
        "analysis_id": report.get("analysis_id"),
        "report_status": report.get("status"),
        "message": report.get("message"),
        "result_path": str(result_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
