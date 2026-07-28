"""Independent review workflow for correction feedback samples."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.feedback.store import (
    export_feedback_manifest,
    feedback_root,
    load_feedback_annotation,
    read_feedback_manifest,
    revise_feedback_correction,
    update_feedback_review,
)


REVIEW_ACTIONS = {
    "approve": ("verified", "accepted_for_dataset", True),
    "return": ("returned", "correction_only", False),
    "reject": ("rejected", "correction_only", False),
    "duplicate": ("duplicate", "correction_only", False),
    "license_unclear": ("license_unclear", "correction_only", False),
}


def _require_identity(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


class FeedbackReviewService:
    """List and update feedback samples that require independent review."""

    def __init__(self, output_dir: str | Path) -> None:
        self.root = feedback_root(output_dir)

    def list_items(self, status: str = "pending", query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Return review items with manifest row and annotation payload."""
        query = query.strip().lower()
        items: list[dict[str, Any]] = []
        for row in read_feedback_manifest(self.root):
            if status != "all" and row.get("review_status") != status:
                continue
            annotation = load_feedback_annotation(self.root, row)
            item = self._build_item(row, annotation)
            if query and not self._matches(item, query):
                continue
            items.append(item)
        items.sort(key=lambda item: str(item.get("saved_at") or ""), reverse=True)
        return items[:limit]

    def get_item(self, analysis_id: str) -> dict[str, Any] | None:
        """Return one review item by analysis id."""
        for row in read_feedback_manifest(self.root):
            if row.get("analysis_id") == analysis_id:
                return self._build_item(row, load_feedback_annotation(self.root, row))
        return None

    def approve_for_dataset(self, analysis_id: str, reviewer_notes: str = "", reviewer: str = "") -> dict[str, Any]:
        """Mark an item verified and eligible for training export."""
        return self._apply_action(analysis_id, "approve", reviewer_notes, reviewer=reviewer)

    def return_for_revision(self, analysis_id: str, reviewer_notes: str = "", reviewer: str = "") -> dict[str, Any]:
        """Return an item to correction without adding it to training data."""
        return self._apply_action(analysis_id, "return", reviewer_notes, reviewer=reviewer)

    def reject_sample(self, analysis_id: str, reviewer_notes: str = "", reviewer: str = "") -> dict[str, Any]:
        """Reject an item without adding it to training data."""
        return self._apply_action(analysis_id, "reject", reviewer_notes, reviewer=reviewer)

    def mark_duplicate(self, analysis_id: str, duplicate_of: str = "", reviewer_notes: str = "", reviewer: str = "") -> dict[str, Any]:
        """Mark an item as a duplicate sample."""
        status, action, include = REVIEW_ACTIONS["duplicate"]
        reviewer = _require_identity(reviewer, "reviewer")
        return update_feedback_review(
            self.root,
            analysis_id,
            status,
            feedback_action=action,
            include_in_training=include,
            reviewer_notes=reviewer_notes,
            reviewer=reviewer,
            duplicate_of=duplicate_of,
        )

    def mark_license_unclear(self, analysis_id: str, reviewer_notes: str = "", reviewer: str = "") -> dict[str, Any]:
        """Mark an item as blocked by unclear license/source permission."""
        status, action, include = REVIEW_ACTIONS["license_unclear"]
        reviewer = _require_identity(reviewer, "reviewer")
        return update_feedback_review(
            self.root,
            analysis_id,
            status,
            feedback_action=action,
            include_in_training=include,
            reviewer_notes=reviewer_notes,
            reviewer=reviewer,
            source_license="unclear",
        )

    def revise_and_resubmit(
        self,
        analysis_id: str,
        corrected_smiles: str,
        revised_by: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a new correction revision and move the item back to pending review."""
        revised_by = _require_identity(revised_by, "revised_by")
        return revise_feedback_correction(
            self.root,
            analysis_id,
            corrected_smiles,
            revised_by=revised_by,
            notes=notes,
        )

    def export_verified_manifest(self, output_manifest: str | Path, split: str = "train") -> dict[str, Any]:
        """Export only independently verified training samples."""
        return export_feedback_manifest(self.root, output_manifest, split=split, review_status="verified")

    def _apply_action(self, analysis_id: str, action_name: str, reviewer_notes: str, reviewer: str = "") -> dict[str, Any]:
        status, action, include = REVIEW_ACTIONS[action_name]
        reviewer = _require_identity(reviewer, "reviewer")
        return update_feedback_review(
            self.root,
            analysis_id,
            status,
            feedback_action=action,
            include_in_training=include,
            reviewer_notes=reviewer_notes,
            reviewer=reviewer,
        )

    def _build_item(self, row: dict[str, str], annotation: dict[str, Any] | None) -> dict[str, Any]:
        annotation = annotation or {}
        prediction = annotation.get("prediction") or {}
        correction = annotation.get("correction") or {}
        feedback = annotation.get("feedback") or {}
        final = annotation.get("final") or {}
        original_input = annotation.get("original_input") or {}
        item = {
            **row,
            "annotation": annotation,
            "prediction": prediction,
            "correction": correction,
            "feedback": feedback,
            "final": final,
            "original_input": original_input,
            "predicted_smiles": prediction.get("predicted_smiles") or row.get("predicted_smiles"),
            "corrected_smiles": correction.get("corrected_smiles") or row.get("corrected_smiles"),
            "model_name": prediction.get("model_name") or row.get("model_name"),
            "model_version": prediction.get("model_version") or row.get("model_version"),
            "source_reference": feedback.get("source_reference") or row.get("source_reference"),
            "source_license": feedback.get("source_license") or row.get("source_license"),
            "reviewer": feedback.get("reviewer") or row.get("reviewer"),
            "reviewed_at": feedback.get("reviewed_at") or row.get("reviewed_at"),
            "revision": feedback.get("revision") or row.get("revision") or 1,
            "revised_by": feedback.get("revised_by") or row.get("revised_by"),
            "revised_at": feedback.get("revised_at") or row.get("revised_at"),
            "history": annotation.get("history") or [],
        }
        item["image_path_abs"] = self.resolve_path(row.get("image_path"))
        return item

    def resolve_path(self, value: str | None) -> str | None:
        """Resolve a feedback-root-relative path to an absolute file path."""
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = self.root / value
        return str(path.resolve()) if path.is_file() else None

    @staticmethod
    def _matches(item: dict[str, Any], query: str) -> bool:
        values = [
            item.get("analysis_id"),
            item.get("predicted_smiles"),
            item.get("corrected_smiles"),
            item.get("image_sha256"),
            item.get("source_reference"),
            (item.get("original_input") or {}).get("filename"),
        ]
        return any(query in str(value or "").lower() for value in values)
