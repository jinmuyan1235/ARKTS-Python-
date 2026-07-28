"""Tests for page truth editing, matching, snapshot safety, and reporting evidence."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.snapshot_page_dataset import snapshot
from src.datasets.page_annotations import PageAnnotationStore
from src.evaluation.page_proposals import bbox_iou, match_boxes, evaluate_page_proposals
from src.ui.page_annotation_page import _annotation_from_canvas_object, _canvas_object
from src.ui.drawable_canvas_compat import background_image_url


def _workspace(root: Path, *, completed: bool = False) -> Path:
    (root / "pages").mkdir(parents=True)
    Image.new("RGB", (300, 200), "white").save(root / "pages" / "doc_p001.png")
    page = {
        "page_id": "doc_p001", "source_document": "doc", "pmcid": "PMC1", "page_number": 1,
        "image_path": "pages/doc_p001.png", "width": 300, "height": 200,
        "annotation_status": "completed" if completed else "pending", "layout_tags": [],
        "annotations": [{"annotation_id": "a0001", "bbox": [40, 40, 180, 160], "class": "molecule"}] if completed else [],
        "annotator": "tester" if completed else "", "updated_at": "",
    }
    (root / "annotations.json").write_text(json.dumps({"schema_version": 1, "pages": {"doc_p001": page}}), encoding="utf-8")
    (root / "protocol.json").write_text(json.dumps({"config_sha256": "dataset-config-sha"}), encoding="utf-8")
    with (root / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["page_id", "source_document", "pmcid", "page_number", "image_path", "width", "height", "annotation_status", "layout_tags"])
        writer.writeheader(); writer.writerow({key: page.get(key, "") for key in writer.fieldnames})
    return root


def test_page_annotation_add_update_delete_and_save(tmp_path: Path) -> None:
    store = PageAnnotationStore(_workspace(tmp_path / "work"))
    boxes = store.add_box([], [10, 20, 80, 100], "molecule")
    boxes = store.update_box(boxes, 0, bbox=[11, 21, 90, 110], region_class="reaction")
    store.save_page("doc_p001", boxes, annotator="human", layout_tags=["reaction_scheme"])
    saved = store.page("doc_p001")
    assert saved["annotations"][0]["bbox"] == [11, 21, 90, 110]
    assert saved["annotations"][0]["class"] == "reaction"
    assert store.delete_box(saved["annotations"], 0) == []


def test_canvas_bbox_round_trip_preserves_class_and_original_coordinates() -> None:
    annotation = {"bbox": [100, 200, 500, 700], "class": "reaction"}
    canvas = _canvas_object(annotation, 0.5)
    assert _annotation_from_canvas_object(canvas, 0.5, "molecule") == annotation
    canvas["scaleX"] = 1.25
    resized = _annotation_from_canvas_object(canvas, 0.5, "molecule")
    assert resized == {"bbox": [100, 200, 600, 700], "class": "reaction"}


def test_canvas_background_uses_current_streamlit_layout_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_image_to_url(image, layout_config, *args):
        captured["size"] = image.size
        captured["width"] = layout_config.width
        return "/media/test.png"

    monkeypatch.setattr("src.ui.drawable_canvas_compat.image_to_url", fake_image_to_url)
    monkeypatch.setattr(
        "src.ui.drawable_canvas_compat.st._config.get_option", lambda _name: "",
    )
    assert background_image_url(Image.new("RGB", (12, 8), "white"), 12, "test") == "/media/test.png"
    assert captured == {"size": (12, 8), "width": 12}


def test_iou_and_one_to_many_many_to_one_statistics() -> None:
    assert bbox_iou([0, 0, 100, 100], [0, 0, 100, 100]) == 1.0
    merged = match_boxes([[0, 0, 80, 100], [80, 0, 160, 100]], [[0, 0, 160, 100]])
    assert merged["merged_region_errors"] == 1
    split = match_boxes([[0, 0, 160, 100]], [[0, 0, 80, 100], [80, 0, 160, 100]])
    assert split["split_truth_errors"] == 1


def test_page_snapshot_refuses_pending_and_overwrite(tmp_path: Path) -> None:
    pending = _workspace(tmp_path / "pending")
    with pytest.raises(ValueError, match="remaining=1"):
        snapshot(pending, tmp_path / "frozen", version="v1")
    completed = _workspace(tmp_path / "completed", completed=True)
    result = snapshot(completed, tmp_path / "frozen", version="v1")
    assert result["dataset_role"] == "page_holdout"
    assert (tmp_path / "frozen" / "checksums.sha256").is_file()
    with pytest.raises(FileExistsError, match="overwrite"):
        snapshot(completed, tmp_path / "frozen", version="v1")


def test_page_evaluator_writes_required_outputs_and_hashes(tmp_path: Path) -> None:
    dataset = _workspace(tmp_path / "dataset", completed=True)
    output = tmp_path / "evaluation"
    metrics = evaluate_page_proposals(dataset, output, proposal_config="baseline")
    required = {
        "metrics.json", "per_page_metrics.csv", "per_document_metrics.csv", "matches.csv",
        "missed_molecules.csv", "false_proposals.csv", "comparison_gallery", "report.md",
    }
    assert required == {path.name for path in output.iterdir()}
    assert metrics["proposal_config_sha256"]
    assert metrics["dataset_config_sha256"] == "dataset-config-sha"
