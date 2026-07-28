from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.analysis.correction import apply_smiles_correction, save_correction_feedback
from src.analysis.molecule_report import MoleculeReportGenerator
from src.feedback.review_service import FeedbackReviewService
from src.feedback.store import read_feedback_manifest, update_feedback_review


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _corrected_report(tmp_path: Path, analysis_id: str, corrected_smiles: str = "CCN") -> dict:
    sample = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    report = MoleculeReportGenerator("manual", tmp_path).generate(smiles="CCO", analysis_id=analysis_id)
    report["input"].update({
        "type": "image",
        "filename": f"{analysis_id}.png",
        "path": str(sample),
        "image_sha256": f"sha-{analysis_id}",
    })
    return apply_smiles_correction(report, corrected_smiles, tmp_path)


def _save_pending(tmp_path: Path, analysis_id: str, corrected_smiles: str = "CCN") -> dict:
    report = _corrected_report(tmp_path, analysis_id, corrected_smiles)
    return save_correction_feedback(
        report,
        tmp_path,
        correction_type="atom",
        review_status="pending",
        feedback_action="correction_only",
        include_in_training=False,
        source_reference="unit-test",
        source_license="internal",
        notes="needs review",
    )


def test_pending_feedback_requires_review_before_training_export(tmp_path: Path) -> None:
    _save_pending(tmp_path, "review001")
    service = FeedbackReviewService(tmp_path)

    pending = service.list_items("pending")
    assert [item["analysis_id"] for item in pending] == ["review001"]
    assert pending[0]["corrected_smiles"] == "CCN"
    assert pending[0]["predicted_smiles"] == "CCO"

    manifest_path = tmp_path / "verified_manifest.csv"
    before = service.export_verified_manifest(manifest_path)
    assert before["exported_count"] == 0

    approved = service.approve_for_dataset("review001", "looks correct", reviewer="chemist-a")
    assert approved["review_status"] == "verified"
    assert approved["include_in_training"] is True
    assert approved["reviewer"] == "chemist-a"
    assert approved["reviewed_at"]
    assert datetime.fromisoformat(str(approved["reviewed_at"])).tzinfo is not None
    assert approved["revision"] == 1

    rows = read_feedback_manifest(tmp_path / "feedback")
    assert rows[0]["review_status"] == "verified"
    assert rows[0]["feedback_action"] == "accepted_for_dataset"
    assert rows[0]["include_in_training"] == "True"
    assert rows[0]["reviewer"] == "chemist-a"
    assert rows[0]["reviewed_at"]
    assert rows[0]["revision"] == "1"
    annotation = json.loads(Path(tmp_path / "feedback" / rows[0]["annotation_path"]).read_text(encoding="utf-8"))
    assert annotation["feedback"]["reviewer_notes"] == "looks correct"
    assert annotation["feedback"]["reviewer"] == "chemist-a"
    assert annotation["history"][-1]["operation"] == "review_verified"
    assert annotation["history"][-1]["old_status"] == "pending"
    assert annotation["history"][-1]["new_status"] == "verified"
    assert annotation["history"][-1]["reviewer"] == "chemist-a"
    assert annotation["history"][-1]["reviewer_notes"] == "looks correct"

    after = service.export_verified_manifest(manifest_path)
    assert after["exported_count"] == 1


def test_review_service_non_approval_actions_do_not_enter_training(tmp_path: Path) -> None:
    _save_pending(tmp_path, "return001", "CCN")
    _save_pending(tmp_path, "reject001", "CCC")
    _save_pending(tmp_path, "dupe001", "CCCl")
    _save_pending(tmp_path, "license001", "CCBr")
    service = FeedbackReviewService(tmp_path)

    assert service.return_for_revision("return001", "fix source", reviewer="reviewer-a")["review_status"] == "returned"
    assert service.reject_sample("reject001", "not a molecule", reviewer="reviewer-a")["review_status"] == "rejected"
    assert service.mark_duplicate("dupe001", duplicate_of="return001", reviewer="reviewer-a")["review_status"] == "duplicate"
    assert service.mark_license_unclear("license001", reviewer_notes="check license", reviewer="reviewer-a")["review_status"] == "license_unclear"

    all_items = {item["analysis_id"]: item for item in service.list_items("all")}
    assert all_items["dupe001"]["feedback"]["duplicate_of"] == "return001"
    assert all_items["license001"]["feedback"]["source_license"] == "unclear"
    assert service.export_verified_manifest(tmp_path / "verified_manifest.csv")["exported_count"] == 0


def test_review_actions_require_non_empty_reviewer_even_without_ui(tmp_path: Path) -> None:
    actions = [
        ("approve001", lambda service: service.approve_for_dataset("approve001", "ok", reviewer=" ")),
        ("return001", lambda service: service.return_for_revision("return001", "fix", reviewer="")),
        ("reject001", lambda service: service.reject_sample("reject001", "bad", reviewer=" \t")),
        ("dupe001", lambda service: service.mark_duplicate("dupe001", duplicate_of="approve001", reviewer="")),
        ("license001", lambda service: service.mark_license_unclear("license001", reviewer_notes="license", reviewer=" ")),
    ]
    for analysis_id, _action in actions:
        _save_pending(tmp_path, analysis_id, "CCN")
    service = FeedbackReviewService(tmp_path)

    for _analysis_id, action in actions:
        with pytest.raises(ValueError, match="reviewer"):
            action(service)

    assert {item["analysis_id"]: item["review_status"] for item in service.list_items("all")} == {
        "approve001": "pending",
        "return001": "pending",
        "reject001": "pending",
        "dupe001": "pending",
        "license001": "pending",
    }


def test_store_rejects_blank_reviewer_when_service_is_bypassed(tmp_path: Path) -> None:
    _save_pending(tmp_path, "store001")

    with pytest.raises(ValueError, match="reviewer"):
        update_feedback_review(
            tmp_path / "feedback",
            "store001",
            "verified",
            feedback_action="accepted_for_dataset",
            include_in_training=True,
            reviewer="   ",
        )

    rows = read_feedback_manifest(tmp_path / "feedback")
    assert rows[0]["review_status"] == "pending"
    assert rows[0]["reviewer"] == ""


def test_returned_feedback_can_be_revised_and_resubmitted(tmp_path: Path) -> None:
    _save_pending(tmp_path, "revise001", "CCN")
    service = FeedbackReviewService(tmp_path)

    returned = service.return_for_revision("revise001", "wrong atom count", reviewer="reviewer-a")
    assert returned["review_status"] == "returned"

    revised = service.revise_and_resubmit("revise001", "CCCO", revised_by="chemist-b", notes="fixed chain length")
    assert revised["review_status"] == "pending"
    assert revised["include_in_training"] is False
    assert revised["revision"] == 2
    assert revised["corrected_smiles"] == "CCCO"

    item = service.get_item("revise001")
    assert item is not None
    assert item["review_status"] == "pending"
    assert item["corrected_smiles"] == "CCCO"
    assert item["revision"] == 2
    assert item["revised_by"] == "chemist-b"

    rows = read_feedback_manifest(tmp_path / "feedback")
    row = next(row for row in rows if row["analysis_id"] == "revise001")
    annotation = json.loads(Path(tmp_path / "feedback" / row["annotation_path"]).read_text(encoding="utf-8"))
    assert annotation["feedback"]["revision"] == 2
    assert annotation["feedback"]["revised_by"] == "chemist-b"
    assert annotation["feedback"]["review_status"] == "pending"
    assert annotation["revision_history"][0]["correction"]["corrected_smiles"] == "CCN"
    assert annotation["history"][-1]["operation"] == "revise_and_resubmit"
    assert annotation["history"][-1]["old_status"] == "returned"
    assert annotation["history"][-1]["new_status"] == "pending"
    assert annotation["history"][-1]["revised_by"] == "chemist-b"


def test_revision_requires_revised_by_and_verified_revision_exits_training(tmp_path: Path) -> None:
    _save_pending(tmp_path, "verified-revise001", "CCN")
    service = FeedbackReviewService(tmp_path)
    service.approve_for_dataset("verified-revise001", "good", reviewer="reviewer-a")
    assert service.export_verified_manifest(tmp_path / "verified_manifest.csv")["exported_count"] == 1

    with pytest.raises(ValueError, match="revised_by"):
        service.revise_and_resubmit("verified-revise001", "CCCO", revised_by=" ", notes="changed")

    revised = service.revise_and_resubmit("verified-revise001", "CCCO", revised_by="chemist-b", notes="changed")
    assert revised["review_status"] == "pending"
    assert revised["include_in_training"] is False

    item = service.get_item("verified-revise001")
    assert item is not None
    assert item["review_status"] == "pending"
    assert item["reviewer"] == ""
    assert item["reviewed_at"] in {"", None}
    assert item["revised_by"] == "chemist-b"
    assert service.export_verified_manifest(tmp_path / "verified_manifest_after_revision.csv")["exported_count"] == 0
