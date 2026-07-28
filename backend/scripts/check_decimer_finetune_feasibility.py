"""Audit installed DECIMER and export a bounded 50-CID feasibility dataset."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ocsr.decimer_finetune_feasibility import (
    FeasibilityExportConfig,
    audit_installed_decimer,
    export_feasibility_dataset,
)


def _package_root() -> Path:
    spec = importlib.util.find_spec("DECIMER")
    if spec is None or spec.origin is None:
        raise RuntimeError("DECIMER is not installed in this Python environment")
    return Path(spec.origin).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/datasets/ocsr-trusted-v0.1"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/decimer-finetune-feasibility/official-aware-50cid"),
    )
    parser.add_argument("--seed", type=int, default=20260718)
    args = parser.parse_args()

    tensorflow_version = importlib.metadata.version("tensorflow")
    audit = audit_installed_decimer(_package_root(), tensorflow_version)
    audit["decimer_version"] = importlib.metadata.version("decimer")
    report = export_feasibility_dataset(
        args.dataset / "manifest.csv",
        args.dataset,
        args.output,
        audit,
        FeasibilityExportConfig(seed=args.seed),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
