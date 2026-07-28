from __future__ import annotations

import sys
import time
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image

from src.analysis.batch_analyzer import BatchAnalyzer
from src.analysis.correction import reset_human_review
from src.analysis.molecule_report import MoleculeReportGenerator
from src.runtime.batch_job_store import BatchJobStore
from src.runtime.batch_inputs import extract_batch_uploads, inspect_batch_uploads
from src.runtime.job_manager import run_process
from src.runtime.job_registry import _retry_source_paths, load_batch_job_result, refresh_batch_job, start_batch_job
from src.runtime.batch_result_review import apply_batch_review_actions, persist_batch_result


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_batch_summary_uses_mutually_exclusive_status_counts() -> None:
    summary = BatchAnalyzer._summary(
        [
            {"status": "success", "valid": True, "recognition_decision": "accepted"},
            {"status": "success", "valid": True, "recognition_decision": "accepted_with_warning"},
            {"status": "success", "valid": True, "recognition_decision": "review_needed"},
            {"status": "success", "valid": False, "recognition_decision": "rejected", "message": "rejected input"},
            {"status": "failed", "valid": False, "recognition_decision": "rejected", "message": "bad input"},
            {"status": "skipped", "valid": False, "recognition_decision": "skipped", "message": "skipped"},
        ],
        total=6,
    )

    assert summary["accepted"] == 1
    assert summary["accepted_with_warning"] == 1
    assert summary["review_needed"] == 1
    assert summary["rejected"] == 1
    assert summary["failed"] == 1
    assert summary["skipped"] == 1
    assert summary["manual_review_total"] == 2
    assert (
        summary["accepted"]
        + summary["accepted_with_warning"]
        + summary["review_needed"]
        + summary["rejected"]
        + summary["failed"]
        + summary["skipped"]
    ) == summary["completed"]


def test_batch_job_store_persists_progress_and_control_flags(tmp_path: Path) -> None:
    store = BatchJobStore(tmp_path / "jobs")
    state = store.create(
        "job1",
        backend="demo",
        input_dir=tmp_path,
        output_dir=tmp_path / "out",
        total=3,
        source="test",
    )
    assert state["status"] == "queued"

    updated = store.mark_running("job1", 1234)
    assert updated["status"] == "running"
    assert updated["pid"] == 1234

    progress = store.update_progress("job1", {
        "status": "running",
        "total": 3,
        "completed": 3,
        "current_file": "compound_001.png",
        "summary": {
            "summary_schema_version": 2,
            "total": 3,
            "completed": 3,
            "accepted": 1,
            "accepted_with_warning": 1,
            "review_needed": 1,
            "manual_review_total": 2,
            "rejected": 0,
            "failed": 0,
            "skipped": 0,
        },
    })
    assert progress["completed"] == 3
    assert progress["current_file"] == "compound_001.png"
    assert progress["accepted"] == 1
    assert progress["accepted_with_warning"] == 1
    assert progress["review_needed"] == 1
    assert progress["manual_review_total"] == 2
    assert (
        progress["accepted"]
        + progress["accepted_with_warning"]
        + progress["review_needed"]
        + progress["rejected"]
        + progress["failed"]
        + progress["skipped"]
    ) == progress["completed"]

    skip_state = store.request_skip_current("job1")
    assert "下一张未开始文件" in skip_state["message"]
    assert store.consume_skip_request("job1") is True
    assert store.consume_skip_request("job1") is False

    cancelling = store.request_cancel("job1")
    assert cancelling["status"] == "cancelling"
    assert store.cancel_requested("job1") is True


def test_batch_job_store_reads_legacy_review_needed_as_manual_review_total(tmp_path: Path) -> None:
    store = BatchJobStore(tmp_path / "jobs")
    store.create(
        "legacy",
        backend="demo",
        input_dir=tmp_path,
        output_dir=tmp_path / "out",
        total=3,
        source="test",
    )

    state = store.update_progress("legacy", {
        "status": "running",
        "total": 3,
        "completed": 3,
        "summary": {
            "total": 3,
            "completed": 3,
            "accepted": 1,
            "accepted_with_warning": 1,
            "review_needed": 2,
            "rejected": 0,
            "failed": 0,
            "skipped": 0,
        },
    })

    assert state["accepted_with_warning"] == 1
    assert state["review_needed"] == 1
    assert state["manual_review_total"] == 2
    assert (
        state["accepted"]
        + state["accepted_with_warning"]
        + state["review_needed"]
        + state["rejected"]
        + state["failed"]
        + state["skipped"]
    ) == state["completed"]


def test_process_batch_writes_background_job_result(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    sample = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    (input_dir / "aspirin.png").write_bytes(sample.read_bytes())

    store = BatchJobStore(tmp_path / "jobs")
    output_dir = tmp_path / "out"
    job_id = "job_process"
    store.create(job_id, backend="demo", input_dir=input_dir, output_dir=output_dir, total=1)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "process_batch.py"),
        "--input",
        str(input_dir),
        "--backend",
        "demo",
        "--output",
        str(output_dir),
        "--job-id",
        job_id,
        "--job-store-dir",
        str(store.root),
    ]
    result = run_process(command, cwd=PROJECT_ROOT, timeout=60)

    assert result.returncode == 0, result.stderr or result.stdout
    state = store.read(job_id)
    assert state["status"] == "completed"
    assert state["completed"] == 1
    assert Path(state["result_path"]).is_file()
    payload = store.load_result(job_id)
    assert payload is not None
    assert payload["summary"]["total"] == 1
    assert payload["exports"]["csv"]


def test_job_registry_starts_and_recovers_batch_result(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    sample = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    (input_dir / "aspirin.png").write_bytes(sample.read_bytes())
    store = BatchJobStore(tmp_path / "jobs")

    state = start_batch_job(input_dir, "demo", {}, store=store, source="test")
    job_id = state["job_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        state = refresh_batch_job(job_id, store)
        if state["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.2)

    assert state["status"] == "completed", state
    result = load_batch_job_result(job_id, store)
    assert result is not None
    assert result["summary"]["completed"] == 1
    assert Path(result["exports"]["json"]).is_file()


def test_batch_job_store_persists_pause_and_resume(tmp_path: Path) -> None:
    store = BatchJobStore(tmp_path / "jobs")
    store.create("paused", backend="demo", input_dir=tmp_path, output_dir=tmp_path / "out", total=2)
    store.mark_running("paused", 1234)

    paused = store.request_pause("paused")
    assert paused["status"] == "paused"
    assert store.pause_requested("paused") is True
    resumed = store.resume("paused")
    assert resumed["status"] == "running"
    assert store.pause_requested("paused") is False


def test_batch_zip_validation_dedup_and_safe_extraction(tmp_path: Path) -> None:
    image_buffer = BytesIO()
    Image.new("RGB", (40, 30), "white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    archive_buffer = BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("nested/a.png", image_bytes)
        archive.writestr("nested/b.png", image_bytes)
        archive.writestr("notes.txt", "ignored")

    uploads = [("images.zip", archive_buffer.getvalue())]
    inspection = inspect_batch_uploads(uploads)
    assert inspection["valid_files"] == 2
    assert inspection["duplicate_files"] == 1
    paths, extracted = extract_batch_uploads(uploads, tmp_path / "input")
    assert len(paths) == 2
    assert all(path.is_file() for path in paths)
    assert extracted["duplicate_files"] == 1

    unsafe_buffer = BytesIO()
    with zipfile.ZipFile(unsafe_buffer, "w") as archive:
        archive.writestr("../escape.png", image_bytes)
    unsafe = inspect_batch_uploads([("unsafe.zip", unsafe_buffer.getvalue())])
    assert any("不安全路径" in message for message in unsafe["errors"])

    mixed = inspect_batch_uploads([("source_note.txt", b"not an image"), ("valid.png", image_bytes)])
    assert mixed["total_files"] == 2
    assert mixed["valid_files"] == 1
    assert any("不支持的图片格式" in message for message in mixed["errors"])


def test_batch_analyzer_reuses_hash_cache_and_checkpoint(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    image = Image.new("RGB", (32, 24), "white")
    image.save(input_dir / "a.png")
    image.save(input_dir / "b.png")
    calls = {"count": 0}
    analyzer = BatchAnalyzer("demo", tmp_path / "out1", cache_dir=tmp_path / "cache")

    def fake_generate(image_path: Path):
        calls["count"] += 1
        return {
            "analysis_id": f"generated-{calls['count']}",
            "status": "success",
            "message": "candidate",
            "input": {"type": "image", "filename": image_path.name, "path": str(image_path)},
            "ocsr": {"status": "success", "smiles": "CCO", "backend": "demo"},
            "final": {"smiles": "CCO", "source": "ocsr"},
            "validation": {"valid": True, "canonical_smiles": "CCO"},
            "human_review": {"required": True, "status": "unconfirmed", "confirmed": False},
            "recognition_decision": {"decision": "accepted", "manual_review_recommended": False},
            "images": {},
        }

    monkeypatch.setattr(analyzer.generator, "generate", fake_generate)
    first = analyzer.analyze_folder(input_dir, checkpoint_path=tmp_path / "checkpoint.json")
    assert calls["count"] == 1
    assert first["summary"]["cache_hits"] == 1
    assert first["summary"]["pending_confirmation"] == 2

    second = BatchAnalyzer("demo", tmp_path / "out2", cache_dir=tmp_path / "cache")

    def unexpected_generate(**_kwargs):
        raise AssertionError("cache miss")

    monkeypatch.setattr(second.generator, "generate", unexpected_generate)
    cached = second.analyze_folder(input_dir, checkpoint_path=tmp_path / "checkpoint2.json")
    assert cached["summary"]["cache_hits"] == 2

    resumed = second.analyze_folder(input_dir, checkpoint_path=tmp_path / "checkpoint.json")
    assert resumed["summary"]["completed"] == 2
    assert resumed["summary"]["cache_hits"] == 2


def test_selected_retry_only_returns_requested_report_paths(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (10, 10)).save(first)
    Image.new("RGB", (10, 10)).save(second)
    result = {
        "reports": [
            {"analysis_id": "a", "status": "success", "input": {"path": str(first)}},
            {"analysis_id": "b", "status": "success", "input": {"path": str(second)}},
        ]
    }

    assert _retry_source_paths(result, "selected", analysis_ids=["b"]) == [second]


def test_batch_review_persists_confirmation_correction_and_formal_exports(tmp_path: Path) -> None:
    report = MoleculeReportGenerator("manual", tmp_path / "report").generate(smiles="CCO", analysis_id="batch-review")
    report["input"].update({"type": "image", "filename": "ethanol.png"})
    report = reset_human_review(report)
    analyzer = BatchAnalyzer("demo", tmp_path / "summary")
    row = analyzer._summary([], total=1)
    batch = {
        "summary": row,
        "rows": [],
        "reports": [report],
        "exports": {},
    }

    corrected = apply_batch_review_actions(
        batch,
        [{"action": "correct_smiles", "analysis_id": "batch-review", "smiles": "OCC"}],
        tmp_path / "reviewed",
    )
    assert corrected["summary"]["pending_confirmation"] == 1
    assert Path(corrected["exports"]["merged_sdf"]).read_text(encoding="utf-8") == ""

    confirmed = apply_batch_review_actions(
        corrected,
        [{"action": "confirm", "analysis_id": "batch-review"}],
        tmp_path / "reviewed",
    )
    assert confirmed["summary"]["pending_confirmation"] == 0
    assert "$$$$" in Path(confirmed["exports"]["merged_sdf"]).read_text(encoding="utf-8")
    result_path = persist_batch_result(confirmed, tmp_path / "batch_ui_result.json")
    assert result_path.is_file()
