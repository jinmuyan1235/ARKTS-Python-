"""Tests for visual-only OCSR review and trusted structure confirmation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src.datasets.solo_review import SoloReviewStore
from src.ui.dataset_review_page import _batch_thumbnail, _model_failures


QUEUE_FIELDS = (
    "sample_id", "verification_status", "dataset_root", "image_path", "source_page_path", "bbox", "machine_category", "category",
    "source_kind", "source_id", "source_document", "source_license", "source_url", "attribution", "expected_action", "split",
    "ground_truth_origin", "ground_truth_smiles", "ground_truth_inchikey", "source_compound_id", "source_structure_file",
    "source_canonical_smiles", "source_inchikey", "molscribe_smiles", "decimer_smiles", "ensemble_smiles",
    "molscribe_inchikey", "decimer_inchikey", "ensemble_inchikey", "molscribe_raw", "decimer_raw", "ensemble_raw",
    "risk_reasons", "deterministic_errors", "image_quality_score", "image_quality_level",
)


def _page(path: Path) -> None:
    image = Image.new("RGB", (180, 140), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 100, 100), outline="black", width=4)
    draw.line((20, 60, 100, 60), fill="black", width=3)
    image.save(path)


def _queue_row(sample_id: str, image_name: str, *, trusted: bool = False) -> dict[str, str]:
    raw = json.dumps({"status": "success", "smiles": "CCO"})
    row = {
        "sample_id": sample_id, "verification_status": "pending_human_review", "dataset_root": "",
        "image_path": f"candidates/{image_name}", "source_page_path": "document_runs/page.png", "bbox": "[20, 20, 100, 100]",
        "machine_category": "molecule", "category": "molecule", "source_kind": "pmc_oa", "source_id": "PMC-test",
        "source_document": "PMC-test", "source_license": "cc-by-4.0", "source_url": "https://example.test/source",
        "attribution": "Example attribution", "expected_action": "recognize", "split": "train",
        "ground_truth_origin": "", "ground_truth_smiles": "", "ground_truth_inchikey": "", "source_compound_id": "",
        "source_structure_file": "", "source_canonical_smiles": "", "source_inchikey": "",
        "molscribe_smiles": "CCO", "decimer_smiles": "CCO", "ensemble_smiles": "CCO",
        "molscribe_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "decimer_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "ensemble_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "molscribe_raw": raw, "decimer_raw": raw, "ensemble_raw": raw,
        "risk_reasons": "[\"model_disagreement\"]", "deterministic_errors": "[]", "image_quality_score": "0.77", "image_quality_level": "medium",
    }
    if trusted:
        row.update({
            "source_kind": "pubchem", "source_id": "123", "ground_truth_origin": "pubchem", "ground_truth_smiles": "CCO",
            "ground_truth_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "source_compound_id": "123", "source_structure_file": "records/123.sdf",
        })
    return row


def _setup(tmp_path: Path, sample_ids: tuple[str, ...] = ("sample-1",), *, trusted: bool = False) -> tuple[Path, Path, SoloReviewStore]:
    default_root = tmp_path / "default-root"
    dataset = tmp_path / "dataset"
    review = tmp_path / "review"
    (dataset / "candidates").mkdir(parents=True)
    (dataset / "document_runs").mkdir(parents=True)
    _page(dataset / "document_runs" / "page.png")
    rows = []
    for index, sample_id in enumerate(sample_ids):
        image = dataset / "candidates" / f"crop-{index}.png"
        _page(image)
        row = _queue_row(sample_id, image.name, trusted=trusted)
        row["dataset_root"] = str(dataset)
        rows.append(row)
    review.mkdir()
    with (review / "machine_review_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return dataset, review, SoloReviewStore(default_root, review_root=review)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_visual_review_does_not_require_smiles_and_routes_to_chemistry_required(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path)

    result = store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop", bbox_after=[30, 25, 110, 105], region_type="molecule", review_notes="Clear crop.", reviewer="developer")

    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["visual_review_status"] == "valid_single_molecule_crop"
    assert audit["final_smiles"] == ""
    assert set(audit["correction_types"]) == {"bbox"}
    assert Path(result.image_path).is_file()
    assert _read_rows(review / "visual_verified.csv")[0]["sample_id"] == "sample-1"
    assert _read_rows(review / "detector_training_manifest.csv")[0]["expected_action"] == "recognize"
    assert _read_rows(review / "chemistry_review_required.csv")[0]["ground_truth_smiles"] == ""
    assert _read_rows(review / "structure_ground_truth_verified.csv") == []


def test_trusted_external_ground_truth_can_be_confirmed_after_visual_review(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path, trusted=True)
    item = store.get_item("sample-1")
    assert item and item["trusted_ground_truth_available"] is True
    assert item["molscribe_matches_ground_truth"] is True

    store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop", region_type="molecule")
    result = store.submit_structure_ground_truth("sample-1", reviewer="developer")

    assert result.verification_status == "structure_ground_truth_verified"
    verified = _read_rows(review / "structure_ground_truth_verified.csv")
    assert verified[0]["ground_truth_origin"] == "pubchem"
    assert verified[0]["ground_truth_canonical_smiles"] == "CCO"
    assert verified[0]["source_compound_id"] == "123"


def test_visual_rejection_revokes_prior_structure_confirmation(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path, trusted=True)
    store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop", region_type="molecule")
    store.submit_structure_ground_truth("sample-1")

    store.submit_visual("sample-1", visual_review_status="invalid_crop", region_type="invalid_crop")

    assert _read_rows(review / "structure_ground_truth_verified.csv") == []
    assert _read_rows(review / "visual_rejected.csv")[0]["sample_id"] == "sample-1"
    assert _read_rows(review / "detector_training_manifest.csv")[0]["expected_action"] == "reject"


def test_structure_ground_truth_is_blocked_for_model_only_labels(tmp_path: Path) -> None:
    _, _, store = _setup(tmp_path)
    store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop", region_type="molecule")
    with pytest.raises(ValueError, match="No trusted external ground truth"):
        store.submit_structure_ground_truth("sample-1")


def test_missing_source_files_require_missing_status_and_record_output(tmp_path: Path) -> None:
    dataset, review, store = _setup(tmp_path, trusted=True)
    (dataset / "document_runs" / "page.png").unlink()
    item = store.get_item("sample-1")
    assert item and item["files_complete"] is False
    assert "source_page_path" in item["missing_source_files"]
    with pytest.raises(ValueError, match="missing_source_file"):
        store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop")

    store.submit_visual("sample-1", visual_review_status="missing_source_file")
    missing = _read_rows(review / "missing_files.csv")
    assert missing[0]["sample_id"] == "sample-1"
    with pytest.raises(ValueError, match="source page or crop"):
        store.submit_structure_ground_truth("sample-1")


def test_machine_manifest_is_primary_and_explicit_dataset_root_resolves_document_runs_and_candidates(tmp_path: Path) -> None:
    dataset, _, store = _setup(tmp_path, ("human", "verified", "machine", "invalid"))
    rows = _read_rows(store.machine_manifest_path)
    rows[1]["verification_status"] = "machine_verified"
    rows[2]["verification_status"] = "pending_machine_review"
    rows[2]["deterministic_errors"] = "[\"model_backend_unavailable\"]"
    rows[2]["molscribe_raw"] = json.dumps({"status": "failed", "message": "model package missing"})
    rows[3]["verification_status"] = "rejected_invalid"
    with store.machine_manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    human = store.list_items()
    assert [item["sample_id"] for item in human] == ["human"]
    assert human[0]["page_path_abs"] == str((dataset / "document_runs" / "page.png").resolve())
    assert human[0]["crop_path_abs"] == str((dataset / "candidates" / "crop-0.png").resolve())
    pending_machine = store.list_items(scope="pending_machine_review")
    assert _model_failures(pending_machine[0]) == [{"backend": "molscribe", "status": "failed", "message": "model package missing"}]
    assert [item["sample_id"] for item in store.list_items(scope="all_reviewable")] == ["human", "machine", "verified"]
    assert [item["sample_id"] for item in store.list_items(scope="machine_rejected")] == ["invalid"]
    assert store.get_item("invalid") is not None


def test_lightweight_id_listing_does_not_enrich_every_queue_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store = _setup(tmp_path, ("first", "second", "third"))

    def unexpected_enrichment(*_: object, **__: object) -> dict[str, object]:
        raise AssertionError("list_item_ids must not enrich queue rows")

    monkeypatch.setattr(store, "_enrich_row", unexpected_enrichment)

    assert store.list_item_ids(scope="pending_human_review") == ["first", "second", "third"]


def test_get_item_enriches_only_the_selected_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store = _setup(tmp_path, ("first", "second", "third"))
    original = store._enrich_row
    calls: list[str] = []

    def track(row: dict[str, str], audit: dict[str, object]) -> dict[str, object]:
        calls.append(row["sample_id"])
        return original(row, audit)

    monkeypatch.setattr(store, "_enrich_row", track)

    assert store.get_item("second")["sample_id"] == "second"
    assert calls == ["second"]


def test_batch_visual_classification_writes_all_audits_and_exports_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, review, store = _setup(tmp_path, ("first", "second", "third"))
    original_export = store.export_outcomes
    export_calls = 0

    def track_export() -> dict[str, Path]:
        nonlocal export_calls
        export_calls += 1
        return original_export()

    monkeypatch.setattr(store, "export_outcomes", track_export)

    result = store.submit_visual_batch(
        ["first", "second", "third"],
        visual_review_status="text",
        region_type="text",
        reviewer="batch-reviewer",
        review_notes="Same text class.",
    )

    assert result["reviewed_count"] == 3
    assert export_calls == 1
    rows = _read_rows(review / "detector_training_manifest.csv")
    assert {row["sample_id"] for row in rows} == {"first", "second", "third"}
    assert {row["expected_action"] for row in rows} == {"reject"}
    assert {row["category"] for row in rows} == {"text"}


def test_batch_visual_classification_requires_selection(tmp_path: Path) -> None:
    _, _, store = _setup(tmp_path)

    with pytest.raises(ValueError, match="Select at least one"):
        store.submit_visual_batch([], visual_review_status="text", region_type="text")


def test_reviewed_summaries_can_be_filtered_and_batch_corrected(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path, ("wrong-a", "wrong-b", "correct"))
    store.submit_visual_batch(
        ["wrong-a", "wrong-b"],
        visual_review_status="valid_single_molecule_crop",
        region_type="molecule",
        reviewer="first-pass",
    )
    store.submit_visual("correct", visual_review_status="text", region_type="text")

    reviewed = store.list_item_summaries(scope="all_samples", include_reviewed=True)
    wrong_ids = {
        row["sample_id"] for row in reviewed
        if row["visual_review_status"] == "valid_single_molecule_crop"
    }
    assert wrong_ids == {"wrong-a", "wrong-b"}

    store.submit_visual_batch(
        sorted(wrong_ids),
        visual_review_status="text",
        region_type="text",
        reviewer="correction-pass",
        review_notes="Correct accidental molecule labels.",
    )

    corrected = SoloReviewStore(store.dataset_root, review_root=review)
    assert corrected.queue_stats()["reviewed"] == 3
    assert {
        row["visual_review_status"]
        for row in corrected.list_item_summaries(scope="all_samples", include_reviewed=True)
    } == {"text"}
    assert _read_rows(review / "visual_verified.csv") == []
    assert len(_read_rows(review / "visual_rejected.csv")) == 3


def test_visual_remaining_counts_only_machine_routed_samples_without_audit(tmp_path: Path) -> None:
    _, _, store = _setup(tmp_path, ("first", "second", "third"))

    before = store.queue_stats()
    assert before["machine_routed_visual"] == 3
    assert before["visual_remaining"] == 3
    assert before["reviewed"] == 0

    store.submit_visual_batch(
        ["first", "second", "third"], visual_review_status="text", region_type="text",
    )
    after = store.queue_stats()
    assert after["machine_routed_visual"] == 3
    assert after["visual_remaining"] == 0
    assert after["reviewed"] == 3


def test_strict_dataset_root_does_not_search_legacy_collection_fallbacks(tmp_path: Path) -> None:
    store = SoloReviewStore(
        tmp_path / "holdout_collection",
        review_root=tmp_path / "review_holdout",
        strict_dataset_root=True,
    )
    roots = store._candidate_roots(None)
    assert roots == [(tmp_path / "holdout_collection").resolve()]


def test_machine_rejected_samples_are_available_to_batch_review(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path, ("rejected-a", "rejected-b"))
    rows = _read_rows(store.machine_manifest_path)
    for row in rows:
        row["verification_status"] = "rejected_invalid"
    with store.machine_manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    fresh = SoloReviewStore(store.dataset_root, review_root=review)

    summaries = fresh.list_item_summaries(scope="machine_rejected", include_reviewed=False)
    assert {row["sample_id"] for row in summaries} == {"rejected-a", "rejected-b"}
    result = fresh.submit_visual_batch(
        ["rejected-a", "rejected-b"], visual_review_status="text", region_type="text",
    )
    assert result["reviewed_count"] == 2


def test_batch_thumbnail_uses_fixed_canvas_for_different_aspect_ratios(tmp_path: Path) -> None:
    wide = tmp_path / "wide.png"
    tall = tmp_path / "tall.png"
    Image.new("RGB", (600, 80), "white").save(wide)
    Image.new("RGB", (80, 600), "white").save(tall)

    wide_thumbnail = _batch_thumbnail(str(wide), wide.stat().st_mtime_ns)
    tall_thumbnail = _batch_thumbnail(str(tall), tall.stat().st_mtime_ns)

    assert wide_thumbnail.size == (480, 300)
    assert tall_thumbnail.size == (480, 300)


def test_delayed_recheck_hides_first_visual_decision_and_compares_visual_results(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path)
    store.submit_visual("sample-1", visual_review_status="valid_single_molecule_crop", region_type="molecule", review_notes="First visual review.")

    selected = store.create_recheck_queue(1.0, seed=7)
    recheck_items = store.list_recheck_items()
    assert selected["selected"] == 1
    assert recheck_items[0]["first_review_hidden"] is True
    assert recheck_items[0]["audit"] == {}
    store.submit_recheck("sample-1", visual_review_status="valid_single_molecule_crop", bbox_after=[20, 20, 100, 100], region_type="molecule")
    report = json.loads((review / "review_consistency_report.json").read_text(encoding="utf-8"))
    assert report["completed_rechecks"] == 1
    assert report["consistency_rate"] == 1.0
    assert report["by_visual_review_status"]["valid_single_molecule_crop"]["consistency_rate"] == 1.0


def test_recheck_queue_extends_to_new_total_without_resampling_existing_items(tmp_path: Path) -> None:
    sample_ids = tuple(f"sample-{index}" for index in range(10))
    _, _, store = _setup(tmp_path, sample_ids)
    store.submit_visual_batch(
        sample_ids[:4], visual_review_status="text", region_type="text",
    )
    first = store.create_recheck_queue(0.5, seed=3)
    first_ids = {row["sample_id"] for row in _read_rows(store.recheck_path)}
    assert first["selected"] == 2
    assert len(first_ids) == 2

    store.submit_visual_batch(
        sample_ids[4:], visual_review_status="text", region_type="text",
    )
    extended = store.create_recheck_queue(0.5, seed=3)
    final_ids = {row["sample_id"] for row in _read_rows(store.recheck_path)}
    assert extended["selected"] == 3
    assert len(final_ids) == 5
    assert first_ids.issubset(final_ids)


def test_delayed_recheck_stratifies_all_visual_classes_and_supports_batch(tmp_path: Path) -> None:
    _, review, store = _setup(tmp_path, ("molecule", "text-a", "text-b", "reaction"))
    store.submit_visual("molecule", visual_review_status="valid_single_molecule_crop", region_type="molecule")
    store.submit_visual("text-a", visual_review_status="text", region_type="text")
    store.submit_visual("text-b", visual_review_status="text", region_type="text")
    store.submit_visual("reaction", visual_review_status="reaction", region_type="reaction")

    selected = store.create_recheck_queue(1.0, seed=11, max_samples=3)
    assert selected["selected"] == 3
    assert selected["selected_by_visual_status"] == {
        "reaction": 1, "text": 1, "valid_single_molecule_crop": 1,
    }
    pending_ids = [str(item["sample_id"]) for item in store.list_recheck_items()]
    result = store.submit_recheck_batch(
        pending_ids, visual_review_status="text", region_type="text", review_notes="Independent batch.",
    )
    assert result["reviewed_count"] == 3
    report = json.loads((review / "review_consistency_report.json").read_text(encoding="utf-8"))
    assert set(report["by_visual_review_status"]) == {
        "reaction", "text", "valid_single_molecule_crop",
    }
    assert report["completed_rechecks"] == 3
