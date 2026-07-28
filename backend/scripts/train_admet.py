"""Train an optional single-endpoint ADMET Random Forest baseline from CSV."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import ADMET_MODEL_PATH
from src.ml.admet_baseline import ADMETBaseline


def main() -> int:
    """Train a trusted local model artifact from user-supplied labeled data."""
    parser = argparse.ArgumentParser(description="训练 Morgan 指纹 + Random Forest ADMET baseline")
    parser.add_argument("--input", required=True, help="包含 SMILES 和目标标签的 CSV 文件")
    parser.add_argument("--smiles-column", default="smiles")
    parser.add_argument("--target-column", required=True)
    parser.add_argument("--task", choices=["classification", "regression"], default="classification")
    parser.add_argument("--split-strategy", choices=["scaffold", "random"], default="scaffold")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--output", default=str(ADMET_MODEL_PATH))
    args = parser.parse_args()
    try:
        frame = pd.read_csv(args.input)
        missing = {args.smiles_column, args.target_column} - set(frame.columns)
        if missing:
            raise ValueError(f"CSV 缺少列：{', '.join(sorted(missing))}")
        model = ADMETBaseline.train(
            frame[args.smiles_column],
            frame[args.target_column],
            target_name=args.target_column,
            task_type=args.task,
            random_state=args.random_state,
            split_strategy=args.split_strategy,
            test_size=args.test_size,
            min_samples=args.min_samples,
        )
        path = model.save(args.output)
        print(json.dumps({
            "model_path": path,
            "training_samples": model.training_samples,
            "validation_samples": model.validation_samples,
            "split_strategy": model.split_strategy,
            "metrics": model.metrics,
            "quality_gate": model.quality_gate,
            "applicability_domain": model.applicability_domain,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ADMET baseline 训练失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
