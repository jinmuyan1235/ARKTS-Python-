"""Append-only provenance registry for collected external sources."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.utils.file_utils import ensure_directory


SOURCE_FIELDS = (
    "source_key",
    "source_kind",
    "source_id",
    "source_url",
    "license",
    "license_allowed",
    "retrieved_at",
    "source_sha256",
    "attribution",
    "metadata_json",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceRecord:
    """The minimum provenance facts required for every external source."""

    source_key: str
    source_kind: str
    source_id: str
    source_url: str
    license: str
    license_allowed: bool
    retrieved_at: str
    source_sha256: str = ""
    attribution: str = ""
    metadata: dict[str, Any] | None = None

    def to_row(self) -> dict[str, str]:
        data = asdict(self)
        return {
            "source_key": data["source_key"],
            "source_kind": data["source_kind"],
            "source_id": data["source_id"],
            "source_url": data["source_url"],
            "license": data["license"],
            "license_allowed": "true" if data["license_allowed"] else "false",
            "retrieved_at": data["retrieved_at"],
            "source_sha256": data["source_sha256"],
            "attribution": data["attribution"],
            "metadata_json": json.dumps(data["metadata"] or {}, ensure_ascii=False, sort_keys=True),
        }


class SourceRegistry:
    """Persist source metadata even when policy blocks source materialization."""

    def __init__(self, root: str | Path) -> None:
        self.root = ensure_directory(Path(root).expanduser().resolve())
        self.path = self.root / "source_registry.csv"

    def records(self) -> list[dict[str, str]]:
        if not self.path.is_file():
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]

    def get(self, source_key: str) -> dict[str, str] | None:
        return next((row for row in self.records() if row.get("source_key") == source_key), None)

    def upsert(self, record: SourceRecord) -> Path:
        rows = [row for row in self.records() if row.get("source_key") != record.source_key]
        rows.append(record.to_row())
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SOURCE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return self.path

    def write_many(self, records: Iterable[SourceRecord]) -> Path:
        for record in records:
            self.upsert(record)
        return self.path
