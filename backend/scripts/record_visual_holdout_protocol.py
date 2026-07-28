"""Record immutable visual-holdout provenance before collection or review begins."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.documents.candidate_screening import get_screening_config


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dev-checksums", required=True)
    parser.add_argument("--paper-sources", required=True)
    parser.add_argument("--pmcid", action="append", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selected = {value.strip().upper() for value in args.pmcid}
    with Path(args.paper_sources).open("r", encoding="utf-8-sig", newline="") as handle:
        papers = [row for row in csv.DictReader(handle) if str(row.get("pmcid") or "").upper() in selected]
    found = {str(row.get("pmcid") or "").upper() for row in papers}
    if found != selected:
        raise SystemExit(f"Paper metadata missing for: {sorted(selected - found)}")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    screening_source = PROJECT_ROOT / "src" / "documents" / "candidate_screening.py"
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "candidate_screening_source_sha256": _sha256(screening_source),
        "visual_dev_checksums_sha256": _sha256(Path(args.dev_checksums).resolve()),
        "screening_configs": {
            name: asdict(get_screening_config(name)) for name in ("baseline", "candidate")
        },
        "papers": papers,
        "collection_screening_config": "baseline",
        "constraints": {
            "screening_logic_frozen": True,
            "threshold_changes_allowed": False,
            "old_review_results_reused": False,
        },
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    papers_output = output.parent / "holdout_papers.json"
    papers_output.write_text(
        json.dumps({"dataset_role": "holdout", "papers": papers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "success", "output": str(output), "papers_output": str(papers_output),
        "papers": sorted(found),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
