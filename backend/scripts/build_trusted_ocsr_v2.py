"""Build the independent ocsr-trusted-v0.2 external holdout."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.datasets.trusted_ocsr_v2 import ExternalHoldoutBuildConfig, TrustedOCSRExternalHoldoutBuilder
def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,default=ROOT/"data/datasets/ocsr-trusted-v0.2")
    p.add_argument("--cache-dir",type=Path,default=ROOT/"data/download_cache/pubchem")
    p.add_argument("--reference-manifest",type=Path,default=ROOT/"data/datasets/ocsr-trusted-v0.1/manifest.csv")
    p.add_argument("--frozen-profiles",type=Path,default=ROOT/"data/evaluation/ocsr-trusted-v0.1/dev_preprocessing/best_profiles.json")
    p.add_argument("--target-cids",type=int,default=300); p.add_argument("--minimum-success",type=int,default=300)
    p.add_argument("--candidate-pool-size",type=int,default=1800); p.add_argument("--seed",type=int,default=20260719)
    a=p.parse_args(); config=ExternalHoldoutBuildConfig(a.output,a.cache_dir,a.reference_manifest,a.frozen_profiles,a.target_cids,a.minimum_success,a.candidate_pool_size,a.seed)
    print(json.dumps(TrustedOCSRExternalHoldoutBuilder(config).build(),ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
