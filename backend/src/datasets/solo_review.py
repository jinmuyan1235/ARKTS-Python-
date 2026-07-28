"""Audit ledger for visual OCSR review and trusted structure confirmation.

The ledger intentionally separates what a non-chemist can verify from a
chemical ground truth.  A model prediction, including one typed by a reviewer,
is never promoted to an exact-match benchmark label by this module.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image
from rdkit import Chem

import config
from src.chem.smiles_validator import validate_smiles
from src.datasets.splits import scaffold_for_smiles
from src.utils.file_utils import ensure_directory, safe_stem


VISUAL_REVIEW_STATUSES = (
    "valid_single_molecule_crop",
    "reaction",
    "multiple_molecules",
    "text",
    "table",
    "figure",
    "logo",
    "blank",
    "invalid_crop",
    "missing_source_file",
    "uncertain_visual",
)
VISUAL_REJECTED_STATUSES = {
    "reaction", "multiple_molecules", "text", "table", "figure", "logo", "blank", "invalid_crop",
}
TRUSTED_GROUND_TRUTH_ORIGINS = frozenset({
    "pubchem", "chembl", "supplementary_sdf", "supplementary_smiles", "curated_database", "chemist_manual",
})
REGION_TYPES = (
    "molecule", "reaction", "multiple_molecules", "text", "table", "figure", "logo", "blank", "invalid_crop",
)
# Retained as a small compatibility surface for code that imported the old name.
SOLO_STATUSES = VISUAL_REVIEW_STATUSES
REVIEW_SCOPES = {
    "pending_human_review": {"pending_human_review"},
    "machine_verified": {"machine_verified"},
    "pending_machine_review": {"pending_machine_review"},
    "machine_rejected": {"rejected_invalid", "rejected_license"},
    "all_reviewable": {"pending_human_review", "machine_verified", "pending_machine_review"},
    "all_samples": {
        "pending_human_review", "machine_verified", "pending_machine_review",
        "rejected_invalid", "rejected_license", "human_verified", "human_rejected",
    },
}
OUTCOME_FIELDS = (
    "sample_id", "dataset_root", "image_path", "source_page_path", "resolved_image_path", "resolved_source_page_path",
    "visual_review_status", "structure_review_status", "verification_status", "expected_action", "category",
    "source", "source_document", "source_url", "source_license", "attribution", "split", "scaffold_key",
    "ground_truth_origin", "ground_truth_smiles", "ground_truth_canonical_smiles", "ground_truth_inchikey",
    "source_compound_id", "source_structure_file", "molscribe_matches_ground_truth",
    "decimer_matches_ground_truth", "ensemble_matches_ground_truth", "reviewed_at", "reviewer",
    "original_prediction", "final_smiles", "bbox_before", "bbox_after", "correction_types", "review_notes",
    "original_queue_status",
)
RECHECK_FIELDS = (
    "recheck_id", "sample_id", "created_at", "recheck_status", "completed_at", "source_document",
    "image_path", "category", "first_visual_review_status", "source_license", "review_notes_hidden",
)
_WINDOWS_PATH = re.compile(r"^([a-zA-Z]):[\\/](.*)$")
_WSL_MOUNT_PATH = re.compile(r"^/mnt/([a-zA-Z])/(.*)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: tuple[str, ...] = OUTCOME_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _parse_json(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _native_path(raw_path: str | Path) -> Path:
    """Translate Windows and WSL mount paths to the current runtime form."""
    raw = str(raw_path).strip()
    windows = _WINDOWS_PATH.match(raw)
    if windows:
        return Path("/mnt") / windows.group(1).lower() / windows.group(2).replace("\\", "/")
    wsl_mount = _WSL_MOUNT_PATH.match(raw.replace("\\", "/"))
    if wsl_mount and Path("/").anchor != "/":
        return Path(f"{wsl_mount.group(1).upper()}:/{wsl_mount.group(2)}")
    return Path(raw).expanduser()


@dataclass(frozen=True)
class SoloReviewResult:
    sample_id: str
    verification_status: str
    audit_path: Path
    image_path: str


class SoloReviewStore:
    """Persist visual decisions and trusted-label confirmation without changing source data."""

    def __init__(
        self,
        dataset_root: str | Path = config.DATA_DIR / "ocsr_collections",
        *,
        review_root: str | Path = config.DATA_DIR / "review",
        strict_dataset_root: bool = False,
    ) -> None:
        self.dataset_root = _native_path(dataset_root).resolve()
        self.review_root = ensure_directory(_native_path(review_root).resolve())
        self.strict_dataset_root = strict_dataset_root
        self.machine_manifest_path = self.review_root / "machine_review_manifest.csv"
        self.queue_path = self.review_root / "human_review_queue.csv"
        self.audit_dir = ensure_directory(self.review_root / "single_reviews")
        self.crop_dir = ensure_directory(self.review_root / "single_review_crops")
        self.recheck_dir = ensure_directory(self.review_root / "rechecks")
        self.recheck_path = self.review_root / "recheck_queue.csv"
        # A store lives for one Streamlit run. Memoizing these small ledgers
        # avoids repeatedly reading them while resolving the selected sample.
        self._source_rows_cache: list[dict[str, str]] | None = None
        self._audits_cache: dict[str, dict[str, Any]] | None = None

    def list_items(self, *, scope: str = "pending_human_review", include_reviewed: bool = True) -> list[dict[str, Any]]:
        """List rows from the full machine manifest, using the old queue only as a fallback."""
        if scope not in REVIEW_SCOPES:
            raise ValueError(f"Unsupported review scope: {scope}")
        audits = self._audits()
        items: list[dict[str, Any]] = []
        for row in self._source_rows():
            if str(row.get("verification_status") or "") not in REVIEW_SCOPES[scope]:
                continue
            sample_id = str(row.get("sample_id") or "")
            audit = audits.get(sample_id)
            if audit and not include_reviewed:
                continue
            items.append(self._enrich_row(row, audit or {}))
        return sorted(items, key=lambda item: (bool(item.get("audit")), str(item.get("sample_id") or "")))

    def list_item_ids(self, *, scope: str = "pending_human_review", include_reviewed: bool = True) -> list[str]:
        """List queue identifiers without resolving paths or parsing structures."""
        if scope not in REVIEW_SCOPES:
            raise ValueError(f"Unsupported review scope: {scope}")
        audits = self._audits()
        identifiers: list[tuple[bool, str]] = []
        for row in self._source_rows():
            if str(row.get("verification_status") or "") not in REVIEW_SCOPES[scope]:
                continue
            sample_id = str(row.get("sample_id") or "")
            audit = audits.get(sample_id)
            if audit and not include_reviewed:
                continue
            if sample_id:
                identifiers.append((bool(audit), sample_id))
        return [sample_id for _, sample_id in sorted(identifiers)]

    def list_item_summaries(
        self,
        *,
        scope: str = "pending_human_review",
        include_reviewed: bool = True,
    ) -> list[dict[str, str]]:
        """Return lightweight fields for filtering and batch thumbnail pages."""
        allowed_ids = set(self.list_item_ids(scope=scope, include_reviewed=include_reviewed))
        audits = self._audits()
        summaries = [
            {
                "sample_id": str(row.get("sample_id") or ""),
                "verification_status": str(row.get("verification_status") or ""),
                "category": str(row.get("machine_category") or row.get("category") or "invalid_crop"),
                "image_quality_level": str(row.get("image_quality_level") or ""),
                "dataset_root": str(row.get("dataset_root") or ""),
                "image_path": str(row.get("image_path") or ""),
                "visual_review_status": str(
                    audits.get(str(row.get("sample_id") or ""), {}).get("visual_review_status") or ""
                ),
            }
            for row in self._source_rows()
            if str(row.get("sample_id") or "") in allowed_ids
        ]
        return sorted(summaries, key=lambda row: row["sample_id"])

    def get_item(self, sample_id: str) -> dict[str, Any] | None:
        audit = self._audits().get(sample_id, {})
        row = next(
            (row for row in self._source_rows() if str(row.get("sample_id") or "") == sample_id),
            None,
        )
        return self._enrich_row(row, audit) if row is not None else None

    def queue_stats(self) -> dict[str, int]:
        rows = self._source_rows()
        audits = self._audits()
        machine_routed_ids = {
            str(row.get("sample_id") or "") for row in rows
            if row.get("verification_status") == "pending_human_review" and row.get("sample_id")
        }
        rejected_ids = {
            str(row.get("sample_id") or "") for row in rows
            if str(row.get("verification_status") or "").startswith("rejected_")
        }
        rejected_ids.update(
            sample_id for sample_id, audit in audits.items()
            if audit.get("visual_review_status") in VISUAL_REJECTED_STATUSES
        )
        machine_rejected = sum(
            str(row.get("verification_status") or "").startswith("rejected_") for row in rows
        )
        visual_negative = sum(
            audit.get("visual_review_status") in VISUAL_REJECTED_STATUSES for audit in audits.values()
        )
        return {
            "total": len(rows),
            "machine_routed_visual": len(machine_routed_ids),
            "visual_remaining": len(machine_routed_ids.difference(audits)),
            # Compatibility alias for callers written before the statistic was clarified.
            "pending_human": len(machine_routed_ids),
            "machine_verified": sum(row.get("verification_status") == "machine_verified" for row in rows),
            "pending_machine": sum(row.get("verification_status") == "pending_machine_review" for row in rows),
            "reviewed": len(audits),
            "rejected": len({sample_id for sample_id in rejected_ids if sample_id}),
            "machine_rejected": machine_rejected,
            "visual_negative": visual_negative,
            "positive_candidates": sum(
                (row.get("machine_category") or row.get("category")) == "molecule" for row in rows
            ),
            "negative_candidates": sum(
                (row.get("machine_category") or row.get("category")) != "molecule" for row in rows
            ),
            "trusted_structure_labels": sum(
                str(row.get("ground_truth_origin") or "").strip().lower() in TRUSTED_GROUND_TRUTH_ORIGINS
                and self._structure(str(row.get("ground_truth_smiles") or ""))["valid"]
                for row in rows
            ),
        }

    def submit_visual(
        self,
        sample_id: str,
        *,
        visual_review_status: str,
        bbox_after: list[int] | tuple[int, int, int, int] | None = None,
        region_type: str | None = None,
        review_notes: str = "",
        reviewer: str = "local",
    ) -> SoloReviewResult:
        """Record an image/region-only decision; no SMILES is accepted here."""
        payload = self._visual_review_payload(
            sample_id,
            visual_review_status=visual_review_status,
            bbox_after=bbox_after,
            region_type=region_type,
            review_notes=review_notes,
            reviewer=reviewer,
        )
        return self._write_audit(payload)

    def submit_visual_batch(
        self,
        sample_ids: Iterable[str],
        *,
        visual_review_status: str,
        region_type: str,
        review_notes: str = "",
        reviewer: str = "local",
    ) -> dict[str, Any]:
        """Validate a batch, write every audit, then rebuild exports once."""
        identifiers = list(dict.fromkeys(str(sample_id) for sample_id in sample_ids if str(sample_id)))
        if not identifiers:
            raise ValueError("Select at least one sample for batch classification.")
        payloads = [
            self._visual_review_payload(
                sample_id,
                visual_review_status=visual_review_status,
                region_type=region_type,
                review_notes=review_notes,
                reviewer=reviewer,
            )
            for sample_id in identifiers
        ]
        # All validation is complete before the first audit file is changed.
        for payload in payloads:
            audit_path = self.audit_dir / f"{safe_stem(str(payload['sample_id']))}.json"
            audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._audits_cache = None
        outputs = self.export_outcomes()
        return {
            "reviewed_count": len(payloads),
            "sample_ids": identifiers,
            "visual_review_status": visual_review_status,
            "region_type": region_type,
            "outputs": {name: str(path) for name, path in outputs.items()},
        }

    def _visual_review_payload(
        self,
        sample_id: str,
        *,
        visual_review_status: str,
        bbox_after: list[int] | tuple[int, int, int, int] | None = None,
        region_type: str | None = None,
        review_notes: str = "",
        reviewer: str = "local",
    ) -> dict[str, Any]:
        if visual_review_status not in VISUAL_REVIEW_STATUSES:
            raise ValueError(f"Unsupported visual review status: {visual_review_status}")
        item = self._required_item(sample_id)
        if not item["files_complete"] and visual_review_status != "missing_source_file":
            raise ValueError("Missing source files must be recorded as missing_source_file.")
        before = self._bbox(item.get("bbox"))
        after = list(bbox_after) if bbox_after is not None else before
        if after and not self._valid_bbox(after):
            raise ValueError("bbox_after must be [x1, y1, x2, y2] with a positive area.")
        final_region = str(region_type or item.get("machine_category") or item.get("category") or "invalid_crop").lower()
        if final_region not in REGION_TYPES:
            raise ValueError(f"Unsupported region type: {final_region}")
        existing = item.get("audit") or {}
        preserve_structure = (
            visual_review_status == "valid_single_molecule_crop"
            and existing.get("structure_review_status") == "structure_ground_truth_verified"
        )
        corrections = self._correction_types(item, before, after, final_region)
        payload = {
            **existing,
            "sample_id": sample_id,
            "reviewed_at": _now(),
            "visual_reviewed_at": _now(),
            "reviewer": reviewer.strip() or "local",
            "verification_status": visual_review_status,
            "visual_review_status": visual_review_status,
            "structure_review_status": existing.get("structure_review_status", "") if preserve_structure else "",
            "original_prediction": self._original_predictions(item),
            "final_smiles": existing.get("final_smiles", "") if preserve_structure else "",
            "final_canonical_smiles": existing.get("final_canonical_smiles", "") if preserve_structure else "",
            "final_inchikey": existing.get("final_inchikey", "") if preserve_structure else "",
            "bbox_before": before,
            "bbox_after": after,
            "region_type_before": item.get("machine_category") or item.get("category"),
            "region_type_after": final_region,
            "correction_types": corrections,
            "review_notes": review_notes,
            "reviewed_image_path": self._review_image(item, after, before),
            "source_queue_row": self._source_snapshot(item),
        }
        return payload

    def submit_structure_ground_truth(
        self,
        sample_id: str,
        *,
        review_notes: str = "",
        reviewer: str = "local",
    ) -> SoloReviewResult:
        """Confirm an already supplied, whitelisted external structure label."""
        item = self._required_item(sample_id)
        if not item["files_complete"]:
            raise ValueError("Cannot accept ground truth while a source page or crop file is missing.")
        if not item["trusted_ground_truth_available"]:
            raise ValueError("No trusted external ground truth is available for this sample.")
        audit = item.get("audit") or {}
        if audit.get("visual_review_status") != "valid_single_molecule_crop":
            raise ValueError("Complete Visual Review as valid_single_molecule_crop before confirming structure ground truth.")
        truth = self._structure(str(item.get("ground_truth_smiles") or ""))
        if not truth["valid"]:
            raise ValueError("Trusted ground-truth SMILES is invalid.")
        payload = {
            **audit,
            "reviewed_at": _now(),
            "structure_reviewed_at": _now(),
            "reviewer": reviewer.strip() or "local",
            "verification_status": "structure_ground_truth_verified",
            "structure_review_status": "structure_ground_truth_verified",
            "final_smiles": str(item.get("ground_truth_smiles") or ""),
            "final_canonical_smiles": truth["canonical_smiles"],
            "final_inchikey": truth["inchikey"],
            "review_notes": review_notes or audit.get("review_notes", ""),
            "source_queue_row": self._source_snapshot(item),
        }
        return self._write_audit(payload)

    def submit(
        self,
        sample_id: str,
        *,
        verification_status: str,
        final_smiles: str = "",
        bbox_after: list[int] | tuple[int, int, int, int] | None = None,
        region_type: str | None = None,
        review_notes: str = "",
        reviewer: str = "local",
        selected_prediction: str = "",
    ) -> SoloReviewResult:
        """Compatibility wrapper for the former single-review API.

        The wrapper deliberately refuses to convert a typed or model SMILES into
        ground truth.  New callers should use ``submit_visual`` and, where
        allowed, ``submit_structure_ground_truth``.
        """
        legacy = {
            "rejected": "invalid_crop",
            "uncertain": "uncertain_visual",
            "human_verified_single": "valid_single_molecule_crop",
        }
        if verification_status not in legacy:
            raise ValueError(f"Unsupported single-review status: {verification_status}")
        result = self.submit_visual(
            sample_id,
            visual_review_status=legacy[verification_status],
            bbox_after=bbox_after,
            region_type=region_type,
            review_notes=review_notes,
            reviewer=reviewer,
        )
        if verification_status == "human_verified_single":
            return self.submit_structure_ground_truth(sample_id, review_notes=review_notes, reviewer=reviewer)
        return result

    def create_recheck_queue(
        self,
        proportion: float,
        *,
        seed: int | None = None,
        max_samples: int | None = None,
    ) -> dict[str, Any]:
        if not 0.0 <= proportion <= 1.0:
            raise ValueError("proportion must be between 0 and 1.")
        if max_samples is not None and max_samples < 1:
            raise ValueError("max_samples must be positive when supplied.")
        eligible = [
            audit for audit in self._audits().values()
            if audit.get("visual_review_status") in VISUAL_REVIEW_STATUSES
        ]
        randomizer = random.Random(seed)
        target = round(len(eligible) * proportion)
        if proportion > 0.0 and eligible:
            target = max(1, target)
        if max_samples is not None:
            target = min(target, max_samples)
        target = min(target, len(eligible))
        audits_by_id = {str(audit["sample_id"]): audit for audit in eligible}
        existing = {
            str(row.get("sample_id") or ""): row for row in _read_csv(self.recheck_path)
            if str(row.get("sample_id") or "") in audits_by_id
        }
        strata: dict[str, list[dict[str, Any]]] = {}
        for audit in eligible:
            if str(audit["sample_id"]) in existing:
                continue
            strata.setdefault(str(audit["visual_review_status"]), []).append(audit)
        for audits in strata.values():
            randomizer.shuffle(audits)
        all_statuses = sorted({str(audit["visual_review_status"]) for audit in eligible})
        class_counts = {
            status: sum(audit["visual_review_status"] == status for audit in eligible)
            for status in all_statuses
        }
        ideals = {status: target * class_counts[status] / len(eligible) for status in all_statuses}
        quotas = {status: 0 for status in all_statuses}
        if target >= len(all_statuses):
            quotas = {
                status: min(class_counts[status], max(1, int(ideals[status])))
                for status in all_statuses
            }
            while sum(quotas.values()) > target:
                status = min(
                    (name for name in all_statuses if quotas[name] > 1),
                    key=lambda name: (ideals[name] - quotas[name], name),
                )
                quotas[status] -= 1
            while sum(quotas.values()) < target:
                status = max(
                    (name for name in all_statuses if quotas[name] < class_counts[name]),
                    key=lambda name: (ideals[name] - quotas[name], class_counts[name], name),
                )
                quotas[status] += 1
        else:
            statuses = list(all_statuses)
            randomizer.shuffle(statuses)
            for status in statuses[:target]:
                quotas[status] = 1
        existing_by_status = {
            status: sum(
                audits_by_id[sample_id]["visual_review_status"] == status
                for sample_id in existing
            )
            for status in all_statuses
        }
        selected: list[dict[str, Any]] = []
        for status in all_statuses:
            needed = max(0, quotas[status] - existing_by_status[status])
            selected.extend(strata.get(status, [])[:needed])
            strata[status] = strata.get(status, [])[needed:]
        remaining_slots = max(0, target - len(existing) - len(selected))
        remainder = [audit for status in all_statuses for audit in strata.get(status, [])]
        randomizer.shuffle(remainder)
        selected.extend(remainder[:remaining_slots])
        for audit in selected:
            sample_id = str(audit["sample_id"])
            if sample_id in existing:
                continue
            source = audit.get("source_queue_row") or {}
            existing[sample_id] = {
                "recheck_id": f"recheck_{safe_stem(sample_id)}", "sample_id": sample_id, "created_at": _now(),
                "recheck_status": "pending", "completed_at": "", "source_document": source.get("source_document", ""),
                "image_path": audit.get("reviewed_image_path") or source.get("image_path", ""),
                "category": audit.get("region_type_after") or source.get("category", ""),
                "first_visual_review_status": audit.get("visual_review_status", ""),
                "source_license": source.get("source_license", ""), "review_notes_hidden": "true",
            }
        rows = sorted(existing.values(), key=lambda row: str(row.get("sample_id") or ""))
        _write_csv(self.recheck_path, rows, RECHECK_FIELDS)
        report = self._write_consistency_report()
        selected_by_status = {
            status: sum(audit.get("visual_review_status") == status for audit in selected)
            for status in sorted({str(audit.get("visual_review_status") or "") for audit in selected})
        }
        return {
            "selected": len(selected), "selected_by_visual_status": selected_by_status,
            "queue_size": len(rows), "recheck_queue": str(self.recheck_path), "consistency_report": str(report),
        }

    def list_recheck_items(self, *, pending_only: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in _read_csv(self.recheck_path):
            if pending_only and row.get("recheck_status") != "pending":
                continue
            item = self.get_item(str(row.get("sample_id") or ""))
            if item is None:
                continue
            item["recheck"] = row
            item["audit"] = {}
            item["first_review_hidden"] = True
            items.append(item)
        return items

    def submit_recheck(
        self,
        sample_id: str,
        *,
        visual_review_status: str,
        bbox_after: list[int] | tuple[int, int, int, int] | None = None,
        region_type: str | None = None,
        review_notes: str = "",
    ) -> Path:
        payload = self._recheck_payload(
            sample_id,
            visual_review_status=visual_review_status,
            bbox_after=bbox_after,
            region_type=region_type,
            review_notes=review_notes,
        )
        path = self.recheck_dir / f"{safe_stem(sample_id)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._mark_rechecks_completed({sample_id: payload["reviewed_at"]})
        self._write_consistency_report()
        return path

    def submit_recheck_batch(
        self,
        sample_ids: Iterable[str],
        *,
        visual_review_status: str,
        region_type: str,
        review_notes: str = "",
    ) -> dict[str, Any]:
        """Validate and persist a delayed-recheck batch, then report consistency once."""
        identifiers = list(dict.fromkeys(str(sample_id) for sample_id in sample_ids if str(sample_id)))
        if not identifiers:
            raise ValueError("Select at least one sample for batch recheck.")
        payloads = [
            self._recheck_payload(
                sample_id,
                visual_review_status=visual_review_status,
                region_type=region_type,
                review_notes=review_notes,
            )
            for sample_id in identifiers
        ]
        for payload in payloads:
            path = self.recheck_dir / f"{safe_stem(str(payload['sample_id']))}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._mark_rechecks_completed({str(payload["sample_id"]): str(payload["reviewed_at"]) for payload in payloads})
        report_path = self._write_consistency_report()
        return {
            "reviewed_count": len(payloads), "sample_ids": identifiers,
            "visual_review_status": visual_review_status, "region_type": region_type,
            "consistency_report": str(report_path),
        }

    def _recheck_payload(
        self,
        sample_id: str,
        *,
        visual_review_status: str,
        bbox_after: list[int] | tuple[int, int, int, int] | None = None,
        region_type: str | None = None,
        review_notes: str = "",
    ) -> dict[str, Any]:
        if visual_review_status not in VISUAL_REVIEW_STATUSES:
            raise ValueError("Unsupported recheck visual status.")
        queued = {
            str(row.get("sample_id") or "") for row in _read_csv(self.recheck_path)
            if row.get("recheck_status") == "pending"
        }
        item = self.get_item(sample_id) if sample_id in queued else None
        if item is None:
            raise ValueError(f"Sample is not pending in the recheck queue: {sample_id}")
        before = self._bbox(item.get("bbox"))
        after = list(bbox_after) if bbox_after is not None else before
        if after and not self._valid_bbox(after):
            raise ValueError("bbox_after must have a positive area.")
        final_region = str(region_type or item.get("machine_category") or item.get("category") or "invalid_crop").lower()
        return {
            "sample_id": sample_id, "reviewed_at": _now(), "visual_review_status": visual_review_status,
            "bbox_after": after, "region_type_after": final_region, "review_notes": review_notes,
        }

    def _mark_rechecks_completed(self, completed: dict[str, str]) -> None:
        rows = _read_csv(self.recheck_path)
        for row in rows:
            sample_id = str(row.get("sample_id") or "")
            if sample_id in completed:
                row["recheck_status"] = "completed"
                row["completed_at"] = completed[sample_id]
        _write_csv(self.recheck_path, rows, RECHECK_FIELDS)

    def export_outcomes(self) -> dict[str, Path]:
        outcomes = [self._outcome_row(audit) for audit in self._audits().values()]
        paths = {
            "visual_verified": self.review_root / "visual_verified.csv",
            "visual_rejected": self.review_root / "visual_rejected.csv",
            "detector_training_manifest": self.review_root / "detector_training_manifest.csv",
            "missing_files": self.review_root / "missing_files.csv",
            "structure_ground_truth_verified": self.review_root / "structure_ground_truth_verified.csv",
            "chemistry_review_required": self.review_root / "chemistry_review_required.csv",
        }
        _write_csv(paths["visual_verified"], [row for row in outcomes if row["visual_review_status"] == "valid_single_molecule_crop"])
        _write_csv(paths["visual_rejected"], [row for row in outcomes if row["visual_review_status"] in VISUAL_REJECTED_STATUSES])
        _write_csv(paths["detector_training_manifest"], [
            row for row in outcomes
            if row["visual_review_status"] == "valid_single_molecule_crop"
            or row["visual_review_status"] in VISUAL_REJECTED_STATUSES
        ])
        _write_csv(paths["missing_files"], [row for row in outcomes if row["visual_review_status"] == "missing_source_file"])
        _write_csv(paths["structure_ground_truth_verified"], [row for row in outcomes if row["structure_review_status"] == "structure_ground_truth_verified"])
        _write_csv(paths["chemistry_review_required"], [
            row for row in outcomes
            if row["visual_review_status"] == "valid_single_molecule_crop"
            and row["structure_review_status"] != "structure_ground_truth_verified"
        ])
        return paths

    def resolve_dataset_path(self, raw_path: str | None, *, dataset_root: str | None = None) -> str | None:
        info = self.resolve_dataset_path_info(raw_path, dataset_root=dataset_root)
        return str(info["resolved_path"]) if info["exists"] else None

    def resolve_dataset_path_info(self, raw_path: str | None, *, dataset_root: str | None = None) -> dict[str, str | bool]:
        raw = str(raw_path or "").strip()
        roots = self._candidate_roots(dataset_root)
        if not raw:
            return {"manifest_path": "", "dataset_root": str(roots[0]), "resolved_path": "", "exists": False}
        path = _native_path(raw)
        candidates = [path.resolve()] if path.is_absolute() else [(root / path).resolve() for root in roots]
        selected = candidates[0]
        selected_root = roots[0]
        for candidate in candidates:
            if candidate.is_file():
                selected = candidate
                selected_root = next((root for root in roots if candidate == root or root in candidate.parents), roots[0])
                break
        return {
            "manifest_path": raw, "dataset_root": str(selected_root), "resolved_path": str(selected), "exists": selected.is_file(),
        }

    def prediction_redraw(self, sample_id: str, backend: str, smiles: str) -> str | None:
        """Render a model prediction separately from source images."""
        from src.chem.mol_drawer import draw_molecule

        if not self._structure(smiles)["valid"]:
            return None
        path = self.review_root / "prediction_redraws" / f"{safe_stem(sample_id)}_{safe_stem(backend)}.png"
        if not path.is_file():
            try:
                draw_molecule(smiles, path)
            except Exception:
                return None
        return str(path.resolve())

    def _enrich_row(self, row: dict[str, str], audit: dict[str, Any]) -> dict[str, Any]:
        explicit_root = str(row.get("dataset_root") or "")
        crop_info = self.resolve_dataset_path_info(row.get("crop_path") or row.get("image_path"), dataset_root=explicit_root)
        image_info = self.resolve_dataset_path_info(row.get("image_path"), dataset_root=explicit_root)
        page_info = self.resolve_dataset_path_info(row.get("source_page_path"), dataset_root=explicit_root)
        root = str(crop_info["dataset_root"] or page_info["dataset_root"] or self.dataset_root)
        truth_origin = str(row.get("ground_truth_origin") or "").strip().lower()
        truth_smiles = str(row.get("ground_truth_smiles") or row.get("source_canonical_smiles") or "")
        truth = self._structure(truth_smiles)
        trusted = truth_origin in TRUSTED_GROUND_TRUTH_ORIGINS and truth["valid"]
        item: dict[str, Any] = {
            **row,
            "audit": audit,
            "dataset_root_resolved": root,
            "crop_path_info": crop_info,
            "image_path_info": image_info,
            "page_path_info": page_info,
            "crop_path_abs": str(crop_info["resolved_path"]) if crop_info["exists"] else None,
            "image_path_abs": str(image_info["resolved_path"]) if image_info["exists"] else None,
            "page_path_abs": str(page_info["resolved_path"]) if page_info["exists"] else None,
            "files_complete": bool(crop_info["exists"] and image_info["exists"] and page_info["exists"]),
            "missing_source_files": [
                name for name, info in (("source_page_path", page_info), ("image_path", image_info), ("crop", crop_info))
                if not info["exists"]
            ],
            "ground_truth_origin": truth_origin,
            "ground_truth_smiles": truth_smiles if trusted else "",
            "ground_truth_canonical_smiles": truth["canonical_smiles"] if trusted else "",
            "ground_truth_inchikey": str(row.get("ground_truth_inchikey") or row.get("source_inchikey") or truth["inchikey"]) if trusted else "",
            "trusted_ground_truth_available": trusted,
            "effective_status": str(audit.get("visual_review_status") or row.get("verification_status") or ""),
        }
        for backend in ("molscribe", "decimer", "ensemble"):
            predicted = str(row.get(f"{backend}_inchikey") or self._structure(str(row.get(f"{backend}_smiles") or ""))["inchikey"])
            item[f"{backend}_matches_ground_truth"] = bool(trusted and predicted and predicted == item["ground_truth_inchikey"])
        return item

    def _candidate_roots(self, explicit_root: str | None) -> list[Path]:
        roots: list[Path] = []
        configured = (explicit_root or "", self.dataset_root)
        fallbacks = (
            config.DATA_DIR / "ocsr_first_batch_final", config.DATA_DIR / "ocsr_first_batch",
            config.DATA_DIR / "ocsr_collections",
        )
        for value in configured + (() if self.strict_dataset_root else fallbacks):
            if not str(value):
                continue
            root = _native_path(value).resolve()
            if root not in roots:
                roots.append(root)
        return roots or [self.dataset_root]

    def _required_item(self, sample_id: str) -> dict[str, Any]:
        item = self.get_item(sample_id)
        if item is None:
            raise ValueError(f"Unknown sample_id: {sample_id}")
        return item

    def _source_rows(self) -> list[dict[str, str]]:
        if self._source_rows_cache is None:
            self._source_rows_cache = (
                _read_csv(self.machine_manifest_path)
                if self.machine_manifest_path.is_file()
                else _read_csv(self.queue_path)
            )
        return self._source_rows_cache

    def _audits(self) -> dict[str, dict[str, Any]]:
        if self._audits_cache is not None:
            return self._audits_cache
        audits: dict[str, dict[str, Any]] = {}
        for path in self.audit_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            sample_id = str(payload.get("sample_id") or "")
            if sample_id:
                audits[sample_id] = payload
        self._audits_cache = audits
        return self._audits_cache

    @staticmethod
    def _bbox(raw_bbox: str | list[int] | tuple[int, ...] | None) -> list[int]:
        value = _parse_json(raw_bbox, []) if isinstance(raw_bbox, str) else raw_bbox
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return []
        try:
            return [int(number) for number in value]
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _valid_bbox(bbox: list[int]) -> bool:
        return len(bbox) == 4 and bbox[0] >= 0 and bbox[1] >= 0 and bbox[2] > bbox[0] and bbox[3] > bbox[1]

    @staticmethod
    def _structure(smiles: str) -> dict[str, Any]:
        validation = validate_smiles(smiles)
        canonical = str(validation.get("canonical_smiles") or "")
        if not validation.get("valid"):
            return {"valid": False, "canonical_smiles": "", "inchikey": ""}
        molecule = Chem.MolFromSmiles(canonical)
        return {"valid": molecule is not None, "canonical_smiles": canonical, "inchikey": Chem.MolToInchiKey(molecule) if molecule is not None else ""}

    @staticmethod
    def _original_predictions(item: dict[str, Any]) -> dict[str, Any]:
        return {backend: _parse_json(item.get(f"{backend}_raw"), {"smiles": item.get(f"{backend}_smiles") or ""}) for backend in ("molscribe", "decimer", "ensemble")}

    def _review_image(self, item: dict[str, Any], bbox_after: list[int], bbox_before: list[int]) -> str:
        if bbox_after != bbox_before and item.get("page_path_abs") and bbox_after:
            page = Path(str(item["page_path_abs"]))
            try:
                with Image.open(page) as image:
                    x1, y1, x2, y2 = bbox_after
                    if x2 > image.width or y2 > image.height:
                        raise ValueError("bbox_after exceeds the source page")
                    output = self.crop_dir / f"{safe_stem(str(item['sample_id']))}.png"
                    image.crop((x1, y1, x2, y2)).convert("RGB").save(output)
                    return str(output.resolve())
            except Exception as exc:
                raise ValueError(f"Could not create corrected crop: {exc}") from exc
        return str(item.get("crop_path_abs") or "")

    def _correction_types(self, item: dict[str, Any], before: list[int], after: list[int], region: str) -> list[str]:
        corrections: list[str] = []
        if after != before:
            corrections.append("bbox")
        if region != str(item.get("machine_category") or item.get("category") or ""):
            corrections.append("region_type")
        return corrections

    @staticmethod
    def _source_snapshot(item: dict[str, Any]) -> dict[str, Any]:
        omit = {"audit", "crop_path_info", "image_path_info", "page_path_info", "crop_path_abs", "image_path_abs", "page_path_abs", "missing_source_files"}
        return {key: value for key, value in item.items() if key not in omit}

    def _write_audit(self, payload: dict[str, Any]) -> SoloReviewResult:
        audit_path = self.audit_dir / f"{safe_stem(str(payload['sample_id']))}.json"
        audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._audits_cache = None
        self.export_outcomes()
        return SoloReviewResult(str(payload["sample_id"]), str(payload["verification_status"]), audit_path, str(payload.get("reviewed_image_path") or ""))

    def _outcome_row(self, audit: dict[str, Any]) -> dict[str, Any]:
        source = audit.get("source_queue_row") or {}
        visual_status = str(audit.get("visual_review_status") or "")
        structure_verified = audit.get("structure_review_status") == "structure_ground_truth_verified"
        if visual_status == "valid_single_molecule_crop":
            expected_action = "recognize"
        elif visual_status in VISUAL_REJECTED_STATUSES:
            expected_action = "reject"
        else:
            expected_action = ""
        truth = self._structure(str(audit.get("final_smiles") or "")) if structure_verified else self._structure("")
        origin = str(source.get("ground_truth_origin") or "").lower()
        return {
            "sample_id": audit.get("sample_id", ""), "dataset_root": source.get("dataset_root_resolved") or source.get("dataset_root", ""),
            "image_path": audit.get("reviewed_image_path", "") or source.get("image_path", ""),
            "source_page_path": source.get("source_page_path", ""), "resolved_image_path": source.get("crop_path_abs", ""),
            "resolved_source_page_path": source.get("page_path_abs", ""), "visual_review_status": audit.get("visual_review_status", ""),
            "structure_review_status": audit.get("structure_review_status", ""), "verification_status": audit.get("verification_status", ""),
            "expected_action": expected_action, "category": audit.get("region_type_after") or source.get("machine_category") or source.get("category", ""),
            "source": source.get("source_kind", ""), "source_document": source.get("source_document", ""), "source_url": source.get("source_url", ""),
            "source_license": source.get("source_license", ""), "attribution": source.get("attribution", ""), "split": source.get("split", ""),
            "scaffold_key": scaffold_for_smiles(truth["canonical_smiles"]) if structure_verified else "",
            "ground_truth_origin": origin if structure_verified else "", "ground_truth_smiles": audit.get("final_smiles", "") if structure_verified else "",
            "ground_truth_canonical_smiles": truth["canonical_smiles"] if structure_verified else "", "ground_truth_inchikey": truth["inchikey"] if structure_verified else "",
            "source_compound_id": source.get("source_compound_id") or source.get("source_id", "") if structure_verified else "",
            "source_structure_file": source.get("source_structure_file", "") if structure_verified else "",
            "molscribe_matches_ground_truth": str(source.get("molscribe_matches_ground_truth", "")).lower() if structure_verified else "",
            "decimer_matches_ground_truth": str(source.get("decimer_matches_ground_truth", "")).lower() if structure_verified else "",
            "ensemble_matches_ground_truth": str(source.get("ensemble_matches_ground_truth", "")).lower() if structure_verified else "",
            "reviewed_at": audit.get("reviewed_at", ""), "reviewer": audit.get("reviewer", ""),
            "original_prediction": _json(audit.get("original_prediction") or {}), "final_smiles": audit.get("final_smiles", "") if structure_verified else "",
            "bbox_before": _json(audit.get("bbox_before") or []), "bbox_after": _json(audit.get("bbox_after") or []),
            "correction_types": _json(audit.get("correction_types") or []), "review_notes": audit.get("review_notes", ""),
            "original_queue_status": source.get("verification_status", ""),
        }

    def _write_consistency_report(self) -> Path:
        completed: list[dict[str, Any]] = []
        for path in self.recheck_dir.glob("*.json"):
            try:
                completed.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        audits = self._audits()
        comparisons: list[dict[str, Any]] = []
        for recheck in completed:
            first = audits.get(str(recheck.get("sample_id") or ""))
            if not first:
                continue
            same_visual = first.get("visual_review_status") == recheck.get("visual_review_status")
            same_bbox = list(first.get("bbox_after") or []) == list(recheck.get("bbox_after") or [])
            same_region = first.get("region_type_after") == recheck.get("region_type_after")
            comparisons.append({
                "sample_id": recheck.get("sample_id"),
                "first_visual_review_status": first.get("visual_review_status"),
                "same_visual_status": same_visual, "same_bbox": same_bbox,
                "same_region_type": same_region, "consistent": same_visual and same_bbox and same_region,
            })
        total = len(comparisons)
        by_status: dict[str, dict[str, Any]] = {}
        for item in comparisons:
            status = str(item.get("first_visual_review_status") or "unknown")
            aggregate = by_status.setdefault(status, {"completed_rechecks": 0, "consistent_rechecks": 0})
            aggregate["completed_rechecks"] += 1
            aggregate["consistent_rechecks"] += int(bool(item["consistent"]))
        for aggregate in by_status.values():
            aggregate["consistency_rate"] = aggregate["consistent_rechecks"] / aggregate["completed_rechecks"]
        consistent = sum(item["consistent"] for item in comparisons)
        report = {
            "completed_rechecks": total, "consistent_rechecks": consistent,
            "consistency_rate": consistent / total if total else None,
            "by_visual_review_status": by_status, "comparisons": comparisons,
        }
        path = self.review_root / "review_consistency_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
