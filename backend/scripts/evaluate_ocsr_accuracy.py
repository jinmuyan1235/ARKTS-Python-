"""Evaluate OCSR accuracy on the repository sample images or a user manifest."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cuda_env import ensure_cuda_library_path

ensure_cuda_library_path(reexec=True)

from src.evaluation.dataset import load_manifest
from src.evaluation.evaluator import OCSREvaluator


SAMPLE_GROUND_TRUTH = {
    "aspirin.png": "CC(=O)Oc1ccccc1C(=O)O",
    "benzene.png": "c1ccccc1",
    "caffeine.png": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "ethanol.png": "CCO",
}


def _sample_manifest() -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    temp_root = PROJECT_ROOT / "data" / "outputs"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.TemporaryDirectory(dir=temp_root)
    manifest = Path(temp_dir.name) / "sample_manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", "image_path", "ground_truth_smiles", "category", "source", "notes"],
        )
        writer.writeheader()
        for filename, smiles in SAMPLE_GROUND_TRUTH.items():
            absolute_image_path = PROJECT_ROOT / "data" / "samples" / filename
            if absolute_image_path.is_file():
                writer.writerow({
                    "sample_id": absolute_image_path.stem,
                    "image_path": f"data/samples/{filename}",
                    "ground_truth_smiles": smiles,
                    "category": "local_sample",
                    "source": "repository",
                    "notes": "",
                })
    return manifest, temp_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCSR accuracy.")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--backend", default="ensemble", choices=["molscribe", "decimer", "ensemble", "demo"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    temp: tempfile.TemporaryDirectory[str] | None = None
    manifest = args.manifest
    dataset_root = PROJECT_ROOT
    if manifest is None:
        manifest, temp = _sample_manifest()
    else:
        dataset_root = manifest.parent
    try:
        samples = load_manifest(manifest, dataset_root)
        if args.limit:
            samples = samples[: args.limit]
        result = OCSREvaluator(args.backend).run(samples)
        print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
        return 0
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
