"""Validate v0.2 integrity and absence of v0.1 identity leakage."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.datasets.trusted_ocsr_v2 import validate_external_holdout
def main()->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--dataset",type=Path,default=ROOT/"data/datasets/ocsr-trusted-v0.2"); p.add_argument("--reference",type=Path,default=ROOT/"data/datasets/ocsr-trusted-v0.1")
    a=p.parse_args(); result=validate_external_holdout(a.dataset,a.reference); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result["valid"] else 2
if __name__=="__main__": raise SystemExit(main())
