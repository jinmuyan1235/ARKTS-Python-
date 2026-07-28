"""Tests for persistent uploaded-image run lifecycle and feedback archival."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from PIL import Image

from src.analysis.correction import apply_smiles_correction, save_correction_feedback
from src.analysis.molecule_report import MoleculeReportGenerator
from src.runtime.run_store import (
    cleanup_runs,
    cleanup_runs_if_due,
    create_image_run_from_bytes,
    report_output_dir,
    save_report_for_existing_run,
    save_run_report,
    write_runtime_metadata,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_uploaded_image_run_persists_original_report_and_feedback(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    image_run = create_image_run_from_bytes(source.read_bytes(), "aspirin.png", runs_root=tmp_path / "runs")

    report = MoleculeReportGenerator("demo", image_run.run_dir).generate(
        image_path=image_run.input_path,
        analysis_id=image_run.analysis_id,
    )
    save_run_report(report, image_run)

    assert report["status"] == "success"
    assert report["analysis_id"] == image_run.analysis_id
    assert Path(report["input"]["path"]).is_file()
    assert Path(report["input"]["path"]) == image_run.input_path
    assert Path(report["run"]["report_path"]).is_file()
    assert (image_run.run_dir / "preprocessing").is_dir()
    assert (image_run.run_dir / "structures").is_dir()

    reloaded = json.loads(image_run.report_path.read_text(encoding="utf-8"))
    corrected = apply_smiles_correction(reloaded, "CCO", report_output_dir(reloaded))
    save_report_for_existing_run(corrected)
    corrected_reloaded = json.loads(image_run.report_path.read_text(encoding="utf-8"))
    assert corrected_reloaded["correction"]["applied"] is True
    assert Path(corrected_reloaded["images"]["corrected_molecule"]).is_file()

    feedback = save_correction_feedback(
        corrected_reloaded,
        tmp_path,
        correction_type="atom",
        review_status="verified",
        feedback_action="accepted_for_dataset",
        include_in_training=True,
    )
    assert Path(feedback["image_path"]).is_file()
    with Image.open(feedback["image_path"]) as archived:
        assert archived.width > 0 and archived.height > 0

    runtime = json.loads(image_run.runtime_path.read_text(encoding="utf-8"))
    assert runtime["protected"] is True
    cleanup = cleanup_runs(tmp_path / "runs", retention_days=1, max_storage_gb=0.000001)
    assert cleanup["deleted_count"] == 0
    assert image_run.input_path.is_file()


def test_cleanup_runs_if_due_uses_interval_marker(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    state_path = tmp_path / "state" / "run_cleanup.json"
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    old_created_at = (now - timedelta(days=5)).isoformat()

    old_run = create_image_run_from_bytes(b"old-image", "old.png", runs_root=runs_root, analysis_id="old-run")
    write_runtime_metadata(old_run, {"created_at": old_created_at})

    first = cleanup_runs_if_due(
        runs_root,
        retention_days=1,
        max_storage_gb=10,
        state_path=state_path,
        now=now,
    )
    assert first["status"] == "completed"
    assert first["deleted_count"] == 1
    assert not old_run.run_dir.exists()
    assert state_path.is_file()

    second_old_run = create_image_run_from_bytes(
        b"another-old-image",
        "old2.png",
        runs_root=runs_root,
        analysis_id="old-run-2",
    )
    write_runtime_metadata(second_old_run, {"created_at": old_created_at})

    second = cleanup_runs_if_due(
        runs_root,
        retention_days=1,
        max_storage_gb=10,
        state_path=state_path,
        now=now + timedelta(hours=1),
    )
    assert second["status"] == "skipped"
    assert second["reason"] == "not_due"
    assert second_old_run.run_dir.exists()
