"""End-to-end, audit-first OCSR collection pipeline for approved public sources."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from PIL import Image, ImageStat

import config
from src.datasets.http import CachedHttpClient
from src.datasets.pmc import PmcOpenAccessCollector
from src.datasets.provenance import SourceRecord, SourceRegistry, sha256_file
from src.datasets.pubchem import PubChemCollector, PubChemStructure
from src.datasets.review import PENDING_FIELDS, DatasetReviewStore
from src.documents.processor import DocumentOCSRProcessor
from src.documents.candidate_screening import (
    CandidateProposalConfig, CandidateScreeningConfig, CropScreeningConfig,
    get_crop_screening_config, get_proposal_config, screen_region_candidate,
)
from src.feedback.store import save_review_queue_item
from src.ocsr.base import OCSRResult
from src.ocsr.ensemble import combine_ensemble_results
from src.ocsr.recognizer import MoleculeRecognizer
from src.utils.file_utils import ensure_directory, safe_stem


NEGATIVE_CATEGORIES = {
    "text", "table", "reaction", "figure", "logo", "blank",
    "multiple_molecules", "invalid_crop",
}
REGION_CATEGORY = {
    "molecule": "molecule",
    "text": "text",
    "table": "table",
    "reaction": "reaction",
    "reaction_like": "reaction",
    "reaction_arrow": "reaction",
    "reaction_condition": "reaction",
    "figure": "figure",
    "logo": "logo",
    "blank": "blank",
    "multiple_molecules": "multiple_molecules",
    "uncertain": "uncertain_visual",
}


def perceptual_hash(path: str | Path) -> str:
    """Return a dependency-free 64-bit average perceptual hash."""
    with Image.open(path) as image:
        grayscale = image.convert("L").resize((8, 8))
        values = list(grayscale.getdata())
    average = sum(values) / len(values)
    bits = "".join("1" if value >= average else "0" for value in values)
    return f"{int(bits, 2):016x}"


def hamming_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PENDING_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in PENDING_FIELDS} for row in rows)


@dataclass(frozen=True)
class CandidateResult:
    sample_id: str
    duplicate_of: str | None
    pending_manifest: Path
    queue_annotation_path: str | None


class DatasetPipeline:
    """Collect only license-approved sources and queue all candidates for human review."""

    def __init__(
        self,
        root: str | Path = config.DATA_DIR / "ocsr_collections",
        *,
        client: CachedHttpClient | None = None,
        document_processor_factory: Callable[..., DocumentOCSRProcessor] = DocumentOCSRProcessor,
        recognizer_factory: Callable[[str], MoleculeRecognizer] = MoleculeRecognizer,
        max_downloads: int = 100,
        dry_run: bool = False,
        resume: bool = True,
        proposal_config: str | CandidateProposalConfig = "baseline",
        crop_screening_config: str | CropScreeningConfig = "candidate",
        screening_config: str | CandidateScreeningConfig | None = None,
    ) -> None:
        self.root = ensure_directory(Path(root).expanduser().resolve())
        self.registry = SourceRegistry(self.root)
        self.review_store = DatasetReviewStore(self.root)
        self.client = client or CachedHttpClient(self.root / "http_cache")
        self.document_processor_factory = document_processor_factory
        self.recognizer_factory = recognizer_factory
        self.max_downloads = max(0, int(max_downloads))
        self.dry_run = dry_run
        self.resume = resume
        if screening_config is not None:
            import warnings
            warnings.warn(
                "screening_config is deprecated; use proposal_config and crop_screening_config",
                DeprecationWarning, stacklevel=2,
            )
            legacy_name = screening_config.name if isinstance(screening_config, CandidateScreeningConfig) else screening_config
            proposal_config = legacy_name
            crop_screening_config = legacy_name
        self.proposal_config = get_proposal_config(proposal_config)
        self.crop_screening_config = get_crop_screening_config(crop_screening_config)
        self.material_root = ensure_directory(self.root / "sources")
        self.candidate_root = ensure_directory(self.root / "candidates")
        self.pending_manifest = self.root / "pending_manifest.csv"
        self.state_path = self.root / "collection_state.json"
        self.log_path = self.root / "collection_log.jsonl"
        self._downloads = 0

    def collect_pubchem(self, cid: int) -> dict[str, Any]:
        """Collect a public-domain PubChem structure and queue its 2D image as a candidate."""
        task_key = f"pubchem:{int(cid)}"
        if self._already_completed(task_key):
            return {"status": "skipped", "reason": "already_completed", "source_key": task_key}
        if not self.dry_run:
            self._reserve_download()
        collector = PubChemCollector(self.client, self.material_root)
        result = collector.collect(cid, dry_run=self.dry_run)
        source = result.source if isinstance(result, PubChemStructure) else result
        self.registry.upsert(source)
        if self.dry_run:
            self._record_state(task_key, "dry_run")
            return {"status": "dry_run", "source_key": source.source_key}
        if not isinstance(result, PubChemStructure):
            raise RuntimeError("PubChem collector did not materialize an approved structure.")
        candidate = self.add_candidate(
            result.image_path,
            source,
            category="molecule",
            source_document=source.source_key,
            reference_smiles=result.canonical_smiles,
            reference_inchikey=result.inchikey,
            notes=f"PubChem CID {result.cid} 2D PNG; source structure is reference material, not model truth.",
        )
        self._record_state(task_key, "completed")
        return {"status": "completed", "source_key": source.source_key, "candidate": candidate.__dict__}

    def collect_pmc(self, pmcid: str, *, document_url: str | None = None) -> dict[str, Any]:
        """Register PMC metadata and process document pages only when its license is whitelisted."""
        task_key = f"pmc:{pmcid.upper()}"
        if self._already_completed(task_key):
            return {"status": "skipped", "reason": "already_completed", "source_key": task_key}
        if not self.dry_run:
            self._reserve_download()
        result = PmcOpenAccessCollector(self.client, self.material_root).collect(
            pmcid,
            document_url=document_url,
            dry_run=self.dry_run,
        )
        self.registry.upsert(result.source)
        if self.dry_run:
            self._record_state(task_key, "dry_run")
            return {"status": "dry_run", "source_key": result.source.source_key}
        if not result.source.license_allowed:
            self._record_state(task_key, "blocked_license")
            self._log("blocked_license", source_key=result.source.source_key, license=result.source.license)
            return {"status": "blocked_license", "source_key": result.source.source_key, "license": result.source.license}
        if result.document_path is None:
            raise RuntimeError("Approved PMC source did not provide a document path.")
        processor = self.document_processor_factory(
            backend="ensemble",
            output_dir=self.root / "document_runs",
            review_output_dir=config.DATA_DIR,
            proposal_config=self.proposal_config,
            crop_screening_config=self.crop_screening_config,
        )
        document = processor.process(result.document_path, run_ocsr=False)
        candidates = self._document_candidates(document, result.source)
        self._record_state(task_key, "completed")
        return {"status": "completed", "source_key": result.source.source_key, "candidate_count": len(candidates)}

    def add_candidate(
        self,
        image_path: str | Path | None,
        source: SourceRecord,
        *,
        category: str,
        source_document: str,
        bbox: list[int] | tuple[int, int, int, int] | None = None,
        source_page_path: str | Path | None = None,
        page_size: tuple[int, int] | None = None,
        reference_smiles: str = "",
        reference_inchikey: str = "",
        notes: str = "",
        screening_decision: str = "",
        screening_config_version: str = "",
    ) -> CandidateResult:
        """Persist a candidate; run OCSR only for plausible molecule positives."""
        if not source.license_allowed:
            raise PermissionError("Cannot save a candidate image for a non-whitelisted source license.")
        category = category if category in {"molecule", "uncertain_visual", *NEGATIVE_CATEGORIES} else "invalid_crop"
        sample_id = f"{safe_stem(source.source_key)}_{category}_{uuid4().hex[:12]}"
        relative_image = ""
        image_sha = ""
        phash = ""
        predictions: list[dict[str, Any]] = []
        if image_path is not None:
            source_image = Path(image_path).expanduser().resolve()
            if source_image.is_file():
                destination = ensure_directory(self.candidate_root / safe_stem(source.source_key)) / f"{sample_id}.png"
                with Image.open(source_image) as image:
                    image.convert("RGB").save(destination)
                relative_image = destination.relative_to(self.root).as_posix()
                image_sha = sha256_file(destination)
                phash = perceptual_hash(destination)
                if category == "molecule" and screening_decision != "review_needed":
                    predictions = self._predict_all(destination)
                else:
                    predictions = self._not_applicable_predictions(category)
            else:
                category = "invalid_crop"
                notes = f"{notes} Input image is missing: {source_image}".strip()
        else:
            category = "invalid_crop"

        duplicate_of = self._find_duplicate(image_sha, phash, reference_smiles, reference_inchikey)
        expected_action = (
            "review" if screening_decision == "review_needed"
            else "reject" if category in NEGATIVE_CATEGORIES
            else "recognize"
        )
        row = {
            "sample_id": sample_id,
            "image_path": relative_image,
            "image_sha256": image_sha,
            "perceptual_hash": phash,
            "category": category,
            "expected_action": expected_action,
            "source_kind": source.source_kind,
            "source_id": source.source_id,
            "source_document": source_document,
            "source_url": source.source_url,
            "source_license": source.license,
            "attribution": source.attribution,
            "source_page_path": self._manifest_path(source_page_path),
            "page_width": str(page_size[0]) if page_size else "",
            "page_height": str(page_size[1]) if page_size else "",
            "canonical_smiles": "",
            "inchikey": "",
            "reference_smiles": reference_smiles,
            "reference_inchikey": reference_inchikey,
            "bbox": json.dumps(list(bbox) if bbox else []),
            "candidate_predictions": json.dumps(predictions, ensure_ascii=False),
            "screening_decision": screening_decision,
            "screening_config_version": screening_config_version,
            "duplicate_of": duplicate_of or "",
            "review_status": "duplicate" if duplicate_of else "pending",
            "queue_annotation_path": "",
            "notes": notes,
        }
        if not duplicate_of and relative_image:
            row["queue_annotation_path"] = self._queue_for_existing_review(row, predictions)
        rows = _read_csv(self.pending_manifest)
        rows.append(row)
        _write_csv(self.pending_manifest, rows)
        self._log("candidate_added", sample_id=sample_id, category=category, duplicate_of=duplicate_of)
        return CandidateResult(sample_id, duplicate_of, self.pending_manifest, row["queue_annotation_path"] or None)

    def _document_candidates(self, document: dict[str, Any], source: SourceRecord) -> list[CandidateResult]:
        pages = {int(page["page_number"]): page for page in document.get("pages") or []}
        results: list[CandidateResult] = []
        for region in document.get("regions") or []:
            page = pages.get(int(region.get("page_number") or 0))
            page_path = Path(page["image_path"]) if page else None
            initial_type = str(region.get("region_type") or "")
            category = REGION_CATEGORY.get(initial_type, "invalid_crop")
            screening = None
            if page_path is not None and page_path.is_file():
                try:
                    screening = screen_region_candidate(
                        page_path,
                        region.get("bbox") or [],
                        initial_type,
                        region.get("detection_confidence"),
                        config=self.crop_screening_config,
                    )
                    category = REGION_CATEGORY.get(screening.recommended_region_type, "invalid_crop")
                except (OSError, TypeError, ValueError):
                    category = "invalid_crop"
            crop = self._crop_region(page_path, region.get("bbox")) if page_path else None
            results.append(self.add_candidate(
                crop,
                source,
                category=category,
                source_document=str(document.get("document_id") or source.source_key),
                bbox=region.get("bbox"),
                source_page_path=page_path,
                page_size=self._image_size(page_path),
                screening_decision=screening.decision if screening else "review_needed",
                screening_config_version=screening.config_version if screening else "",
                notes=(
                    f"Document detector region {region.get('region_id')}; initial_type={initial_type}; "
                    f"proposal_config={self.proposal_config.name}; "
                    f"crop_screening_config={self.crop_screening_config.name}; "
                    f"screening_reasons={','.join(screening.reason_codes) if screening else 'screening_failed'}"
                ),
            ))
        for page in pages.values():
            blank = self._blank_crop(Path(page["image_path"]))
            if blank is not None:
                results.append(self.add_candidate(
                    blank,
                    source,
                    category="blank",
                    source_document=str(document.get("document_id") or source.source_key),
                    source_page_path=Path(page["image_path"]),
                    page_size=self._image_size(Path(page["image_path"])),
                    notes="Automatically selected low-ink page patch.",
                ))
        return results

    def _manifest_path(self, path: str | Path | None) -> str:
        if path is None:
            return ""
        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return str(resolved)

    @staticmethod
    def _image_size(path: Path) -> tuple[int, int] | None:
        try:
            with Image.open(path) as image:
                return image.size
        except Exception:
            return None

    def _crop_region(self, page_path: Path, bbox: Any) -> Path | None:
        try:
            values = [int(value) for value in bbox]
            if len(values) != 4:
                return None
            with Image.open(page_path) as image:
                x1, y1, x2, y2 = values
                x1, x2 = sorted((max(0, x1), min(image.width, x2)))
                y1, y2 = sorted((max(0, y1), min(image.height, y2)))
                if x2 <= x1 or y2 <= y1:
                    return None
                crop = image.crop((x1, y1, x2, y2))
                path = ensure_directory(self.root / "work" / "crops") / f"crop_{uuid4().hex}.png"
                crop.save(path)
                return path
        except Exception:
            return None

    def _blank_crop(self, page_path: Path) -> Path | None:
        with Image.open(page_path) as image:
            image = image.convert("L")
            width, height = image.size
            side = min(160, width, height)
            if side < 32:
                return None
            candidates = [(0, 0), (width - side, 0), (0, height - side), (width - side, height - side)]
            crops = [(ImageStat.Stat(image.crop((x, y, x + side, y + side))).mean[0], x, y) for x, y in candidates]
            _, x, y = max(crops)
            crop = image.crop((x, y, x + side, y + side))
            if ImageStat.Stat(crop).mean[0] < 245:
                return None
            path = ensure_directory(self.root / "work" / "blanks") / f"blank_{uuid4().hex}.png"
            crop.save(path)
            return path

    def _predict_all(self, image_path: Path) -> list[dict[str, Any]]:
        raw_results: list[OCSRResult] = []
        for backend in ("molscribe", "decimer"):
            try:
                result = self.recognizer_factory(backend).recognize(image_path)
                payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                raw_results.append(self._result_from_payload(backend, payload))
            except Exception as exc:
                raw_results.append(OCSRResult(None, None, backend, "failed", str(exc)))
        ensemble = combine_ensemble_results(raw_results, enabled_backends=["molscribe", "decimer"])
        return [result.to_dict() for result in [*raw_results, ensemble]]

    @staticmethod
    def _result_from_payload(backend: str, payload: dict[str, Any]) -> OCSRResult:
        fields = OCSRResult.__dataclass_fields__
        values = {field: payload.get(field) for field in fields}
        values["backend"] = backend
        values["status"] = values.get("status") if values.get("status") in {"success", "failed"} else "failed"
        values["message"] = str(values.get("message") or "")
        return OCSRResult(**values)

    @staticmethod
    def _not_applicable_predictions(category: str) -> list[dict[str, Any]]:
        """Keep negative candidates auditable without running expensive OCSR models."""
        message = f"OCSR inference was not run for automatically generated {category} negative candidate."
        return [
            {"backend": backend, "status": "not_applicable", "smiles": None, "message": message}
            for backend in ("molscribe", "decimer", "ensemble")
        ]

    def _find_duplicate(self, image_sha: str, phash: str, canonical_smiles: str, inchikey: str) -> str | None:
        for row in _read_csv(self.pending_manifest):
            if image_sha and row.get("image_sha256") == image_sha:
                return row.get("sample_id")
            if phash and row.get("perceptual_hash") and hamming_distance(phash, row["perceptual_hash"]) <= 3:
                return row.get("sample_id")
            if inchikey and row.get("reference_inchikey") == inchikey:
                return row.get("sample_id")
            if inchikey and row.get("inchikey") == inchikey:
                return row.get("sample_id")
            if canonical_smiles and row.get("reference_smiles") == canonical_smiles:
                return row.get("sample_id")
            if canonical_smiles and row.get("canonical_smiles") == canonical_smiles:
                return row.get("sample_id")
        return None

    def _queue_for_existing_review(self, row: dict[str, Any], predictions: list[dict[str, Any]]) -> str:
        image = (self.root / row["image_path"]).resolve()
        ensemble = next((item for item in predictions if item.get("backend") == "ensemble"), {})
        report = {
            "analysis_id": row["sample_id"],
            "status": "success" if ensemble.get("status") == "success" else "pending",
            "message": "Dataset collection candidate; predictions require independent human review.",
            "input": {"path": str(image), "filename": image.name},
            "ocsr": {"smiles": ensemble.get("smiles"), "backend": "ensemble", "candidates": predictions},
            "final": {},
        }
        queued = save_review_queue_item(
            report,
            output_dir=config.DATA_DIR,
            notes=f"Dataset candidate {row['sample_id']} from {row['source_kind']}; requires two-person review.",
            source_reference=row["source_url"],
            source_license=row["source_license"],
        )
        return str(queued.get("annotation_path") or "")

    def _reserve_download(self) -> None:
        if self._downloads >= self.max_downloads:
            raise RuntimeError(f"Maximum download count reached: {self.max_downloads}")
        self._downloads += 1

    def _already_completed(self, task_key: str) -> bool:
        if not self.resume:
            return False
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return state.get(task_key, {}).get("status") == "completed"

    def _record_state(self, task_key: str, status: str) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        state[task_key] = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log("state", task_key=task_key, status=status)

    def _log(self, event: str, **fields: Any) -> None:
        payload = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
