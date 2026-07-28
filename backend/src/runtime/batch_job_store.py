"""Persistent batch-job state for the Streamlit UI."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from config import OUTPUT_DIR
from src.utils.file_utils import ensure_directory


BATCH_JOB_STATUSES = {"queued", "running", "paused", "cancelling", "cancelled", "completed", "failed"}
BATCH_SUMMARY_SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchJobStore:
    """Store resumable batch task state as small JSON files."""

    def __init__(self, root: str | Path = OUTPUT_DIR / "batch_jobs") -> None:
        self.root = ensure_directory(root)

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def state_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def cancel_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "cancel.request"

    def skip_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "skip_current.request"

    def pause_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "pause.request"

    def checkpoint_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "checkpoint.json"

    def stdout_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "stdout.log"

    def stderr_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "stderr.log"

    def create(
        self,
        job_id: str,
        *,
        backend: str,
        input_dir: str | Path,
        output_dir: str | Path,
        total: int = 0,
        source: str = "folder",
        command: list[str] | None = None,
        runtime_config: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
        retry_mode: str | None = None,
        input_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        directory = ensure_directory(self.job_dir(job_id))
        state = {
            "job_id": job_id,
            "status": "queued",
            "backend": backend,
            "source": source,
            "input_dir": str(Path(input_dir).expanduser().resolve()),
            "output_dir": str(Path(output_dir).expanduser().resolve()),
            "total": total,
            "completed": 0,
            "successful": 0,
            "accepted": 0,
            "accepted_with_warning": 0,
            "review_needed": 0,
            "manual_review_total": 0,
            "pending_confirmation": 0,
            "rejected": 0,
            "failed": 0,
            "skipped": 0,
            "cache_hits": 0,
            "duplicate_files": int((input_summary or {}).get("duplicate_files") or 0),
            "current_file": None,
            "current_index": None,
            "pid": None,
            "command": command or [],
            "runtime_config": runtime_config or {},
            "parent_job_id": parent_job_id,
            "retry_mode": retry_mode,
            "input_summary": input_summary or {},
            "checkpoint_path": str(self.checkpoint_path(job_id)),
            "max_concurrency": 1,
            "result_path": None,
            "exports": {},
            "message": "",
            "error": "",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "updated_at": utc_now(),
            "stdout_path": str(self.stdout_path(job_id)),
            "stderr_path": str(self.stderr_path(job_id)),
        }
        self._write_state(directory / "job.json", state)
        return state

    def read(self, job_id: str) -> dict[str, Any]:
        path = self.state_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(f"批量任务不存在：{job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, job_id: str) -> bool:
        return self.state_path(job_id).is_file()

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        state = self.read(job_id)
        state.update(fields)
        state["updated_at"] = utc_now()
        self._write_state(self.state_path(job_id), state)
        return state

    def mark_running(self, job_id: str, pid: int, command: list[str] | None = None) -> dict[str, Any]:
        state = self.read(job_id)
        fields: dict[str, Any] = {"status": "running", "pid": pid, "started_at": state.get("started_at") or utc_now()}
        if command is not None:
            fields["command"] = command
        return self.update(job_id, **fields)

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> dict[str, Any]:
        summary = progress.get("summary") or {}
        counts = _batch_summary_counts(summary)
        status = str(progress.get("status") or "running")
        if self.pause_requested(job_id) and status == "running":
            status = "paused"
        if status not in BATCH_JOB_STATUSES:
            status = "running"
        fields = {
            "status": status,
            "total": int(progress.get("total") or summary.get("total") or 0),
            "completed": int(progress.get("completed") or summary.get("completed") or 0),
            "successful": int(summary.get("successful") or 0),
            "accepted": counts["accepted"],
            "accepted_with_warning": counts["accepted_with_warning"],
            "review_needed": counts["review_needed"],
            "manual_review_total": counts["manual_review_total"],
            "pending_confirmation": int(summary.get("pending_confirmation") or 0),
            "rejected": counts["rejected"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "cache_hits": int(progress.get("cache_hits") or summary.get("cache_hits") or 0),
            "current_file": progress.get("current_file"),
            "current_index": progress.get("current_index"),
            "summary": summary,
        }
        if progress.get("result_path"):
            fields["result_path"] = str(progress["result_path"])
        if progress.get("exports"):
            fields["exports"] = dict(progress["exports"])
        if status in {"completed", "cancelled", "failed"}:
            fields["finished_at"] = utc_now()
        return self.update(job_id, **fields)

    def complete(self, job_id: str, result_path: str | Path, exports: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
        counts = _batch_summary_counts(summary)
        return self.update(
            job_id,
            status="completed",
            result_path=str(result_path),
            exports=exports,
            summary=summary,
            total=int(summary.get("total") or 0),
            completed=int(summary.get("completed") or summary.get("total") or 0),
            successful=int(summary.get("successful") or 0),
            accepted=counts["accepted"],
            accepted_with_warning=counts["accepted_with_warning"],
            review_needed=counts["review_needed"],
            manual_review_total=counts["manual_review_total"],
            pending_confirmation=int(summary.get("pending_confirmation") or 0),
            rejected=counts["rejected"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            current_file=None,
            finished_at=utc_now(),
        )

    def fail(self, job_id: str, message: str) -> dict[str, Any]:
        return self.update(job_id, status="failed", error=message, message=message, finished_at=utc_now())

    def mark_cancelled(self, job_id: str, message: str = "任务已取消。") -> dict[str, Any]:
        return self.update(job_id, status="cancelled", message=message, current_file=None, finished_at=utc_now())

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        self.cancel_path(job_id).write_text(utc_now(), encoding="utf-8")
        state = self.read(job_id)
        if state.get("status") in {"queued", "running", "paused"}:
            state = self.update(job_id, status="cancelling", message="正在取消任务……")
        return state

    def cancel_requested(self, job_id: str) -> bool:
        return self.cancel_path(job_id).is_file()

    def request_pause(self, job_id: str) -> dict[str, Any]:
        state = self.read(job_id)
        if state.get("status") not in {"queued", "running"}:
            return state
        self.pause_path(job_id).write_text(utc_now(), encoding="utf-8")
        return self.update(job_id, status="paused", message="任务将在当前文件完成后暂停。")

    def resume(self, job_id: str) -> dict[str, Any]:
        self.pause_path(job_id).unlink(missing_ok=True)
        state = self.read(job_id)
        if state.get("status") == "paused":
            return self.update(job_id, status="running", message="任务已继续。")
        return state

    def pause_requested(self, job_id: str) -> bool:
        return self.pause_path(job_id).is_file()

    def clear_control_requests(self, job_id: str) -> None:
        for path in (self.cancel_path(job_id), self.pause_path(job_id), self.skip_path(job_id)):
            path.unlink(missing_ok=True)

    def request_skip_current(self, job_id: str) -> dict[str, Any]:
        self.skip_path(job_id).write_text(utc_now(), encoding="utf-8")
        return self.update(job_id, message="已请求跳过下一张未开始文件；正在推理的图片不会被中断。")

    def consume_skip_request(self, job_id: str) -> bool:
        path = self.skip_path(job_id)
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            pass
        return True

    def list_jobs(self, limit: int = 20, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        allowed = set(statuses) if statuses is not None else None
        jobs: list[dict[str, Any]] = []
        for path in self.root.glob("*/job.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if allowed is not None and state.get("status") not in allowed:
                continue
            jobs.append(state)
        jobs.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return jobs[:limit]

    def load_result(self, job_id: str) -> dict[str, Any] | None:
        state = self.read(job_id)
        result_path = state.get("result_path")
        if not result_path or not Path(str(result_path)).is_file():
            return None
        return json.loads(Path(str(result_path)).read_text(encoding="utf-8"))

    def clear(self, job_id: str) -> None:
        directory = self.job_dir(job_id)
        if directory.is_dir():
            shutil.rmtree(directory)

    @staticmethod
    def _write_state(path: Path, state: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def _batch_summary_counts(summary: dict[str, Any]) -> dict[str, int]:
    accepted = _int_value(summary.get("accepted"))
    accepted_with_warning = _int_value(summary.get("accepted_with_warning"))
    review_needed = _int_value(summary.get("review_needed"))
    rejected = _int_value(summary.get("rejected"))
    failed = _int_value(summary.get("failed"))
    skipped = _int_value(summary.get("skipped"))
    schema_version = _int_value(summary.get("summary_schema_version"))
    if "manual_review_total" in summary or schema_version >= BATCH_SUMMARY_SCHEMA_VERSION:
        manual_review_total = _int_value(summary.get("manual_review_total"), accepted_with_warning + review_needed)
    else:
        manual_review_total = review_needed
        review_needed = max(0, review_needed - accepted_with_warning)
    return {
        "accepted": accepted,
        "accepted_with_warning": accepted_with_warning,
        "review_needed": review_needed,
        "manual_review_total": manual_review_total,
        "rejected": rejected,
        "failed": failed,
        "skipped": skipped,
    }


def _int_value(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
