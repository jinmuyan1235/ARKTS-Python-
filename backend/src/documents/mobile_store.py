"""Durable storage helpers for the HarmonyOS document-review workflow.

The mobile API keeps only a small manifest in ``data/mobile_documents``.  The
actual rendered pages, detected regions and audit records remain in the
existing document processor's canonical ``document_result.json`` output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MobileDocumentStore:
    """Persist uploaded documents and pointers to processor results safely."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _document_dir(self, document_id: str) -> Path:
        if not document_id or any(character not in "0123456789abcdef" for character in document_id.lower()):
            raise KeyError(document_id)
        path = (self.root / document_id).resolve()
        path.relative_to(self.root)
        return path

    def _manifest_path(self, document_id: str) -> Path:
        return self._document_dir(document_id) / "manifest.json"

    def create(
        self,
        payload: bytes,
        filename: str,
        content_type: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        document_id = uuid4().hex
        document_dir = self._document_dir(document_id)
        document_dir.mkdir(parents=True, exist_ok=False)
        suffix = Path(filename).suffix.lower()
        input_path = document_dir / f"input{suffix}"
        input_path.write_bytes(payload)
        now = _utc_now()
        manifest: dict[str, Any] = {
            "documentId": document_id,
            "filename": Path(filename).name,
            "contentType": content_type,
            "status": "uploaded",
            "message": "文档已上传，等待区域检测。",
            "ownerUserId": owner_user_id,
            "inputPath": str(input_path),
            "resultPath": None,
            "detectionJobId": None,
            "createdAt": now,
            "updatedAt": now,
        }
        self.save(manifest)
        return manifest

    def save(self, manifest: dict[str, Any]) -> None:
        document_id = str(manifest["documentId"])
        target = self._manifest_path(document_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest["updatedAt"] = _utc_now()
        temporary = target.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, target)

    def load(self, document_id: str) -> dict[str, Any] | None:
        try:
            path = self._manifest_path(document_id)
        except (KeyError, ValueError):
            return None
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def update(self, document_id: str, **values: Any) -> dict[str, Any]:
        manifest = self.load(document_id)
        if manifest is None:
            raise KeyError(document_id)
        manifest.update(values)
        self.save(manifest)
        return manifest

    def load_result(self, document_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self.load(document_id)
        if manifest is None:
            raise KeyError(document_id)
        raw_path = manifest.get("resultPath")
        if not raw_path:
            raise FileNotFoundError(document_id)
        result_path = Path(str(raw_path)).expanduser().resolve()
        try:
            value = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(document_id) from exc
        if not isinstance(value, dict):
            raise FileNotFoundError(document_id)
        return manifest, value
