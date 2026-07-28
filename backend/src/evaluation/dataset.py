"""Dataset manifest loading and validation for OCSR benchmark runs."""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import config
from rdkit import Chem

from src.chem.smiles_validator import smiles_to_mol, validate_smiles

REQUIRED_FIELDS = ("sample_id", "image_path", "ground_truth_smiles", "category", "source", "notes")
RECOMMENDED_FIELDS = (
    "dataset_version",
    "image_sha256",
    "expected_action",
    "split",
    "scaffold_key",
    "source_document",
    "source_license",
    "annotator",
    "reviewer",
    "review_status",
    "ground_truth_inchikey",
    "image_quality",
    "complexity",
    "perturbation",
    "structure_features",
    "supported_scope",
)
REAL_ACCEPTANCE_REQUIRED_FIELDS = (
    "dataset_version",
    "image_sha256",
    "source_document",
    "source_license",
    "annotator",
    "reviewer",
    "review_status",
    "ground_truth_smiles",
    "ground_truth_inchikey",
    "expected_action",
    "supported_scope",
)


@dataclass(frozen=True)
class BenchmarkSample:
    """One validated benchmark sample from a manifest row."""

    sample_id: str
    image_path: Path
    manifest_image_path: str
    ground_truth_smiles: str
    ground_truth_canonical_smiles: str | None
    category: str
    source: str
    notes: str
    ground_truth_inchikey: str = ""
    dataset_version: str = "unspecified"
    image_sha256: str = ""
    expected_action: str = "recognize"
    split: str = "unspecified"
    scaffold_key: str = "unspecified"
    source_document: str = "unspecified"
    source_license: str = "unspecified"
    annotator: str = "unspecified"
    reviewer: str = "unspecified"
    review_status: str = "unspecified"
    image_quality: str = "unspecified"
    complexity: str = "unspecified"
    perturbation: str = "none"
    structure_features: str = "unspecified"
    supported_scope: str = "unspecified"


class ManifestValidationError(ValueError):
    """Raised when a benchmark manifest contains invalid rows."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_image_path(raw_path: str, dataset_root: Path) -> Path:
    path = Path(raw_path).expanduser()
    resolved = path if path.is_absolute() else dataset_root / path
    return resolved.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _smiles_inchikey(smiles: str | None) -> str | None:
    mol = smiles_to_mol(smiles or "")
    if mol is None:
        return None
    try:
        return Chem.MolToInchiKey(mol)
    except Exception:
        return None


def load_manifest(
    manifest_path: str | Path,
    dataset_root: str | Path | None = None,
    require_real_metadata: bool = False,
) -> list[BenchmarkSample]:
    """Load and validate a CSV benchmark manifest.

    Image paths are resolved relative to ``dataset_root``. By default, this is
    the project root so the bundled example can reference ``data/samples``.
    """
    manifest = Path(manifest_path).expanduser().resolve()
    root = Path(dataset_root).expanduser().resolve() if dataset_root else config.PROJECT_ROOT
    errors: list[str] = []
    samples: list[BenchmarkSample] = []
    seen_ids: set[str] = set()

    if not manifest.is_file():
        raise ManifestValidationError([f"Manifest file does not exist: {manifest}"])
    if not _is_relative_to(manifest, root):
        errors.append(f"Manifest path is outside dataset root: {manifest}")

    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing_fields = [field for field in REQUIRED_FIELDS if field not in fieldnames]
        if require_real_metadata:
            missing_fields.extend(field for field in REAL_ACCEPTANCE_REQUIRED_FIELDS if field not in fieldnames)
            missing_fields = sorted(set(missing_fields))
        if missing_fields:
            errors.append(f"Manifest missing required fields: {', '.join(missing_fields)}")
            raise ManifestValidationError(errors)

        for line_number, row in enumerate(reader, start=2):
            row_errors: list[str] = []
            raw = {field: (row.get(field) or "").strip() for field in fieldnames}
            values = {field: (row.get(field) or "").strip() for field in REQUIRED_FIELDS}
            recommended = {
                field: (row.get(field) or "").strip() or BenchmarkSample.__dataclass_fields__[field].default
                for field in RECOMMENDED_FIELDS
            }
            expected_action = str(recommended["expected_action"]).lower()
            for field in REQUIRED_FIELDS:
                if field != "notes" and not values[field]:
                    if field == "ground_truth_smiles" and expected_action == "reject":
                        continue
                    row_errors.append(f"Line {line_number}: required field '{field}' is empty.")

            sample_id = values["sample_id"]
            if sample_id:
                if sample_id in seen_ids:
                    row_errors.append(f"Line {line_number}: duplicate sample_id '{sample_id}'.")
                seen_ids.add(sample_id)

            image_path = _resolve_image_path(values["image_path"], root)
            if values["image_path"]:
                if not _is_relative_to(image_path, root):
                    row_errors.append(f"Line {line_number}: image_path escapes dataset root: {values['image_path']}")
                elif not image_path.is_file():
                    row_errors.append(f"Line {line_number}: image file does not exist: {image_path}")
                elif recommended.get("image_sha256"):
                    actual_sha = _sha256_file(image_path)
                    if str(recommended["image_sha256"]).lower() != actual_sha:
                        row_errors.append(
                            f"Line {line_number}: image_sha256 mismatch for sample_id '{sample_id}'."
                        )

            if expected_action not in {"recognize", "reject"}:
                row_errors.append(f"Line {line_number}: expected_action must be 'recognize' or 'reject'.")
                expected_action = "recognize"
            if require_real_metadata:
                for field in REAL_ACCEPTANCE_REQUIRED_FIELDS:
                    if field == "ground_truth_smiles" and expected_action == "reject":
                        continue
                    if field == "ground_truth_inchikey" and expected_action == "reject":
                        continue
                    value = raw.get(field, "")
                    if not str(value or "").strip():
                        row_errors.append(f"Line {line_number}: real acceptance field '{field}' is empty.")
                if recommended.get("review_status") != "verified":
                    row_errors.append(f"Line {line_number}: review_status must be 'verified' for release acceptance.")
                if recommended.get("review_status") == "verified":
                    for field in ("annotator", "reviewer"):
                        if not raw.get(field):
                            row_errors.append(
                                f"Line {line_number}: verified real acceptance row must include '{field}'."
                            )
            validation = {"valid": False, "canonical_smiles": None, "error": None}
            if expected_action == "reject" and not values["ground_truth_smiles"]:
                validation = {"valid": True, "canonical_smiles": None, "error": None}
            else:
                validation = validate_smiles(values["ground_truth_smiles"])
            if not validation["valid"]:
                row_errors.append(
                    f"Line {line_number}: invalid ground_truth_smiles for sample_id '{sample_id}': "
                    f"{validation['error']}"
                )
            expected_inchikey = str(recommended.get("ground_truth_inchikey") or "").strip()
            computed_inchikey = _smiles_inchikey(values["ground_truth_smiles"])
            if expected_inchikey and computed_inchikey and expected_inchikey != computed_inchikey:
                row_errors.append(
                    f"Line {line_number}: ground_truth_inchikey does not match ground_truth_smiles "
                    f"for sample_id '{sample_id}'."
                )

            if row_errors:
                errors.extend(row_errors)
                continue
            samples.append(
                BenchmarkSample(
                    sample_id=sample_id,
                    image_path=image_path,
                    manifest_image_path=values["image_path"],
                    ground_truth_smiles=values["ground_truth_smiles"],
                    ground_truth_canonical_smiles=(
                        str(validation["canonical_smiles"]) if validation["canonical_smiles"] is not None else None
                    ),
                    ground_truth_inchikey=expected_inchikey or computed_inchikey or "",
                    category=values["category"],
                    source=values["source"],
                    notes=values["notes"],
                    dataset_version=recommended["dataset_version"],
                    image_sha256=recommended["image_sha256"],
                    expected_action=expected_action,
                    split=recommended["split"],
                    scaffold_key=recommended["scaffold_key"],
                    source_document=recommended["source_document"],
                    source_license=recommended["source_license"],
                    annotator=recommended["annotator"],
                    reviewer=recommended["reviewer"],
                    review_status=recommended["review_status"],
                    image_quality=recommended["image_quality"],
                    complexity=recommended["complexity"],
                    perturbation=recommended["perturbation"],
                    structure_features=recommended["structure_features"],
                    supported_scope=recommended["supported_scope"],
                )
            )

    if errors:
        raise ManifestValidationError(errors)
    if require_real_metadata:
        split_by_source: dict[str, set[str]] = defaultdict(set)
        for sample in samples:
            source_document = str(sample.source_document or "").strip()
            split = str(sample.split or "").strip() or "unspecified"
            if source_document:
                split_by_source[source_document].add(split)
        split_errors = [
            f"source_document '{source_document}' appears in multiple splits: {', '.join(sorted(splits))}"
            for source_document, splits in sorted(split_by_source.items())
            if len(splits) > 1
        ]
        if split_errors:
            raise ManifestValidationError(split_errors)
    return samples
