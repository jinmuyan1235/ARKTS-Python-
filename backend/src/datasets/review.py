"""Two-person review ledger and verified manifest builder for collected OCSR data."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.chem.smiles_validator import validate_smiles
from src.datasets.licenses import is_allowed_license
from src.datasets.splits import assign_grouped_splits, scaffold_for_smiles, validate_split_isolation
from src.utils.file_utils import ensure_directory, safe_stem


PENDING_FIELDS = (
    "sample_id", "image_path", "image_sha256", "perceptual_hash", "category", "expected_action",
    "source_kind", "source_id", "source_document", "source_url", "source_license", "attribution",
    "source_page_path", "page_width", "page_height",
    "canonical_smiles", "inchikey", "reference_smiles", "reference_inchikey", "bbox", "candidate_predictions",
    "screening_decision", "screening_config_version",
    "duplicate_of", "review_status", "queue_annotation_path", "notes",
)
VERIFIED_FIELDS = (
    "sample_id", "image_path", "image_sha256", "ground_truth_smiles", "ground_truth_inchikey",
    "expected_action", "category", "source", "split", "scaffold_key", "source_document", "source_license",
    "annotator", "reviewer", "review_status", "attribution", "notes",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


@dataclass(frozen=True)
class ReviewVote:
    reviewer: str
    decision: str
    canonical_smiles: str
    inchikey: str
    reviewed_at: str
    notes: str = ""


class DatasetReviewStore:
    """Require two unique reviewers to agree before a record becomes verified."""

    def __init__(self, dataset_root: str | Path) -> None:
        self.root = ensure_directory(Path(dataset_root).expanduser().resolve())
        self.pending_manifest = self.root / "pending_manifest.csv"
        self.review_dir = ensure_directory(self.root / "reviews")

    def record_vote(self, sample_id: str, reviewer: str, decision: str, *, smiles: str = "", notes: str = "") -> dict[str, Any]:
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("reviewer is required.")
        decision = decision.strip().lower()
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject.")
        validation = validate_smiles(smiles) if decision == "approve" else {"valid": True, "canonical_smiles": ""}
        if not validation["valid"]:
            raise ValueError(f"Invalid reviewed SMILES: {validation.get('error')}")
        canonical = str(validation.get("canonical_smiles") or "")
        from rdkit import Chem
        molecule = Chem.MolFromSmiles(canonical) if canonical else None
        inchikey = Chem.MolToInchiKey(molecule) if molecule is not None else ""
        path = self._path(sample_id)
        payload = self._load(path, sample_id)
        votes = [vote for vote in payload.get("votes", []) if vote.get("reviewer") != reviewer]
        votes.append(ReviewVote(reviewer, decision, canonical, inchikey, _now(), notes).__dict__)
        payload["votes"] = votes
        payload["status"] = self._status(votes)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_pending(sample_id, payload)
        return payload

    def build_verified_manifest(self, output_path: str | Path | None = None) -> dict[str, Any]:
        verified: list[dict[str, Any]] = []
        for row in _read_csv(self.pending_manifest):
            review = self._load(self._path(row["sample_id"]), row["sample_id"])
            if review.get("status") not in {"verified", "rejected"}:
                continue
            votes = review.get("votes") or []
            if len(votes) < 2:
                continue
            image_path = (self.root / row.get("image_path", "")).resolve()
            if not image_path.is_file() or not is_allowed_license(row.get("source_license")):
                continue
            first, second = votes[-2], votes[-1]
            approved = first.get("decision") == "approve"
            verified.append({
                "sample_id": row["sample_id"],
                "image_path": row.get("image_path"),
                "image_sha256": row.get("image_sha256"),
                "ground_truth_smiles": first.get("canonical_smiles", "") if approved else "",
                "ground_truth_inchikey": first.get("inchikey", "") if approved else "",
                "expected_action": "recognize" if approved else "reject",
                "category": row.get("category", "molecule"),
                "source": row.get("source_kind", ""),
                "source_document": row.get("source_document", ""),
                "source_license": row.get("source_license", ""),
                "annotator": first.get("reviewer", ""),
                "reviewer": second.get("reviewer", ""),
                "review_status": "verified" if approved else "rejected",
                "attribution": row.get("attribution", ""),
                "notes": row.get("notes", ""),
            })
        verified = assign_grouped_splits(verified)
        errors = validate_split_isolation(verified)
        if errors:
            raise ValueError("\n".join(errors))
        for row in verified:
            row["scaffold_key"] = scaffold_for_smiles(row.get("ground_truth_smiles"))
        target = Path(output_path).expanduser().resolve() if output_path else self.root / "verified_manifest.csv"
        _write_csv(target, verified, VERIFIED_FIELDS)
        return {"output_manifest": str(target), "verified_count": len(verified), "pending_count": len(_read_csv(self.pending_manifest))}

    def _path(self, sample_id: str) -> Path:
        return self.review_dir / f"{safe_stem(sample_id)}.json"

    @staticmethod
    def _load(path: Path, sample_id: str) -> dict[str, Any]:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"sample_id": sample_id, "votes": [], "status": "pending"}

    @staticmethod
    def _status(votes: list[dict[str, Any]]) -> str:
        if len(votes) < 2:
            return "pending"
        latest = votes[-2:]
        if len({vote.get("reviewer") for vote in latest}) < 2:
            return "pending"
        if latest[0].get("decision") != latest[1].get("decision"):
            return "disagreement"
        if latest[0].get("decision") == "approve" and latest[0].get("canonical_smiles") != latest[1].get("canonical_smiles"):
            return "disagreement"
        return "verified" if latest[0].get("decision") == "approve" else "rejected"

    def _sync_pending(self, sample_id: str, review: dict[str, Any]) -> None:
        rows = _read_csv(self.pending_manifest)
        for row in rows:
            if row.get("sample_id") == sample_id:
                row["review_status"] = str(review.get("status") or "pending")
                if review.get("status") == "verified":
                    latest = (review.get("votes") or [])[-1]
                    row["canonical_smiles"] = str(latest.get("canonical_smiles") or "")
                    row["inchikey"] = str(latest.get("inchikey") or "")
        _write_csv(self.pending_manifest, rows, PENDING_FIELDS)
