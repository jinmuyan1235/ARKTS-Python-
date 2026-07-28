"""CLI for the auditable PubChem/PMC OCSR collection and review workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import warnings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.datasets.http import CachedHttpClient
from src.datasets.pipeline import DatasetPipeline
from src.datasets.review import DatasetReviewStore


def _pipeline(args: argparse.Namespace) -> DatasetPipeline:
    root = Path(args.dataset_root).expanduser().resolve()
    client = CachedHttpClient(
        args.cache_dir or root / "http_cache",
        request_interval=args.request_interval,
        retries=args.retries,
    )
    proposal_config = args.proposal_config
    crop_screening_config = args.crop_screening_config
    if args.screening_config:
        warnings.warn(
            "--screening-config is deprecated; use --proposal-config and --crop-screening-config",
            FutureWarning,
            stacklevel=2,
        )
        proposal_config = args.screening_config
        crop_screening_config = args.screening_config
    return DatasetPipeline(
        root,
        client=client,
        max_downloads=args.max_downloads,
        dry_run=bool(args.dry_run),
        resume=bool(args.resume),
        proposal_config=proposal_config,
        crop_screening_config=crop_screening_config,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="data/ocsr_collections")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--max-downloads", type=int, default=100)
    parser.add_argument("--request-interval", type=float, default=0.34)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--proposal-config", choices=["baseline", "candidate"], default="baseline")
    parser.add_argument("--crop-screening-config", choices=["baseline", "candidate"], default="candidate")
    parser.add_argument(
        "--screening-config", choices=["baseline", "candidate"], default=None,
        help="Deprecated compatibility option that sets both new configuration axes.",
    )
    parser.add_argument("--resume", dest="resume", action="store_true", default=True, help="Skip source tasks marked completed in collection_state.json.")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="Ignore completion state for this run.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pubchem = subparsers.add_parser("pubchem", help="Collect one or more public-domain PubChem CIDs.")
    pubchem.add_argument("--cid", type=int, action="append", required=True)

    pmc = subparsers.add_parser("pmc", help="Register PMC OA metadata and collect only whitelisted articles.")
    pmc.add_argument("--pmcid", action="append", required=True)
    pmc.add_argument("--document-url", default=None, help="Optional explicit PDF or PNG/JPEG page-resource URL for one PMC ID.")

    review = subparsers.add_parser("review", help="Record one independent human review vote.")
    review.add_argument("--sample-id", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=["approve", "reject"], required=True)
    review.add_argument("--smiles", default="", help="Required when decision is approve.")
    review.add_argument("--notes", default="")

    export = subparsers.add_parser("export-verified", help="Write only two-person-agreed samples to a benchmark manifest.")
    export.add_argument("--output", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "review":
        result = DatasetReviewStore(args.dataset_root).record_vote(
            args.sample_id,
            args.reviewer,
            args.decision,
            smiles=args.smiles,
            notes=args.notes,
        )
    elif args.command == "export-verified":
        result = DatasetReviewStore(args.dataset_root).build_verified_manifest(args.output or None)
    else:
        pipeline = _pipeline(args)
        if args.command == "pubchem":
            result = [pipeline.collect_pubchem(cid) for cid in args.cid]
        else:
            if args.document_url and len(args.pmcid) != 1:
                raise SystemExit("--document-url can only be used with one --pmcid.")
            result = [pipeline.collect_pmc(pmcid, document_url=args.document_url) for pmcid in args.pmcid]
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
