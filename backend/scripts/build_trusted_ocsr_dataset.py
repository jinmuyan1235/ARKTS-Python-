"""Build the frozen PubChem trusted OCSR benchmark."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.datasets.trusted_ocsr import TrustedDatasetBuildConfig, TrustedOCSRDatasetBuilder

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/download_cache/pubchem")
    parser.add_argument("--target-cids", type=int, default=1000)
    parser.add_argument("--minimum-success", type=int, default=800)
    parser.add_argument("--candidate-pool-size", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--cid-file", type=Path)
    args = parser.parse_args()
    config = TrustedDatasetBuildConfig(args.output, args.cache_dir, args.target_cids, args.minimum_success, args.candidate_pool_size, args.seed, args.cid_file)
    print(json.dumps(TrustedOCSRDatasetBuilder(config).build(), ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
