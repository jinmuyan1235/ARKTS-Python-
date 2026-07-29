"""Repository for searchable local analysis history."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import csv
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping
from uuid import uuid4

import config
from src.runtime.run_store import set_run_protection_from_report
from src.storage.database import connect
from src.utils.file_utils import safe_stem


STATUS_FILTERS = {
    "all": "",
    "success": "status = 'success'",
    "review_needed": "decision = 'review_needed'",
    "rejected": "decision = 'rejected'",
    "failed": "(status != 'success' OR delete_status IN ('deleting', 'delete_failed'))",
}

DELETE_STATUS_ACTIVE = ""
DELETE_STATUS_DELETING = "deleting"
DELETE_STATUS_FAILED = "delete_failed"

ARTIFACT_STATUS_AVAILABLE = "available"
ARTIFACT_STATUS_EXPIRED = "expired"
ARTIFACT_STATUS_MISSING = "missing"


class AnalysisRepository:
    """Read/write the local SQLite history index."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path).expanduser().resolve() if db_path is not None else _default_db_path()

    def save_analysis(
        self,
        report: Mapping[str, Any],
        report_path: str | Path | None = None,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one analysis report into the history index."""
        record = analysis_record_from_report(report, report_path, owner_user_id=owner_user_id)
        with closing(connect(self.db_path)) as connection:
            existing = connection.execute(
                "SELECT is_favorite FROM analyses WHERE analysis_id = ?",
                (record["analysis_id"],),
            ).fetchone()
            if existing is not None:
                record["is_favorite"] = int(existing["is_favorite"])
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id, created_at, updated_at, input_type, filename, input_path,
                    image_sha256, backend, decision, status, final_smiles, inchikey,
                    report_path, is_favorite, delete_status, delete_errors, delete_requested_at,
                    delete_updated_at, recognition_audit, owner_user_id
                )
                VALUES (
                    :analysis_id, :created_at, :updated_at, :input_type, :filename, :input_path,
                    :image_sha256, :backend, :decision, :status, :final_smiles, :inchikey,
                    :report_path, :is_favorite, :delete_status, :delete_errors, :delete_requested_at,
                    :delete_updated_at, :recognition_audit, :owner_user_id
                )
                ON CONFLICT(analysis_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    input_type = excluded.input_type,
                    filename = excluded.filename,
                    input_path = excluded.input_path,
                    image_sha256 = excluded.image_sha256,
                    backend = excluded.backend,
                    decision = excluded.decision,
                    status = excluded.status,
                    final_smiles = excluded.final_smiles,
                    inchikey = excluded.inchikey,
                    report_path = excluded.report_path,
                    is_favorite = analyses.is_favorite,
                    delete_status = excluded.delete_status,
                    delete_errors = excluded.delete_errors,
                    delete_requested_at = excluded.delete_requested_at,
                    delete_updated_at = excluded.delete_updated_at,
                    recognition_audit = excluded.recognition_audit,
                    owner_user_id = COALESCE(excluded.owner_user_id, analyses.owner_user_id)
                """,
                record,
            )
            connection.commit()
        return record

    def save_many(self, reports: Iterable[Mapping[str, Any]], report_path: str | Path | None = None) -> int:
        """Upsert many reports and return the saved count."""
        count = 0
        for report in reports:
            self.save_analysis(report, report_path=report_path)
            count += 1
        return count

    def record_correction(
        self,
        analysis_id: str,
        previous_smiles: str | None,
        new_smiles: str | None,
        source: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Persist one correction event."""
        record = {
            "correction_id": uuid4().hex,
            "analysis_id": analysis_id,
            "previous_smiles": previous_smiles,
            "new_smiles": new_smiles,
            "source": source,
            "created_at": utc_now(),
            "notes": notes,
        }
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO corrections (
                    correction_id, analysis_id, previous_smiles, new_smiles, source, created_at, notes
                )
                VALUES (
                    :correction_id, :analysis_id, :previous_smiles, :new_smiles, :source, :created_at, :notes
                )
                """,
                record,
            )
            connection.commit()
        return record

    def save_job(self, job: Mapping[str, Any], job_type: str = "batch") -> dict[str, Any]:
        """Upsert a background job row."""
        now = utc_now()
        progress = {
            "total": job.get("total"),
            "completed": job.get("completed"),
            "current_file": job.get("current_file"),
            "accepted": job.get("accepted"),
            "accepted_with_warning": job.get("accepted_with_warning"),
            "review_needed": job.get("review_needed"),
            "manual_review_total": job.get("manual_review_total"),
            "rejected": job.get("rejected"),
            "failed": job.get("failed"),
            "skipped": job.get("skipped"),
        }
        record = {
            "job_id": str(job.get("job_id") or uuid4().hex),
            "job_type": job_type,
            "status": str(job.get("status") or "unknown"),
            "progress": json.dumps(progress, ensure_ascii=False),
            "created_at": str(job.get("created_at") or now),
            "updated_at": str(job.get("updated_at") or now),
            "result_path": str(job.get("result_path") or ""),
        }
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_id, job_type, status, progress, created_at, updated_at, result_path)
                VALUES (:job_id, :job_type, :status, :progress, :created_at, :updated_at, :result_path)
                ON CONFLICT(job_id) DO UPDATE SET
                    job_type = excluded.job_type,
                    status = excluded.status,
                    progress = excluded.progress,
                    updated_at = excluded.updated_at,
                    result_path = excluded.result_path
                """,
                record,
            )
            connection.commit()
        return record

    def list_analyses(
        self,
        query: str = "",
        status_filter: str = "all",
        favorites_only: bool = False,
        owner_user_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search analyses by filename, SMILES, InChIKey, or id."""
        clauses: list[str] = []
        params: list[Any] = []
        query = query.strip()
        if query:
            like = f"%{query}%"
            clauses.append(
                "(filename LIKE ? OR final_smiles LIKE ? OR inchikey LIKE ? OR analysis_id LIKE ? OR image_sha256 LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        filter_clause = STATUS_FILTERS.get(status_filter, "")
        if filter_clause:
            clauses.append(filter_clause)
        if favorites_only:
            clauses.append("is_favorite = 1")
        if owner_user_id:
            clauses.append("owner_user_id = ?")
            params.append(owner_user_id)
        if config.IS_PRODUCTION_MODE:
            clauses.append("backend != 'demo'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([int(limit), int(offset)])
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT analysis_id, created_at, updated_at, input_type, filename, input_path,
                       image_sha256, backend, decision, status, final_smiles, inchikey,
                       report_path, is_favorite, delete_status, delete_errors, delete_requested_at,
                       delete_updated_at, owner_user_id
                FROM analyses
                {where}
                ORDER BY is_favorite DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [_row_with_artifact_state(row) for row in rows]

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        """Return one analysis row by id."""
        with closing(connect(self.db_path)) as connection:
            row = connection.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        return _row_with_artifact_state(row) if row is not None else None

    def load_report(self, analysis_id: str) -> dict[str, Any] | None:
        """Load the original report JSON for an indexed analysis."""
        row = self.get_analysis(analysis_id)
        if not row:
            return None
        path = Path(str(row.get("report_path") or ""))
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("analysis_id") == analysis_id:
            return payload
        for key in ("results", "reports"):
            for report in payload.get(key) or []:
                if isinstance(report, Mapping) and report.get("analysis_id") == analysis_id:
                    return dict(report)
        for region in payload.get("regions") or []:
            report = region.get("report") if isinstance(region, Mapping) else None
            if isinstance(report, Mapping) and report.get("analysis_id") == analysis_id:
                return dict(report)
        return None

    def set_favorite(self, analysis_id: str, favorite: bool) -> None:
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE analyses SET is_favorite = ?, updated_at = ? WHERE analysis_id = ?",
                (1 if favorite else 0, utc_now(), analysis_id),
            )
            connection.commit()
        self._set_run_favorite_protection(analysis_id, favorite)

    def delete_analysis(self, analysis_id: str) -> None:
        """Delete an analysis index row and correction rows; report files are left untouched."""
        with closing(connect(self.db_path)) as connection:
            connection.execute("DELETE FROM analyses WHERE analysis_id = ?", (analysis_id,))
            connection.commit()

    def delete_analysis_and_files(self, analysis_id: str) -> dict[str, Any]:
        """Delete a history row and locally owned report/run artifacts."""
        row = self.get_analysis(analysis_id)
        report = self.load_report(analysis_id) if row else None
        result: dict[str, Any] = {
            "analysis_id": analysis_id,
            "status": "missing" if row is None else DELETE_STATUS_DELETING,
            "record_deleted": False,
            "record_retained": bool(row),
            "candidate_paths": [],
            "deleted_paths": [],
            "skipped_paths": [],
            "errors": [],
            "path_results": [],
        }
        if row is None:
            result["errors"].append({"path": "", "error": "analysis_not_found"})
            return result

        candidates = _dedupe_paths([*_owned_file_candidates(analysis_id, row, report), *_previous_failed_paths(row)])
        result["candidate_paths"] = [str(path.expanduser().resolve()) for path in candidates]
        self._set_delete_state(analysis_id, DELETE_STATUS_DELETING, result)
        _delete_owned_candidates(analysis_id, candidates, result)
        result["record_retained"] = bool(result["errors"])
        if result["errors"]:
            result["status"] = DELETE_STATUS_FAILED
            self._set_delete_state(analysis_id, DELETE_STATUS_FAILED, result)
            return result
        self.delete_analysis(analysis_id)
        result["status"] = "deleted"
        result["record_deleted"] = True
        result["record_retained"] = False
        return result

    def export_rows_csv(self, rows: Iterable[Mapping[str, Any]]) -> str:
        """Return selected history rows as UTF-8 CSV text."""
        rows = list(rows)
        fields = [
            "analysis_id",
            "created_at",
            "input_type",
            "filename",
            "image_sha256",
            "backend",
            "decision",
            "status",
            "final_smiles",
            "inchikey",
            "report_path",
            "is_favorite",
            "delete_status",
            "delete_errors",
            "delete_requested_at",
            "delete_updated_at",
            "artifact_status",
            "artifact_reason",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    def _set_run_favorite_protection(self, analysis_id: str, favorite: bool) -> None:
        report = self.load_report(analysis_id)
        if not report:
            return
        set_run_protection_from_report(report, protected=favorite, reason="favorite")

    def _set_delete_state(self, analysis_id: str, status: str, result: Mapping[str, Any]) -> None:
        now = utc_now()
        requested_at = now if status == DELETE_STATUS_DELETING else None
        with closing(connect(self.db_path)) as connection:
            if requested_at is None:
                connection.execute(
                    """
                    UPDATE analyses
                    SET delete_status = ?, delete_errors = ?, delete_updated_at = ?
                    WHERE analysis_id = ?
                    """,
                    (status, json.dumps(dict(result), ensure_ascii=False, default=str), now, analysis_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE analyses
                    SET delete_status = ?, delete_errors = ?, delete_requested_at = ?, delete_updated_at = ?
                    WHERE analysis_id = ?
                    """,
                    (status, json.dumps(dict(result), ensure_ascii=False, default=str), requested_at, now, analysis_id),
                )
            connection.commit()


def record_report(
    report: Mapping[str, Any],
    report_path: str | Path | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for UI code."""
    if not _should_index_report(report):
        return {"indexed": False, "reason": "demo_backend_disabled_in_production"}
    return AnalysisRepository().save_analysis(report, report_path=report_path, owner_user_id=owner_user_id)


def record_reports(
    reports: Iterable[Mapping[str, Any]],
    report_path: str | Path | None = None,
    owner_user_id: str | None = None,
) -> int:
    """Convenience wrapper for indexing many reports."""
    repository = AnalysisRepository()
    count = 0
    for report in reports:
        if _should_index_report(report):
            repository.save_analysis(report, report_path=report_path, owner_user_id=owner_user_id)
            count += 1
    return count


def record_result_payload(
    result: Mapping[str, Any],
    report_path: str | Path | None = None,
    owner_user_id: str | None = None,
) -> int:
    """Index all reports inside a batch or document result payload."""
    reports = list(result.get("reports") or [])
    if not reports and result.get("regions"):
        reports = [
            region.get("report")
            for region in result.get("regions") or []
            if isinstance(region, Mapping) and isinstance(region.get("report"), Mapping)
        ]
    valid_reports = [report for report in reports if isinstance(report, Mapping)]
    if not valid_reports:
        return 0
    # A batch result is a container, not an individual report. Pointing every
    # analysis row at the same container made review persistence overwrite the
    # whole batch JSON. Store one durable snapshot per analysis instead.
    if report_path is not None:
        container_path = Path(report_path).expanduser().resolve()
        snapshot_dir = container_path.parent / f"{container_path.stem}_reports"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        repository = AnalysisRepository()
        for report in valid_reports:
            if not _should_index_report(report):
                continue
            analysis_id = str(report.get("analysis_id") or uuid4().hex)
            snapshot_path = snapshot_dir / f"{analysis_id}.json"
            temporary = snapshot_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(dict(report), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary.replace(snapshot_path)
            repository.save_analysis(report, report_path=snapshot_path, owner_user_id=owner_user_id)
            count += 1
        return count
    return record_reports(valid_reports, owner_user_id=owner_user_id)


def analysis_record_from_report(
    report: Mapping[str, Any],
    report_path: str | Path | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    input_data = _block(report, "input")
    ocsr = _block(report, "ocsr")
    final = _block(report, "final")
    decision = _block(report, "recognition_decision")
    identity = _block(report, "chemical_identity")
    validation = _block(report, "validation")
    recognition_audit = _block(report, "recognition_audit")
    analysis_id = str(report.get("analysis_id") or uuid4().hex)
    created = str(report.get("created_at") or utc_now())
    path = report_path or _report_path_from_report(report)
    return {
        "analysis_id": analysis_id,
        "created_at": created,
        "updated_at": utc_now(),
        "input_type": _text(input_data.get("type")),
        "filename": _text(input_data.get("filename") or Path(str(input_data.get("path") or "")).name),
        "input_path": _text(input_data.get("path")),
        "image_sha256": _text(input_data.get("image_sha256")),
        "backend": _text(ocsr.get("backend")),
        "decision": _text(decision.get("decision") or ocsr.get("decision")),
        "status": _text(report.get("status")),
        "final_smiles": _text(final.get("smiles") or validation.get("canonical_smiles") or ocsr.get("smiles")),
        "inchikey": _text(identity.get("inchikey")),
        "report_path": str(Path(path).expanduser().resolve()) if path else "",
        "is_favorite": 0,
        "delete_status": DELETE_STATUS_ACTIVE,
        "delete_errors": "",
        "delete_requested_at": None,
        "delete_updated_at": None,
        "recognition_audit": json.dumps(dict(recognition_audit), ensure_ascii=False, default=str),
        "owner_user_id": owner_user_id,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report_path_from_report(report: Mapping[str, Any]) -> str | None:
    run = _block(report, "run")
    if run.get("report_path"):
        return str(run["report_path"])
    return None


def _block(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _default_db_path() -> Path | None:
    """Keep subprocess-based pytest runs out of the user's local history database."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return Path(tempfile.gettempdir()) / f"molecule_vision_pytest_{os.getpid()}.db"
    return None


def _should_index_report(report: Mapping[str, Any]) -> bool:
    ocsr = _block(report, "ocsr")
    return not (config.IS_PRODUCTION_MODE and str(ocsr.get("backend") or "").lower() == "demo")


def _row_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    if "is_favorite" in data:
        data["is_favorite"] = bool(data["is_favorite"])
    return data


def _row_with_artifact_state(row: Any) -> dict[str, Any]:
    data = _row_dict(row)
    data.update(_artifact_state_for_row(data))
    return data


def _artifact_state_for_row(row: Mapping[str, Any]) -> dict[str, str]:
    path_text = str(row.get("report_path") or "").strip()
    if not path_text:
        return {
            "artifact_status": ARTIFACT_STATUS_MISSING,
            "artifact_reason": "report_path_missing",
        }
    path = Path(path_text).expanduser()
    try:
        if path.is_file():
            return {
                "artifact_status": ARTIFACT_STATUS_AVAILABLE,
                "artifact_reason": "",
            }
        resolved = path.resolve()
    except OSError as exc:
        return {
            "artifact_status": ARTIFACT_STATUS_MISSING,
            "artifact_reason": f"report_path_error:{exc.__class__.__name__}",
        }
    if _is_under_expirable_artifact_root(resolved):
        return {
            "artifact_status": ARTIFACT_STATUS_EXPIRED,
            "artifact_reason": "run_artifact_expired",
        }
    return {
        "artifact_status": ARTIFACT_STATUS_MISSING,
        "artifact_reason": "report_path_missing",
    }


def _is_under_expirable_artifact_root(path: Path) -> bool:
    runs_root = Path(config.RUNS_DIR).expanduser().resolve()
    return path == runs_root or _is_relative_to(path, runs_root)


def _owned_file_candidates(
    analysis_id: str,
    row: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
) -> list[Path]:
    candidates: list[Path] = []
    run_dir = _run_dir_from_report(report)
    if run_dir is not None and _is_owned_run_dir(run_dir, analysis_id):
        candidates.append(run_dir)

    report_path = _report_path_from_row_or_report(row, report)
    if report_path is not None and _is_direct_report_file(report_path, analysis_id):
        if run_dir is None or not _is_relative_to(report_path.expanduser().resolve(), run_dir.expanduser().resolve()):
            candidates.append(report_path)

    if report is not None:
        for path in _report_asset_paths(report):
            candidate = path.expanduser()
            resolved = candidate.resolve()
            if run_dir is not None and _is_relative_to(resolved, run_dir.expanduser().resolve()):
                continue
            if _is_generated_asset_path(resolved, analysis_id) or _is_generated_symlink_candidate(candidate, analysis_id):
                candidates.append(candidate)
    return _dedupe_paths(candidates)


def _previous_failed_paths(row: Mapping[str, Any] | None) -> list[Path]:
    if not row:
        return []
    try:
        payload = json.loads(str(row.get("delete_errors") or "{}"))
    except json.JSONDecodeError:
        return []
    paths: list[Path] = []
    if not isinstance(payload, Mapping):
        return paths
    for item in payload.get("errors") or []:
        if not isinstance(item, Mapping):
            continue
        value = str(item.get("path") or "").strip()
        if value:
            paths.append(Path(value))
    return paths


def _delete_owned_candidates(analysis_id: str, candidates: Iterable[Path], result: dict[str, Any]) -> None:
    deleted_roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if any(resolved == root or _is_relative_to(resolved, root) for root in deleted_roots):
            skipped = {"path": str(resolved), "reason": "covered_by_deleted_parent"}
            result["skipped_paths"].append(skipped)
            result["path_results"].append({"path": str(resolved), "status": "skipped", **skipped})
            continue
        verdict = _deletion_verdict(analysis_id, candidate)
        if verdict["status"] == "skip":
            result["skipped_paths"].append({"path": verdict["path"], "reason": verdict["reason"]})
            result["path_results"].append(verdict)
            continue
        if verdict["status"] == "error":
            error = {"path": verdict["path"], "error": verdict["reason"]}
            result["errors"].append(error)
            result["path_results"].append(verdict)
            continue
        path = Path(str(verdict["path"]))
        try:
            _delete_owned_path(path)
            result["deleted_paths"].append(str(path))
            if verdict.get("target_type") == "directory":
                deleted_roots.append(path)
            result["path_results"].append({"path": str(path), "status": "deleted", "target_type": verdict.get("target_type")})
        except Exception as exc:
            error = {"path": str(path), "error": str(exc), "exception": exc.__class__.__name__}
            result["errors"].append(error)
            result["path_results"].append({"path": str(path), "status": "error", "reason": str(exc), "exception": exc.__class__.__name__})


def _deletion_verdict(analysis_id: str, candidate: Path) -> dict[str, Any]:
    if candidate.is_symlink():
        return {"path": str(candidate.expanduser().resolve()), "status": "error", "reason": "symlink_not_deleted"}
    resolved = candidate.expanduser().resolve()
    if not resolved.exists():
        return {"path": str(resolved), "status": "skip", "reason": "missing"}
    if not _is_under_managed_root(resolved):
        return {"path": str(resolved), "status": "error", "reason": "path_outside_managed_roots"}
    if resolved.is_dir():
        if _is_owned_run_dir(resolved, analysis_id):
            return {"path": str(resolved), "status": "ok", "target_type": "directory"}
        return {"path": str(resolved), "status": "error", "reason": "directory_not_owned_by_analysis"}
    if resolved.is_file():
        if _is_direct_report_file(resolved, analysis_id) or _is_generated_asset_path(resolved, analysis_id):
            return {"path": str(resolved), "status": "ok", "target_type": "file"}
        return {"path": str(resolved), "status": "error", "reason": "file_not_owned_by_analysis"}
    return {"path": str(resolved), "status": "error", "reason": "unsupported_path_type"}


def _delete_owned_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _managed_delete_roots() -> list[Path]:
    roots = [
        Path(config.RUNS_DIR).expanduser().resolve(),
        Path(config.OUTPUT_DIR).expanduser().resolve(),
        Path(config.DOCUMENT_OUTPUT_DIR).expanduser().resolve(),
    ]
    deduped: list[Path] = []
    for root in roots:
        if root not in deduped:
            deduped.append(root)
    return deduped


def _is_under_managed_root(path: Path) -> bool:
    return any(path == root or _is_relative_to(path, root) for root in _managed_delete_roots())


def _run_dir_from_report(report: Mapping[str, Any] | None) -> Path | None:
    if not report:
        return None
    run_data = report.get("run") if isinstance(report.get("run"), Mapping) else {}
    run_dir = run_data.get("run_dir") if isinstance(run_data, Mapping) else None
    return Path(str(run_dir)).expanduser().resolve() if run_dir else None


def _report_path_from_row_or_report(row: Mapping[str, Any] | None, report: Mapping[str, Any] | None) -> Path | None:
    value = row.get("report_path") if row else None
    if not value and report:
        run_data = report.get("run") if isinstance(report.get("run"), Mapping) else {}
        value = run_data.get("report_path") if isinstance(run_data, Mapping) else None
    return Path(str(value)).expanduser().resolve() if value else None


def _is_owned_run_dir(path: Path, analysis_id: str) -> bool:
    if not path.is_dir():
        return False
    runs_root = Path(config.RUNS_DIR).expanduser().resolve()
    if path.parent.resolve() == runs_root and path.name == safe_stem(analysis_id, "analysis"):
        return True
    for metadata_path in (path / "runtime.json", path / "report.json"):
        if not metadata_path.is_file():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, Mapping) and str(payload.get("analysis_id") or "") == analysis_id:
            return True
    return False


def _is_direct_report_file(path: Path, analysis_id: str) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(payload, Mapping) and str(payload.get("analysis_id") or "") == analysis_id


def _report_asset_paths(report: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    _collect_existing_paths(report.get("images"), paths)
    return paths


def _collect_existing_paths(value: Any, paths: list[Path]) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_existing_paths(nested, paths)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_existing_paths(nested, paths)
        return
    if not isinstance(value, str) or not value.strip():
        return
    path = Path(value).expanduser()
    if path.is_file():
        paths.append(path)


def _is_generated_asset_path(path: Path, analysis_id: str) -> bool:
    if not path.is_file():
        return False
    if not _is_under_managed_root(path.expanduser().resolve()):
        return False
    token = analysis_id[:8]
    return analysis_id in path.name or (bool(token) and token in path.name)


def _is_generated_symlink_candidate(path: Path, analysis_id: str) -> bool:
    if not path.is_symlink():
        return False
    location = (path.expanduser().parent.resolve() / path.name)
    if not _is_under_managed_root(location):
        return False
    token = analysis_id[:8]
    return analysis_id in path.name or (bool(token) and token in path.name)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser().resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path.expanduser())
    return deduped


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
