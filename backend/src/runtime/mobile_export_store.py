"""Short-lived, isolated artifacts for HarmonyOS export downloads."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from src.utils.file_utils import ensure_directory, safe_stem


class MobileExportStore:
    """Copy generated files into an expiring download-only area."""

    def __init__(self, root: str | Path) -> None:
        self.root = ensure_directory(Path(root).expanduser().resolve())
        self.lock = Lock()

    def register(
        self,
        source: str | Path,
        filename: str,
        content_type: str,
        *,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        export_id = uuid4().hex
        directory = ensure_directory(self.root / export_id)
        suffix = Path(filename).suffix.lower()
        destination = directory / f"artifact{suffix}"
        shutil.copy2(source_path, destination)
        expires_at = int(time.time()) + max(30, int(ttl_seconds))
        metadata = {
            "export_id": export_id,
            "filename": safe_stem(Path(filename).stem, "molecule_export") + suffix,
            "content_type": content_type,
            "path": str(destination),
            "size_bytes": destination.stat().st_size,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_metadata(directory / "export.json", metadata)
        self.cleanup()
        return metadata

    def load(self, export_id: str) -> dict[str, Any] | None:
        if len(export_id) != 32 or any(character not in "0123456789abcdef" for character in export_id.lower()):
            return None
        metadata_path = self.root / export_id / "export.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            path = Path(str(metadata.get("path") or "")).expanduser().resolve()
            path.relative_to((self.root / export_id).resolve())
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not path.is_file():
            return None
        metadata["path"] = str(path)
        return metadata

    def cleanup(self) -> None:
        now = int(time.time())
        with self.lock:
            for directory in self.root.iterdir():
                if not directory.is_dir():
                    continue
                metadata_path = directory / "export.json"
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    expired = int(metadata.get("expires_at") or 0) < now
                except (OSError, ValueError, json.JSONDecodeError):
                    expired = True
                if expired:
                    shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
