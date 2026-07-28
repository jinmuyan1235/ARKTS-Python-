from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.molecule_report import MoleculeReportGenerator
from src.runtime.run_store import (
    cleanup_runs,
    create_image_run_from_bytes,
    mark_run_protected_from_report,
    save_run_report,
    write_runtime_metadata,
)
from src.storage.analysis_repository import (
    ARTIFACT_STATUS_EXPIRED,
    ARTIFACT_STATUS_MISSING,
    AnalysisRepository,
    record_report,
    record_result_payload,
)


def _patch_delete_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.storage.analysis_repository as module

    monkeypatch.setattr(module.config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(module.config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(module.config, "DOCUMENT_OUTPUT_DIR", tmp_path / "documents")


def _report(tmp_path: Path, smiles: str, analysis_id: str, filename: str) -> dict:
    report = MoleculeReportGenerator("manual", tmp_path).generate(smiles=smiles, analysis_id=analysis_id)
    report["input"].update({"type": "image", "filename": filename, "path": str(tmp_path / filename), "image_sha256": f"sha-{analysis_id}"})
    report["ocsr"]["backend"] = "demo"
    report["recognition_decision"] = {"decision": "accepted", "manual_review_recommended": False}
    return report


def test_repository_saves_searches_filters_and_favorites(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    repository = AnalysisRepository(db_path)
    aspirin = _report(tmp_path, "CC(=O)Oc1ccccc1C(=O)O", "aspirin001", "aspirin.png")
    review = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    review["recognition_decision"] = {"decision": "review_needed", "manual_review_recommended": True}
    aspirin_path = tmp_path / "aspirin_report.json"
    review_path = tmp_path / "review_report.json"
    aspirin_path.write_text(json.dumps(aspirin, ensure_ascii=False), encoding="utf-8")
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(aspirin, aspirin_path)
    repository.save_analysis(review, review_path)

    assert [row["analysis_id"] for row in repository.list_analyses(query="aspirin")] == ["aspirin001"]
    assert [row["analysis_id"] for row in repository.list_analyses(query=aspirin["chemical_identity"]["inchikey"])] == ["aspirin001"]
    assert [row["analysis_id"] for row in repository.list_analyses(status_filter="review_needed")] == ["ethanol001"]

    repository.set_favorite("aspirin001", True)
    favorite = repository.get_analysis("aspirin001")
    assert favorite is not None
    assert favorite["is_favorite"] is True
    assert repository.list_analyses(favorites_only=True)[0]["analysis_id"] == "aspirin001"

    csv_text = repository.export_rows_csv(repository.list_analyses())
    assert "analysis_id,created_at" in csv_text
    assert "aspirin001" in csv_text

    repository.delete_analysis("aspirin001")
    assert repository.get_analysis("aspirin001") is None
    assert aspirin_path.exists()


def test_repository_delete_analysis_and_files_removes_owned_run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    image_run = create_image_run_from_bytes(
        b"owned-image",
        "owned.png",
        runs_root=tmp_path / "runs",
        analysis_id="owned001",
    )
    report = MoleculeReportGenerator("manual", image_run.run_dir).generate(
        smiles="CCO",
        analysis_id=image_run.analysis_id,
    )
    report_path = save_run_report(report, image_run)
    repository.save_analysis(report, report_path)

    result = repository.delete_analysis_and_files(image_run.analysis_id)

    assert repository.get_analysis(image_run.analysis_id) is None
    assert not image_run.run_dir.exists()
    assert str(image_run.run_dir.resolve()) in result["deleted_paths"]
    assert result["errors"] == []


def test_repository_delete_analysis_and_files_removes_direct_report_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    report_path = tmp_path / "outputs" / "ethanol_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(report, report_path)

    result = repository.delete_analysis_and_files("ethanol001")

    assert repository.get_analysis("ethanol001") is None
    assert not report_path.exists()
    assert str(report_path.resolve()) in result["deleted_paths"]


def test_repository_delete_analysis_and_files_keeps_shared_batch_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    first = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    second = _report(tmp_path, "c1ccccc1", "benzene001", "benzene.png")
    payload_path = tmp_path / "outputs" / "batch_payload.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps({"reports": [first, second]}, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(first, payload_path)
    repository.save_analysis(second, payload_path)

    result = repository.delete_analysis_and_files("ethanol001")

    assert repository.get_analysis("ethanol001") is None
    assert repository.get_analysis("benzene001") is not None
    assert payload_path.exists()
    assert result["deleted_paths"] == []


def test_delete_permission_failure_retains_history_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    image_run = create_image_run_from_bytes(
        b"locked-image",
        "locked.png",
        runs_root=tmp_path / "runs",
        analysis_id="locked001",
    )
    report = MoleculeReportGenerator("manual", image_run.run_dir).generate(smiles="CCO", analysis_id=image_run.analysis_id)
    report_path = save_run_report(report, image_run)
    repository.save_analysis(report, report_path)

    import src.storage.analysis_repository as module

    def deny_delete(path: Path) -> None:
        if path.resolve() == image_run.run_dir.resolve():
            raise PermissionError("permission denied")
        module.shutil.rmtree(path) if path.is_dir() else path.unlink()

    monkeypatch.setattr(module, "_delete_owned_path", deny_delete)

    result = repository.delete_analysis_and_files(image_run.analysis_id)
    row = repository.get_analysis(image_run.analysis_id)

    assert row is not None
    assert row["delete_status"] == "delete_failed"
    assert image_run.run_dir.exists()
    assert result["record_retained"] is True
    assert result["errors"][0]["exception"] == "PermissionError"
    saved_error = json.loads(row["delete_errors"])
    assert saved_error["errors"][0]["path"] == str(image_run.run_dir.resolve())


def test_partial_delete_failure_retains_row_and_retry_deletes_residual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    locked_asset = output_dir / "ethanol001_locked.png"
    locked_asset.write_bytes(b"asset")
    report["images"]["redrawn_molecule"] = str(locked_asset)
    report_path = output_dir / "ethanol_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(report, report_path)

    import src.storage.analysis_repository as module

    original_delete = module._delete_owned_path
    fail_once = {"enabled": True}

    def occupied_once(path: Path) -> None:
        if path.resolve() == locked_asset.resolve() and fail_once["enabled"]:
            fail_once["enabled"] = False
            raise OSError("file is occupied")
        original_delete(path)

    monkeypatch.setattr(module, "_delete_owned_path", occupied_once)

    first = repository.delete_analysis_and_files("ethanol001")

    assert repository.get_analysis("ethanol001") is not None
    assert not report_path.exists()
    assert locked_asset.exists()
    assert str(report_path.resolve()) in first["deleted_paths"]
    assert first["errors"][0]["path"] == str(locked_asset.resolve())

    second = repository.delete_analysis_and_files("ethanol001")

    assert repository.get_analysis("ethanol001") is None
    assert not locked_asset.exists()
    assert str(locked_asset.resolve()) in second["deleted_paths"]


def test_delete_rejects_symlink_that_escapes_managed_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    output_dir = tmp_path / "outputs"
    external_dir = tmp_path / "external"
    output_dir.mkdir(parents=True, exist_ok=True)
    external_dir.mkdir(parents=True, exist_ok=True)
    external_file = external_dir / "keep.png"
    external_file.write_bytes(b"external")
    link_path = output_dir / "ethanol001_link.png"
    try:
        link_path.symlink_to(external_file)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    report["images"]["redrawn_molecule"] = str(link_path)
    report_path = output_dir / "ethanol_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(report, report_path)

    result = repository.delete_analysis_and_files("ethanol001")

    assert repository.get_analysis("ethanol001") is not None
    assert external_file.exists()
    assert result["errors"][0]["error"] in {"symlink_not_deleted", "path_outside_managed_roots"}


def test_repository_loads_report_from_single_and_batch_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    repository = AnalysisRepository(db_path)
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(report, report_path)
    assert repository.load_report("ethanol001")["analysis_id"] == "ethanol001"

    batch_report = _report(tmp_path, "c1ccccc1", "benzene001", "benzene.png")
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(json.dumps({"reports": [batch_report]}, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(batch_report, batch_path)
    assert repository.load_report("benzene001")["analysis_id"] == "benzene001"


def test_repository_records_corrections_jobs_and_result_payloads(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "app.db"
    repository = AnalysisRepository(db_path)
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    repository.save_analysis(report, tmp_path / "report.json")

    correction = repository.record_correction("ethanol001", "CO", "CCO", "user", "fixed missing carbon")
    assert correction["analysis_id"] == "ethanol001"

    job = repository.save_job({
        "job_id": "batch001",
        "status": "running",
        "total": 10,
        "completed": 4,
        "current_file": "compound_004.png",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:01:00+00:00",
        "result_path": str(tmp_path / "batch.json"),
    })
    assert job["job_id"] == "batch001"

    batch_report = _report(tmp_path, "CCN", "amine001", "amine.png")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"reports": [batch_report]}, ensure_ascii=False), encoding="utf-8")

    import src.storage.analysis_repository as module

    monkeypatch.setattr(module, "AnalysisRepository", lambda: AnalysisRepository(db_path))
    assert record_result_payload({"reports": [batch_report]}, payload_path) == 1
    assert repository.get_analysis("amine001") is not None


def test_production_history_excludes_demo_reports(tmp_path: Path, monkeypatch) -> None:
    import src.storage.analysis_repository as module

    repository = AnalysisRepository(tmp_path / "app.db")
    report = _report(tmp_path, "CCO", "demo001", "demo.png")
    monkeypatch.setattr(module.config, "IS_PRODUCTION_MODE", True)
    monkeypatch.setattr(module, "AnalysisRepository", lambda: repository)

    result = record_report(report, tmp_path / "demo_report.json")

    assert result == {"indexed": False, "reason": "demo_backend_disabled_in_production"}
    assert repository.list_analyses() == []


def test_pytest_default_repository_is_not_user_database(monkeypatch) -> None:
    import src.storage.analysis_repository as module

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_analysis_repository.py::test")

    repository = module.AnalysisRepository()

    assert repository.db_path is not None
    assert repository.db_path != module.config.APP_DB_PATH


def test_repository_favorites_protect_persistent_image_runs(tmp_path: Path) -> None:
    repository = AnalysisRepository(tmp_path / "app.db")
    image_run = create_image_run_from_bytes(
        b"favorite-image",
        "favorite.png",
        runs_root=tmp_path / "runs",
        analysis_id="favorite001",
    )
    report = MoleculeReportGenerator("manual", image_run.run_dir).generate(
        smiles="CCO",
        analysis_id=image_run.analysis_id,
    )
    report_path = save_run_report(report, image_run)
    repository.save_analysis(report, report_path)

    repository.set_favorite(image_run.analysis_id, True)
    runtime = json.loads(image_run.runtime_path.read_text(encoding="utf-8"))
    assert runtime["protected"] is True
    assert "favorite" in runtime["protected_reasons"]

    write_runtime_metadata(image_run, {"created_at": "2020-01-01T00:00:00+00:00"})
    protected_cleanup = cleanup_runs(tmp_path / "runs", retention_days=1, max_storage_gb=10)
    assert protected_cleanup["deleted_count"] == 0
    assert image_run.run_dir.exists()

    repository.set_favorite(image_run.analysis_id, False)
    runtime = json.loads(image_run.runtime_path.read_text(encoding="utf-8"))
    assert runtime["protected"] is False

    unprotected_cleanup = cleanup_runs(tmp_path / "runs", retention_days=1, max_storage_gb=10)
    assert unprotected_cleanup["deleted_count"] == 1
    assert not image_run.run_dir.exists()


def test_plain_history_row_survives_expired_run_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_delete_roots(monkeypatch, tmp_path)
    repository = AnalysisRepository(tmp_path / "app.db")
    image_run = create_image_run_from_bytes(
        b"plain-history-image",
        "plain.png",
        runs_root=tmp_path / "runs",
        analysis_id="plain001",
    )
    report = MoleculeReportGenerator("manual", image_run.run_dir).generate(smiles="CCO", analysis_id=image_run.analysis_id)
    report_path = save_run_report(report, image_run)
    repository.save_analysis(report, report_path)

    write_runtime_metadata(image_run, {"created_at": "2020-01-01T00:00:00+00:00"})
    cleanup = cleanup_runs(tmp_path / "runs", retention_days=1, max_storage_gb=10)

    row = repository.get_analysis(image_run.analysis_id)
    assert cleanup["deleted_count"] == 1
    assert row is not None
    assert row["artifact_status"] == ARTIFACT_STATUS_EXPIRED
    assert row["artifact_reason"] == "run_artifact_expired"
    assert repository.load_report(image_run.analysis_id) is None
    assert repository.list_analyses()[0]["artifact_status"] == ARTIFACT_STATUS_EXPIRED


def test_missing_external_report_is_marked_missing_without_deleting_row(tmp_path: Path) -> None:
    repository = AnalysisRepository(tmp_path / "app.db")
    report = _report(tmp_path, "CCO", "ethanol001", "ethanol.png")
    report_path = tmp_path / "manual_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repository.save_analysis(report, report_path)
    report_path.unlink()

    row = repository.get_analysis("ethanol001")

    assert row is not None
    assert row["artifact_status"] == ARTIFACT_STATUS_MISSING
    assert row["artifact_reason"] == "report_path_missing"
    assert repository.load_report("ethanol001") is None


def test_cancel_favorite_keeps_feedback_run_protection(tmp_path: Path) -> None:
    repository = AnalysisRepository(tmp_path / "app.db")
    image_run = create_image_run_from_bytes(
        b"feedback-protected-image",
        "feedback.png",
        runs_root=tmp_path / "runs",
        analysis_id="feedback001",
    )
    report = MoleculeReportGenerator("manual", image_run.run_dir).generate(smiles="CCO", analysis_id=image_run.analysis_id)
    report_path = save_run_report(report, image_run)
    repository.save_analysis(report, report_path)

    mark_run_protected_from_report(report, reason="feedback")
    repository.set_favorite(image_run.analysis_id, True)
    repository.set_favorite(image_run.analysis_id, False)
    runtime = json.loads(image_run.runtime_path.read_text(encoding="utf-8"))

    assert runtime["protected"] is True
    assert "feedback" in runtime["protected_reasons"]
    assert "favorite" not in runtime["protected_reasons"]

    write_runtime_metadata(image_run, {"created_at": "2020-01-01T00:00:00+00:00"})
    cleanup = cleanup_runs(tmp_path / "runs", retention_days=1, max_storage_gb=10)
    assert cleanup["deleted_count"] == 0
    assert image_run.run_dir.exists()
