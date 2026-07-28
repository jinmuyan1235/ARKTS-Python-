"""Persistent per-analysis run storage for uploaded images."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any
from uuid import uuid4

import config
from src.export.json_exporter import save_json
from src.utils.file_utils import ensure_directory, safe_stem


@dataclass(frozen=True)
class ImageRun:
    """Filesystem layout for one uploaded-image analysis."""

    analysis_id: str
    run_dir: Path
    input_dir: Path
    preprocessing_dir: Path
    structures_dir: Path
    input_path: Path
    report_path: Path
    runtime_path: Path
    original_filename: str
    image_sha256: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_image_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in config.SUPPORTED_IMAGE_EXTENSIONS else ".png"


def image_run_dir(analysis_id: str, runs_root: str | Path = config.RUNS_DIR) -> Path:
    return Path(runs_root).expanduser().resolve() / safe_stem(analysis_id, "analysis")


def _build_run(
    analysis_id: str,
    original_filename: str,
    image_sha256: str,
    runs_root: str | Path = config.RUNS_DIR,
) -> ImageRun:
    run_dir = ensure_directory(image_run_dir(analysis_id, runs_root))
    input_dir = ensure_directory(run_dir / "input")
    preprocessing_dir = ensure_directory(run_dir / "preprocessing")
    structures_dir = ensure_directory(run_dir / "structures")
    suffix = safe_image_suffix(original_filename)
    return ImageRun(
        analysis_id=analysis_id,
        run_dir=run_dir,
        input_dir=input_dir,
        preprocessing_dir=preprocessing_dir,
        structures_dir=structures_dir,
        input_path=input_dir / f"original{suffix}",
        report_path=run_dir / "report.json",
        runtime_path=run_dir / "runtime.json",
        original_filename=original_filename or f"upload{suffix}",
        image_sha256=image_sha256,
    )


def create_image_run_from_bytes(
    payload: bytes,
    original_filename: str,
    runs_root: str | Path = config.RUNS_DIR,
    analysis_id: str | None = None,
) -> ImageRun:
    """Create a persistent image run and save uploaded bytes as input/original.ext."""
    if not payload:
        raise ValueError("上传图片为空，无法创建运行目录。")
    image_sha256 = sha256_bytes(payload)
    run = _build_run(analysis_id or uuid4().hex, original_filename, image_sha256, runs_root)
    run.input_path.write_bytes(payload)
    write_runtime_metadata(run, {"created_at": utc_now_iso(), "status": "created"})
    return run


def create_image_run_from_file(
    source_path: str | Path,
    original_filename: str | None = None,
    runs_root: str | Path = config.RUNS_DIR,
    analysis_id: str | None = None,
) -> ImageRun:
    """Create a persistent image run by copying an existing image file."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入图片不存在：{source}")
    payload = source.read_bytes()
    run = _build_run(analysis_id or uuid4().hex, original_filename or source.name, sha256_bytes(payload), runs_root)
    if source.resolve() != run.input_path.resolve():
        shutil.copy2(source, run.input_path)
    write_runtime_metadata(run, {"created_at": utc_now_iso(), "status": "created"})
    return run


def load_image_run(run_dir: str | Path, original_filename: str | None = None, analysis_id: str | None = None) -> ImageRun:
    """Load a run directory that already contains input/original.ext."""
    root = Path(run_dir).expanduser().resolve()
    input_dir = ensure_directory(root / "input")
    candidates = sorted(input_dir.glob("original.*"))
    if not candidates:
        raise FileNotFoundError(f"运行目录缺少 input/original.*：{root}")
    input_path = candidates[0]
    runtime_path = root / "runtime.json"
    if original_filename is None and runtime_path.is_file():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            original_filename = runtime.get("original_filename")
        except Exception:
            original_filename = None
    payload = input_path.read_bytes()
    run = _build_run(analysis_id or root.name, original_filename or input_path.name, sha256_bytes(payload), root.parent)
    return run


def attach_run_to_report(report: dict[str, Any], run: ImageRun) -> dict[str, Any]:
    """Record persistent input and run paths in a report."""
    report.setdefault("input", {})
    report["input"].update({
        "filename": run.original_filename,
        "path": str(run.input_path.resolve()),
        "image_sha256": run.image_sha256,
    })
    report["run"] = {
        "analysis_id": run.analysis_id,
        "run_dir": str(run.run_dir.resolve()),
        "input_path": str(run.input_path.resolve()),
        "report_path": str(run.report_path.resolve()),
        "runtime_path": str(run.runtime_path.resolve()),
        "protected": False,
    }
    return report


def save_run_report(report: dict[str, Any], run: ImageRun) -> Path:
    """Write report.json and refresh runtime.json for a run."""
    attach_run_to_report(report, run)
    save_json(report, run.report_path)
    write_runtime_metadata(
        run,
        {
            "status": report.get("status"),
            "message": report.get("message"),
            "report_path": str(run.report_path.resolve()),
            "updated_at": utc_now_iso(),
        },
    )
    return run.report_path


def save_report_for_existing_run(report: dict[str, Any]) -> Path | None:
    """Persist an updated report when it already belongs to a run directory."""
    run_data = report.get("run") or {}
    run_dir = run_data.get("run_dir")
    analysis_id = report.get("analysis_id") or run_data.get("analysis_id")
    input_data = report.get("input") or {}
    input_path = input_data.get("path") or run_data.get("input_path")
    if not run_dir or not analysis_id or not input_path:
        return None
    run = ImageRun(
        analysis_id=str(analysis_id),
        run_dir=Path(run_dir).expanduser().resolve(),
        input_dir=Path(run_dir).expanduser().resolve() / "input",
        preprocessing_dir=Path(run_dir).expanduser().resolve() / "preprocessing",
        structures_dir=Path(run_dir).expanduser().resolve() / "structures",
        input_path=Path(input_path).expanduser().resolve(),
        report_path=Path(run_dir).expanduser().resolve() / "report.json",
        runtime_path=Path(run_dir).expanduser().resolve() / "runtime.json",
        original_filename=str(input_data.get("filename") or Path(str(input_path)).name),
        image_sha256=str(input_data.get("image_sha256") or ""),
    )
    return save_run_report(report, run)


def write_runtime_metadata(run: ImageRun, updates: dict[str, Any]) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if run.runtime_path.is_file():
        try:
            existing = json.loads(run.runtime_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    payload = {
        "analysis_id": run.analysis_id,
        "run_dir": str(run.run_dir.resolve()),
        "input_path": str(run.input_path.resolve()),
        "original_filename": run.original_filename,
        "image_sha256": run.image_sha256,
        "protected": existing.get("protected", False),
        **existing,
        **updates,
    }
    payload.setdefault("created_at", utc_now_iso())
    run.runtime_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def report_output_dir(report: dict[str, Any], default: str | Path = config.OUTPUT_DIR) -> Path:
    run = report.get("run") or {}
    run_dir = run.get("run_dir")
    return Path(run_dir).expanduser().resolve() if run_dir else Path(default).expanduser().resolve()


def mark_run_protected_from_report(report: dict[str, Any], reason: str = "feedback") -> None:
    set_run_protection_from_report(report, protected=True, reason=reason)


def set_run_protection_from_report(report: dict[str, Any], protected: bool, reason: str = "manual") -> None:
    run_data = report.get("run") or {}
    run_dir = run_data.get("run_dir")
    analysis_id = report.get("analysis_id") or run_data.get("analysis_id")
    if not run_dir or not analysis_id:
        return
    try:
        input_path = Path(str((report.get("input") or {}).get("path") or run_data.get("input_path"))).expanduser().resolve()
        image_sha = str((report.get("input") or {}).get("image_sha256") or "")
        loaded = ImageRun(
            analysis_id=str(analysis_id),
            run_dir=Path(run_dir).expanduser().resolve(),
            input_dir=Path(run_dir).expanduser().resolve() / "input",
            preprocessing_dir=Path(run_dir).expanduser().resolve() / "preprocessing",
            structures_dir=Path(run_dir).expanduser().resolve() / "structures",
            input_path=input_path,
            report_path=Path(run_dir).expanduser().resolve() / "report.json",
            runtime_path=Path(run_dir).expanduser().resolve() / "runtime.json",
            original_filename=str((report.get("input") or {}).get("filename") or input_path.name),
            image_sha256=image_sha,
        )
        set_run_protection(loaded, protected=protected, reason=reason)
    except Exception:
        return


def set_run_protection(run: ImageRun, protected: bool, reason: str = "manual") -> dict[str, Any]:
    """Add or remove one protection reason from a run's runtime metadata."""
    existing: dict[str, Any] = {}
    if run.runtime_path.is_file():
        try:
            existing = json.loads(run.runtime_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    reasons = _protection_reasons(existing)
    normalized_reason = (reason or "manual").strip() or "manual"
    if protected:
        if normalized_reason not in reasons:
            reasons.append(normalized_reason)
    else:
        reasons = [item for item in reasons if item != normalized_reason]
    updates: dict[str, Any] = {
        "protected": bool(reasons),
        "protected_reasons": reasons,
        "protected_reason": ", ".join(reasons) if reasons else None,
        "protection_updated_at": utc_now_iso(),
    }
    if protected:
        updates["protected_at"] = existing.get("protected_at") or utc_now_iso()
    else:
        updates["unprotected_at"] = utc_now_iso()
    return write_runtime_metadata(run, updates)


def _protection_reasons(runtime: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    raw_reasons = runtime.get("protected_reasons")
    if isinstance(raw_reasons, list):
        reasons.extend(str(item).strip() for item in raw_reasons if str(item).strip())
    legacy_reason = str(runtime.get("protected_reason") or "").strip()
    if legacy_reason:
        reasons.extend(item.strip() for item in legacy_reason.split(",") if item.strip())
    if runtime.get("protected") and not reasons:
        reasons.append("manual")
    deduped: list[str] = []
    for item in reasons:
        if item not in deduped:
            deduped.append(item)
    return deduped


def cleanup_runs(
    runs_root: str | Path = config.RUNS_DIR,
    retention_days: int = config.RUN_RETENTION_DAYS,
    max_storage_gb: float = config.RUN_MAX_STORAGE_GB,
) -> dict[str, Any]:
    """Remove old unprotected runs after retention and storage limits."""
    root = Path(runs_root).expanduser().resolve()
    if not root.is_dir():
        return {"deleted_count": 0, "kept_count": 0, "freed_bytes": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    runs: list[tuple[Path, datetime, int, bool]] = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        runtime_path = item / "runtime.json"
        protected = False
        created = datetime.fromtimestamp(item.stat().st_mtime, timezone.utc)
        if runtime_path.is_file():
            try:
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                protected = bool(runtime.get("protected"))
                created = datetime.fromisoformat(str(runtime.get("created_at")).replace("Z", "+00:00"))
            except Exception:
                pass
        size = sum(path.stat().st_size for path in item.rglob("*") if path.is_file())
        runs.append((item, created, size, protected))
    max_bytes = int(max_storage_gb * 1024**3)
    total_size = sum(size for _path, _created, size, _protected in runs)
    deleted = 0
    freed = 0
    for path, created, size, protected in sorted(runs, key=lambda row: row[1]):
        if protected:
            continue
        if created >= cutoff and total_size <= max_bytes:
            continue
        shutil.rmtree(path, ignore_errors=True)
        deleted += 1
        freed += size
        total_size -= size
    return {"deleted_count": deleted, "kept_count": len(runs) - deleted, "freed_bytes": freed}


def cleanup_runs_if_due(
    runs_root: str | Path = config.RUNS_DIR,
    retention_days: int = config.RUN_RETENTION_DAYS,
    max_storage_gb: float = config.RUN_MAX_STORAGE_GB,
    state_path: str | Path | None = None,
    interval_hours: int = 24,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run cleanup at most once per interval and persist a tiny status file."""
    marker = Path(state_path or (config.DATA_DIR / "health" / "run_cleanup.json")).expanduser().resolve()
    current = now or datetime.now(timezone.utc)
    interval = timedelta(hours=max(1, int(interval_hours)))
    previous = _read_cleanup_state(marker)
    last_attempt = _parse_datetime(previous.get("completed_at") or previous.get("started_at"))
    if not force and last_attempt and current - last_attempt < interval:
        next_run_after = last_attempt + interval
        return {
            "status": "skipped",
            "reason": "not_due",
            "last_completed_at": previous.get("completed_at"),
            "next_run_after": next_run_after.isoformat(),
            "deleted_count": 0,
            "kept_count": previous.get("kept_count", 0),
            "freed_bytes": 0,
            "state_path": str(marker),
        }

    started = time.perf_counter()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at": current.isoformat(),
        "runs_root": str(Path(runs_root).expanduser().resolve()),
        "retention_days": int(retention_days),
        "max_storage_gb": float(max_storage_gb),
        "interval_hours": int(interval_hours),
    }
    try:
        result = cleanup_runs(runs_root, retention_days=retention_days, max_storage_gb=max_storage_gb)
        payload.update(result)
        payload["status"] = "completed"
    except Exception as exc:
        payload.update(
            {
                "status": "failed",
                "exception": exc.__class__.__name__,
                "error": str(exc),
                "deleted_count": 0,
                "kept_count": previous.get("kept_count", 0),
                "freed_bytes": 0,
            }
        )
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    _write_cleanup_state(marker, payload)
    payload["state_path"] = str(marker)
    return payload


def _read_cleanup_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_cleanup_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def image_run_to_dict(run: ImageRun) -> dict[str, Any]:
    data = asdict(run)
    return {key: str(value) if isinstance(value, Path) else value for key, value in data.items()}
