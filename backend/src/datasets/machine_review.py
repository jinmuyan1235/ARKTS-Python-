"""Deterministic, audit-only machine review for collected OCSR candidates.

The reviewer reads ``pending_manifest.csv`` and writes separate review artifacts.
It never changes the pending manifest or a human review ledger.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageFilter, ImageStat
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

import config
from src.chem.mol_drawer import draw_molecule
from src.chem.smiles_validator import smiles_to_mol, validate_smiles
from src.datasets.licenses import is_allowed_license, normalize_license
from src.datasets.pipeline import NEGATIVE_CATEGORIES, hamming_distance, perceptual_hash
from src.datasets.provenance import sha256_file
from src.datasets.splits import scaffold_for_smiles
from src.ocsr.recognizer import MoleculeRecognizer
from src.utils.file_utils import ensure_directory, safe_stem


VERIFICATION_STATUSES = (
    "rejected_invalid",
    "rejected_license",
    "pending_machine_review",
    "machine_verified",
    "pending_human_review",
    "human_verified",
    "human_rejected",
)

MACHINE_REVIEW_FIELDS = (
    "sample_id", "verification_status", "machine_status", "human_review_status",
    "dataset_root",
    "image_path", "image_sha256", "actual_image_sha256", "perceptual_hash", "duplicate_of",
    "source_kind", "source_id", "source_document", "source_url", "source_license", "attribution",
    "source_page_path", "page_width", "page_height",
    "ground_truth_origin", "ground_truth_smiles", "ground_truth_inchikey", "source_compound_id", "source_structure_file",
    "category", "machine_category", "expected_action", "bbox", "bbox_valid", "bbox_edge_contact",
    "source_canonical_smiles", "source_inchikey", "source_formula", "scaffold_key", "split",
    "molscribe_raw", "molscribe_smiles", "molscribe_canonical_smiles", "molscribe_inchikey", "molscribe_formula",
    "decimer_raw", "decimer_smiles", "decimer_canonical_smiles", "decimer_inchikey", "decimer_formula",
    "ensemble_raw", "ensemble_smiles", "ensemble_canonical_smiles", "ensemble_inchikey", "ensemble_formula",
    "models_agree", "valid_model_count", "redraw_path", "redraw_similarity", "image_quality_score",
    "image_quality_level", "complexity", "structure_features", "split_leakage", "risk_reasons",
    "deterministic_errors", "notes",
)

REQUIRED_SOURCE_FIELDS = ("sample_id", "source_document", "source_url", "source_license", "attribution")
REQUIRED_ANNOTATION_FIELDS = ("category", "expected_action", "review_status")
KNOWN_CATEGORIES = {"molecule", *NEGATIVE_CATEGORIES}
METAL_ATOMIC_NUMBERS = {
    3, 4, 11, 12, 13, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
    37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 55, 56, 57,
    72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83,
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MACHINE_REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in MACHINE_REVIEW_FIELDS} for row in rows)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class MachineReviewConfig:
    perceptual_hash_distance: int = 3
    redraw_similarity_threshold: float = 0.58
    image_quality_threshold: float = 0.55
    edge_ink_threshold: float = 0.02


class MachineReviewProcessor:
    """Run reproducible technical checks while preserving human review authority."""

    def __init__(
        self,
        dataset_root: str | Path = config.DATA_DIR / "ocsr_collections",
        *,
        output_dir: str | Path = config.DATA_DIR / "review",
        recognizer_factory: Callable[[str], MoleculeRecognizer] = MoleculeRecognizer,
        config_: MachineReviewConfig | None = None,
        rerun_models: bool = True,
        image_quality_fn: Callable[[Path], dict[str, Any]] | None = None,
        redraw_similarity_fn: Callable[[Path, str, Path], float] | None = None,
    ) -> None:
        self.root = Path(dataset_root).expanduser().resolve()
        self.pending_manifest = self.root / "pending_manifest.csv"
        self.output_dir = ensure_directory(Path(output_dir).expanduser().resolve())
        self.redraw_dir = ensure_directory(self.output_dir / "redraws")
        self.recognizer_factory = recognizer_factory
        self.config = config_ or MachineReviewConfig()
        self.rerun_models = rerun_models
        self.image_quality_fn = image_quality_fn or self._image_quality
        self.redraw_similarity_fn = redraw_similarity_fn or self._redraw_similarity

    def run(self) -> dict[str, Any]:
        """Review all pending rows and write machine, rejected, and human queues."""
        rows = _read_csv(self.pending_manifest)
        leakage = self._split_leakage(rows)
        duplicates = self._manifest_duplicates(rows)
        output_rows = [
            self._review_row(
                row,
                leakage.get(str(row.get("sample_id") or ""), []),
                duplicates.get(str(row.get("sample_id") or "")),
            )
            for row in rows
        ]
        machine_path = self.output_dir / "machine_review_manifest.csv"
        rejected_path = self.output_dir / "rejected_manifest.csv"
        human_path = self.output_dir / "human_review_queue.csv"
        _write_csv(machine_path, output_rows)
        _write_csv(rejected_path, [row for row in output_rows if row["verification_status"].startswith("rejected_")])
        _write_csv(human_path, [row for row in output_rows if row["verification_status"] == "pending_human_review"])
        summary = self._summary(output_rows, machine_path, rejected_path, human_path)
        summary_path = self.output_dir / "review_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report_path = self.output_dir / "review_report.md"
        report_path.write_text(self._report(summary), encoding="utf-8")
        return {
            "machine_review_manifest": str(machine_path),
            "rejected_manifest": str(rejected_path),
            "human_review_queue": str(human_path),
            "review_summary": str(summary_path),
            "review_report": str(report_path),
            "total": len(output_rows),
        }

    def _review_row(
        self,
        row: dict[str, str],
        split_leakage: list[str],
        detected_duplicate: tuple[str, str] | None,
    ) -> dict[str, Any]:
        sample_id = str(row.get("sample_id") or "")
        deterministic_errors: list[str] = []
        risks: list[str] = list(split_leakage)
        for field in REQUIRED_SOURCE_FIELDS + REQUIRED_ANNOTATION_FIELDS:
            if not str(row.get(field) or "").strip():
                deterministic_errors.append(f"missing_{field}")

        image_path, image_info = self._inspect_image(row.get("image_path") or "")
        deterministic_errors.extend(image_info["errors"])
        actual_sha = image_info["sha256"]
        image_sha = str(row.get("image_sha256") or "")
        if image_sha and actual_sha and image_sha != actual_sha:
            deterministic_errors.append("image_sha256_mismatch")
        expected_phash = str(row.get("perceptual_hash") or "")
        if expected_phash and image_info["phash"] and expected_phash != image_info["phash"]:
            risks.append("perceptual_hash_mismatch")

        bbox_valid, bbox_edge_contact, bbox_errors = self._bbox_checks(row, image_info)
        deterministic_errors.extend(bbox_errors)
        if bbox_edge_contact:
            risks.append("bbox_touches_structure_edge")

        category = str(row.get("category") or "invalid_crop").strip().lower()
        machine_category = category if category in KNOWN_CATEGORIES else "invalid_crop"
        if machine_category == "invalid_crop" and category != "invalid_crop":
            deterministic_errors.append("invalid_category")

        license_text = str(row.get("source_license") or "")
        license_key = normalize_license(license_text)
        license_allowed = is_allowed_license(license_text)
        license_unknown = not license_key

        source_structure = self._structure(str(row.get("reference_smiles") or row.get("canonical_smiles") or ""))
        if row.get("reference_smiles") and not source_structure["valid"]:
            deterministic_errors.append("invalid_reference_smiles")
        expected_inchikey = str(row.get("reference_inchikey") or row.get("inchikey") or "")
        if expected_inchikey and source_structure["inchikey"] and expected_inchikey != source_structure["inchikey"]:
            deterministic_errors.append("source_inchikey_mismatch")

        predictions = self._model_predictions(row, image_path, machine_category)
        molscribe = predictions["molscribe"]
        decimer = predictions["decimer"]
        ensemble = predictions["ensemble"]
        valid_models = [record for record in (molscribe, decimer, ensemble) if record["valid"]]
        primary = molscribe if molscribe["valid"] else decimer if decimer["valid"] else ensemble
        models_agree = bool(molscribe["valid"] and decimer["valid"] and molscribe["inchikey"] == decimer["inchikey"])
        if not models_agree and machine_category == "molecule":
            risks.append("model_disagreement")
        if machine_category == "molecule" and len(valid_models) == 1:
            risks.append("only_one_valid_model")
        if machine_category in NEGATIVE_CATEGORIES and valid_models:
            risks.append("negative_sample_has_valid_smiles")

        structure_features = self._structure_features(primary["molecule"], primary["smiles"])
        risks.extend(structure_features["risks"])
        quality = image_info["quality"]
        if quality.get("score", 0.0) < self.config.image_quality_threshold:
            risks.append("low_image_quality")
        redraw_path = ""
        similarity: float | None = None
        if image_path is not None and primary["valid"]:
            redraw = self.redraw_dir / f"{safe_stem(sample_id or 'sample')}.png"
            try:
                similarity = float(self.redraw_similarity_fn(image_path, primary["canonical_smiles"], redraw))
                redraw_path = redraw.relative_to(self.output_dir).as_posix() if redraw.is_file() else ""
            except Exception as exc:
                risks.append("redraw_comparison_failed")
                similarity = None
        # Redraw comparison has no meaning for a negative crop or when no OCSR
        # backend produced a valid structure.  Recording those cases as a low
        # similarity made almost every text/reaction/blank candidate look bad.
        if machine_category == "molecule" and primary["valid"]:
            if similarity is None or similarity < self.config.redraw_similarity_threshold:
                risks.append("low_redraw_similarity")

        if split_leakage:
            deterministic_errors.append("split_leakage")
        duplicate_of = ""
        duplicate_kind = ""
        if detected_duplicate:
            duplicate_of, duplicate_kind = detected_duplicate
        elif row.get("duplicate_of"):
            # Old collector manifests did not preserve whether this was an
            # exact byte duplicate or only an average-hash neighbour.
            duplicate_of = str(row["duplicate_of"])
            duplicate_kind = "collector_reported"
        if duplicate_kind == "exact":
            deterministic_errors.append("duplicate_image")
        elif duplicate_kind == "category_conflict":
            risks.append("duplicate_category_conflict")
        elif duplicate_kind in {"near", "collector_reported"}:
            risks.append("near_duplicate_image")

        machine_status = self._machine_status(
            deterministic_errors=deterministic_errors,
            license_allowed=license_allowed,
            license_unknown=license_unknown,
            category=machine_category,
            models_agree=models_agree,
            valid_model_count=len(valid_models),
            source_inchikey=source_structure["inchikey"],
            expected_inchikey=expected_inchikey,
            primary_inchikey=primary["inchikey"],
            bbox_edge_contact=bbox_edge_contact,
            risks=risks,
        )
        human_status = str(row.get("review_status") or "").strip().lower()
        verification_status = self._final_status(machine_status, human_status)
        ground_truth_origin = str(row.get("ground_truth_origin") or "").strip().lower()
        if not ground_truth_origin and row.get("source_kind") == "pubchem" and source_structure["valid"]:
            ground_truth_origin = "pubchem"

        return {
            "sample_id": sample_id,
            "verification_status": verification_status,
            "machine_status": machine_status,
            "human_review_status": human_status,
            "dataset_root": str(self.root),
            "image_path": row.get("image_path", ""),
            "image_sha256": image_sha,
            "actual_image_sha256": actual_sha,
            "perceptual_hash": image_info["phash"],
            "duplicate_of": duplicate_of,
            "source_kind": row.get("source_kind", ""),
            "source_id": row.get("source_id", ""),
            "source_document": row.get("source_document", ""),
            "source_url": row.get("source_url", ""),
            "source_license": license_text,
            "attribution": row.get("attribution", ""),
            "source_page_path": row.get("source_page_path", ""),
            "page_width": row.get("page_width", ""),
            "page_height": row.get("page_height", ""),
            "ground_truth_origin": ground_truth_origin,
            "ground_truth_smiles": row.get("ground_truth_smiles") or row.get("reference_smiles", ""),
            "ground_truth_inchikey": row.get("ground_truth_inchikey") or row.get("reference_inchikey") or source_structure["inchikey"],
            "source_compound_id": row.get("source_compound_id") or row.get("source_id", ""),
            "source_structure_file": row.get("source_structure_file", ""),
            "category": category,
            "machine_category": machine_category,
            "expected_action": row.get("expected_action", ""),
            "bbox": row.get("bbox", ""),
            "bbox_valid": str(bbox_valid).lower(),
            "bbox_edge_contact": str(bbox_edge_contact).lower(),
            "source_canonical_smiles": source_structure["canonical_smiles"],
            "source_inchikey": source_structure["inchikey"],
            "source_formula": source_structure["formula"],
            "scaffold_key": scaffold_for_smiles(source_structure["canonical_smiles"] or primary["canonical_smiles"]),
            "split": row.get("split", ""),
            **self._prediction_columns("molscribe", molscribe),
            **self._prediction_columns("decimer", decimer),
            **self._prediction_columns("ensemble", ensemble),
            "models_agree": str(models_agree).lower(),
            "valid_model_count": str(len(valid_models)),
            "redraw_path": redraw_path,
            "redraw_similarity": "" if similarity is None else f"{similarity:.4f}",
            "image_quality_score": f"{quality.get('score', 0.0):.4f}",
            "image_quality_level": quality.get("level", "invalid"),
            "complexity": structure_features["complexity"],
            "structure_features": _json(structure_features["features"]),
            "split_leakage": _json(split_leakage),
            "risk_reasons": _json(sorted(set(risks))),
            "deterministic_errors": _json(sorted(set(deterministic_errors))),
            "notes": row.get("notes", ""),
        }

    def _inspect_image(self, raw_path: str) -> tuple[Path | None, dict[str, Any]]:
        info: dict[str, Any] = {"errors": [], "sha256": "", "phash": "", "quality": {"score": 0.0, "level": "invalid"}}
        if not raw_path.strip():
            info["errors"].append("missing_image_path")
            return None, info
        candidate = Path(raw_path).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if not _is_relative_to(resolved, self.root):
            info["errors"].append("manifest_path_escape")
            return None, info
        if not resolved.is_file():
            info["errors"].append("image_missing")
            return None, info
        try:
            with Image.open(resolved) as image:
                image.verify()
            with Image.open(resolved) as image:
                image.load()
        except Exception:
            info["errors"].append("image_decode_failed")
            return None, info
        info["sha256"] = sha256_file(resolved)
        info["phash"] = perceptual_hash(resolved)
        info["quality"] = self.image_quality_fn(resolved)
        return resolved, info

    def _bbox_checks(self, row: dict[str, str], image_info: dict[str, Any]) -> tuple[bool, bool, list[str]]:
        raw_bbox = _parse_json(row.get("bbox"), [])
        if raw_bbox in (None, [], ""):
            return True, bool(image_info.get("quality", {}).get("edge_ink", False)), []
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            return False, False, ["invalid_bbox"]
        try:
            x1, y1, x2, y2 = [int(value) for value in raw_bbox]
        except (TypeError, ValueError):
            return False, False, ["invalid_bbox"]
        if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
            return False, False, ["bbox_out_of_bounds_or_empty"]
        page_width = self._positive_int(row.get("page_width") or row.get("source_page_width"))
        page_height = self._positive_int(row.get("page_height") or row.get("source_page_height"))
        if page_width and page_height and (x2 > page_width or y2 > page_height):
            return False, False, ["bbox_out_of_bounds"]
        return True, bool(image_info.get("quality", {}).get("edge_ink", False)), []

    @staticmethod
    def _positive_int(value: str | None) -> int | None:
        try:
            parsed = int(str(value or ""))
        except ValueError:
            return None
        return parsed if parsed > 0 else None

    def _model_predictions(self, row: dict[str, str], image_path: Path | None, category: str) -> dict[str, dict[str, Any]]:
        saved = _parse_json(row.get("candidate_predictions"), [])
        saved_by_backend = {
            str(item.get("backend") or "").lower(): item
            for item in saved if isinstance(item, dict)
        } if isinstance(saved, list) else {}
        records: dict[str, dict[str, Any]] = {}
        for backend in ("molscribe", "decimer", "ensemble"):
            raw: dict[str, Any]
            if image_path is None:
                raw = saved_by_backend.get(backend, {"backend": backend, "status": "not_run", "smiles": None})
            elif self.rerun_models:
                try:
                    result = self.recognizer_factory(backend).recognize(image_path)
                    raw = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                except Exception as exc:
                    raw = {"backend": backend, "status": "failed", "smiles": None, "message": str(exc)}
            else:
                raw = saved_by_backend.get(backend, {"backend": backend, "status": "missing", "smiles": None})
            raw = dict(raw)
            raw["backend"] = backend
            records[backend] = {**self._structure(str(raw.get("smiles") or "")), "raw": raw}
        return records

    @staticmethod
    def _structure(smiles: str) -> dict[str, Any]:
        validation = validate_smiles(smiles)
        canonical = str(validation.get("canonical_smiles") or "")
        molecule = smiles_to_mol(canonical) if validation.get("valid") else None
        if molecule is None:
            return {"smiles": smiles, "valid": False, "canonical_smiles": "", "inchikey": "", "formula": "", "molecule": None}
        return {
            "smiles": smiles,
            "valid": True,
            "canonical_smiles": canonical,
            "inchikey": Chem.MolToInchiKey(molecule),
            "formula": rdMolDescriptors.CalcMolFormula(molecule),
            "molecule": molecule,
        }

    @staticmethod
    def _prediction_columns(prefix: str, record: dict[str, Any]) -> dict[str, str]:
        return {
            f"{prefix}_raw": _json(record["raw"]),
            f"{prefix}_smiles": record["smiles"],
            f"{prefix}_canonical_smiles": record["canonical_smiles"],
            f"{prefix}_inchikey": record["inchikey"],
            f"{prefix}_formula": record["formula"],
        }

    def _machine_status(
        self,
        *,
        deterministic_errors: list[str],
        license_allowed: bool,
        license_unknown: bool,
        category: str,
        models_agree: bool,
        valid_model_count: int,
        source_inchikey: str,
        expected_inchikey: str,
        primary_inchikey: str,
        bbox_edge_contact: bool,
        risks: list[str],
    ) -> str:
        if deterministic_errors:
            return "rejected_invalid"
        if not license_allowed and not license_unknown:
            return "rejected_license"
        if license_unknown:
            return "pending_human_review"
        if category != "molecule":
            return "pending_human_review"
        if valid_model_count == 0:
            return "pending_machine_review"
        if not models_agree or valid_model_count == 1:
            return "pending_human_review"
        if expected_inchikey and primary_inchikey != expected_inchikey:
            return "pending_human_review"
        if source_inchikey and primary_inchikey != source_inchikey:
            return "pending_human_review"
        blocking_risks = {
            "bbox_touches_structure_edge", "low_image_quality", "low_redraw_similarity",
            "redraw_comparison_failed", "stereochemistry", "markush_or_query_atom", "polymer_risk",
            "metal_coordination", "charged_structure", "salt_or_multifragment", "model_disagreement",
            "only_one_valid_model", "negative_sample_has_valid_smiles",
            "near_duplicate_image", "duplicate_category_conflict",
        }
        if bbox_edge_contact or blocking_risks.intersection(risks):
            return "pending_human_review"
        return "machine_verified"

    @staticmethod
    def _final_status(machine_status: str, human_status: str) -> str:
        if machine_status in {"rejected_invalid", "rejected_license"}:
            return machine_status
        if human_status == "verified":
            return "human_verified"
        if human_status == "rejected":
            return "human_rejected"
        return machine_status

    @staticmethod
    def _structure_features(molecule: Chem.Mol | None, smiles: str) -> dict[str, Any]:
        if molecule is None:
            return {"features": [], "risks": [], "complexity": "unknown"}
        features: list[str] = []
        risks: list[str] = []
        fragments = len(Chem.GetMolFrags(molecule))
        if fragments > 1:
            features.append("multifragment")
            risks.append("salt_or_multifragment")
        if any(atom.GetChiralTag() != Chem.rdchem.ChiralType.CHI_UNSPECIFIED for atom in molecule.GetAtoms()):
            features.append("stereochemistry")
            risks.append("stereochemistry")
        if any(atom.GetFormalCharge() for atom in molecule.GetAtoms()):
            features.append("charge")
            risks.append("charged_structure")
        if any(atom.GetAtomicNum() == 0 for atom in molecule.GetAtoms()) or "*" in smiles:
            features.append("markush")
            risks.append("markush_or_query_atom")
        if any(atom.GetAtomicNum() in METAL_ATOMIC_NUMBERS for atom in molecule.GetAtoms()):
            features.append("metal")
            risks.append("metal_coordination")
        if "poly" in smiles.lower() or "{+}" in smiles:
            features.append("polymer")
            risks.append("polymer_risk")
        atom_count = molecule.GetNumAtoms()
        ring_count = molecule.GetRingInfo().NumRings()
        complexity = "low" if atom_count <= 15 and ring_count <= 1 else "medium" if atom_count <= 35 else "high"
        return {"features": features, "risks": risks, "complexity": complexity}

    def _image_quality(self, path: Path) -> dict[str, Any]:
        with Image.open(path) as image:
            gray = image.convert("L")
            width, height = gray.size
            stat = ImageStat.Stat(gray)
            contrast = min(1.0, float(stat.stddev[0]) / 64.0)
            pixels = list(gray.getdata())
            ink_fraction = sum(value < 245 for value in pixels) / max(1, len(pixels))
            edge_image = gray.filter(ImageFilter.FIND_EDGES)
            sharpness = min(1.0, float(ImageStat.Stat(edge_image).mean[0]) / 32.0)
            border = [gray.crop((0, 0, width, 1)), gray.crop((0, height - 1, width, height)), gray.crop((0, 0, 1, height)), gray.crop((width - 1, 0, width, height))]
            edge_values = [value for crop in border for value in crop.getdata()]
            edge_ink = sum(value < 245 for value in edge_values) / max(1, len(edge_values)) >= self.config.edge_ink_threshold
        resolution = min(1.0, min(width, height) / 160.0)
        ink_score = 1.0 if 0.005 <= ink_fraction <= 0.65 else 0.25
        score = max(0.0, min(1.0, 0.30 * resolution + 0.25 * contrast + 0.25 * sharpness + 0.20 * ink_score))
        level = "high" if score >= 0.75 else "medium" if score >= self.config.image_quality_threshold else "low"
        return {"score": score, "level": level, "width": width, "height": height, "edge_ink": edge_ink, "ink_fraction": ink_fraction}

    def _redraw_similarity(self, image_path: Path, smiles: str, redraw_path: Path) -> float:
        draw_molecule(smiles, redraw_path)
        return max(0.0, 1.0 - hamming_distance(perceptual_hash(image_path), perceptual_hash(redraw_path)) / 64.0)

    def _split_leakage(self, rows: list[dict[str, str]]) -> dict[str, list[str]]:
        values: dict[tuple[str, str], set[str]] = defaultdict(set)
        members: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in rows:
            split = str(row.get("split") or "").strip()
            if not split:
                continue
            identity = self._structure(str(row.get("reference_smiles") or row.get("canonical_smiles") or ""))["inchikey"]
            for kind, value in (
                ("source_document", str(row.get("source_document") or "").strip()),
                ("molecule_identity", identity or str(row.get("reference_inchikey") or row.get("inchikey") or "").strip()),
                ("scaffold", scaffold_for_smiles(str(row.get("reference_smiles") or row.get("canonical_smiles") or ""))),
            ):
                if value and value not in {"negative", "invalid", "acyclic"}:
                    values[(kind, value)].add(split)
                    members[(kind, value)].append(str(row.get("sample_id") or ""))
        result: dict[str, list[str]] = defaultdict(list)
        for key, splits in values.items():
            if len(splits) > 1:
                kind, value = key
                for sample_id in members[key]:
                    result[sample_id].append(f"split_leakage_{kind}:{value}")
        return result

    def _manifest_duplicates(self, rows: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
        """Classify exact duplicates, label conflicts, and same-class near neighbours.

        The 64-bit average hash is intentionally only a review hint. Sparse
        black-on-white paper crops collide frequently, so a perceptual-hash
        neighbour must never be treated as deterministic corruption.
        """
        result: dict[str, tuple[str, str]] = {}
        seen_hashes: dict[str, tuple[str, str]] = {}
        phashes: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for row in rows:
            sample_id = str(row.get("sample_id") or "")
            category = str(row.get("category") or "invalid_crop").strip().lower()
            image_hash = str(row.get("image_sha256") or "")
            if sample_id and image_hash:
                if image_hash in seen_hashes:
                    prior_sample, prior_category = seen_hashes[image_hash]
                    kind = "exact" if category == prior_category else "category_conflict"
                    result[sample_id] = (prior_sample, kind)
                else:
                    seen_hashes[image_hash] = (sample_id, category)
            image_phash = str(row.get("perceptual_hash") or "")
            if sample_id and image_phash and sample_id not in result:
                for prior_sample, prior_phash in phashes[category]:
                    try:
                        is_near = hamming_distance(image_phash, prior_phash) <= self.config.perceptual_hash_distance
                    except ValueError:
                        is_near = False
                    if is_near:
                        result[sample_id] = (prior_sample, "near")
                        break
            if sample_id and image_phash:
                phashes[category].append((sample_id, image_phash))
        return result

    @staticmethod
    def _summary(rows: list[dict[str, Any]], machine_path: Path, rejected_path: Path, human_path: Path) -> dict[str, Any]:
        def count(column: str) -> dict[str, int]:
            return dict(sorted(Counter(str(row.get(column) or "unspecified") for row in rows).items()))

        risks = Counter()
        features = Counter()
        for row in rows:
            risks.update(_parse_json(row.get("risk_reasons"), []))
            risks.update(_parse_json(row.get("deterministic_errors"), []))
            features.update(_parse_json(row.get("structure_features"), []))
        return {
            "total": len(rows),
            "verification_status": count("verification_status"),
            "by_source": count("source_kind"),
            "by_image_quality": count("image_quality_level"),
            "by_complexity": count("complexity"),
            "by_structure_features": dict(sorted(features.items())),
            "by_risk_reason": dict(sorted(risks.items())),
            "outputs": {
                "machine_review_manifest": str(machine_path),
                "rejected_manifest": str(rejected_path),
                "human_review_queue": str(human_path),
            },
        }

    @staticmethod
    def _report(summary: dict[str, Any]) -> str:
        lines = ["# OCSR Machine Review Report", "", f"Total candidates: {summary['total']}", ""]
        for title, key in (
            ("Verification Status", "verification_status"),
            ("Source", "by_source"),
            ("Image Quality", "by_image_quality"),
            ("Complexity", "by_complexity"),
            ("Structure Features", "by_structure_features"),
            ("Risk and Rejection Reasons", "by_risk_reason"),
        ):
            lines.extend([f"## {title}", "", "| Value | Count |", "| --- | ---: |"])
            lines.extend(f"| {value} | {count} |" for value, count in summary[key].items())
            lines.append("")
        lines.extend([
            "## Boundary",
            "",
            "`machine_verified` is a machine gate only. It is not a human ground-truth label and does not alter the pending manifest or human review records.",
            "",
        ])
        return "\n".join(lines)
