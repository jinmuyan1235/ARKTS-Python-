"""State and coordinate helpers for the interactive document-region reviewer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from src.documents.models import normalize_bbox
from src.documents.region_editing import apply_region_edits


CANVAS_ACTIONS = {"select", "create", "update", "delete"}


def canvas_bbox_to_page(
    bbox: list[float] | tuple[float, float, float, float],
    canvas_width: float,
    canvas_height: float,
    page_width: int,
    page_height: int,
) -> list[int]:
    """Map a displayed-canvas bbox back to the rendered page coordinate space."""
    if canvas_width <= 0 or canvas_height <= 0:
        raise ValueError("canvas dimensions must be positive.")
    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive.")
    if len(bbox) != 4:
        raise ValueError("bbox must contain exactly four coordinates.")
    scale_x = page_width / float(canvas_width)
    scale_y = page_height / float(canvas_height)
    mapped = [
        float(bbox[0]) * scale_x,
        float(bbox[1]) * scale_y,
        float(bbox[2]) * scale_x,
        float(bbox[3]) * scale_y,
    ]
    return list(normalize_bbox(mapped, page_width, page_height))


def canvas_event_from_query(params: Mapping[str, Any], page: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate one canvas query event and normalize its bbox to page coordinates."""
    action = str(params.get("doc_bbox_action") or "").strip().lower()
    if action not in CANVAS_ACTIONS:
        return None
    event: dict[str, Any] = {
        "action": action,
        "region_id": str(params.get("doc_bbox_region_id") or "").strip() or None,
        "page_number": int(page.get("page_number") or 0),
        "nonce": str(params.get("doc_bbox_nonce") or ""),
    }
    if action in {"create", "update"}:
        raw_bbox = [float(str(params.get(f"doc_bbox_{coord}") or "0")) for coord in ("x1", "y1", "x2", "y2")]
        event["bbox"] = canvas_bbox_to_page(
            raw_bbox,
            float(str(params.get("doc_canvas_width") or "0")),
            float(str(params.get("doc_canvas_height") or "0")),
            int(page.get("width") or 0),
            int(page.get("height") or 0),
        )
    return event


def save_region_selection(
    document_result: dict[str, Any],
    region_id: str,
    bbox: list[int],
    *,
    recognize: bool,
    region_type: str | None = None,
) -> dict[str, Any]:
    """Save the latest selection, forcing molecule confirmation only for recognition."""
    selected = next(
        (region for region in document_result.get("regions", []) if str(region.get("region_id")) == str(region_id)),
        None,
    )
    if selected is None:
        raise ValueError(f"Unknown region_id: {region_id}")
    edit = {
        "action": "update",
        "region_id": str(region_id),
        "bbox": list(bbox),
        "region_type": "molecule" if recognize else str(region_type or selected.get("region_type") or "molecule"),
        "confirmed": bool(recognize),
        "note": "Saved before background OCSR." if recognize else "Saved selection without recognition.",
    }
    updated = apply_region_edits(document_result, [edit])
    _restore_document_summary(updated, document_result)
    return updated


def apply_canvas_event(document_result: dict[str, Any], event: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Apply a create/update/delete event and return the region that should remain selected."""
    action = str(event.get("action") or "")
    region_id = str(event.get("region_id") or "") or None
    if action == "select":
        return document_result, region_id
    if action == "create":
        before_ids = {str(region.get("region_id")) for region in document_result.get("regions", [])}
        updated = apply_region_edits(document_result, [{
            "action": "add",
            "page_number": int(event["page_number"]),
            "bbox": list(event["bbox"]),
            "region_type": "molecule",
            "confirmed": False,
            "note": "Created by canvas drag.",
        }])
        selected_id = next(
            (str(region.get("region_id")) for region in updated.get("regions", []) if str(region.get("region_id")) not in before_ids),
            None,
        )
    elif action == "update" and region_id:
        current = next(
            (region for region in document_result.get("regions", []) if str(region.get("region_id")) == region_id),
            None,
        )
        if current is None:
            raise ValueError(f"Unknown region_id: {region_id}")
        updated = apply_region_edits(document_result, [{
            "action": "update",
            "region_id": region_id,
            "bbox": list(event["bbox"]),
            "region_type": str(current.get("region_type") or "molecule"),
            "confirmed": False,
            "note": "Moved or resized on canvas.",
        }])
        selected_id = region_id
    elif action == "delete" and region_id:
        updated = apply_region_edits(document_result, [{
            "action": "delete",
            "region_id": region_id,
            "note": "Deleted on canvas.",
        }])
        selected_id = None
    else:
        return document_result, region_id
    _restore_document_summary(updated, document_result)
    return updated, selected_id


def persist_document_result_atomic(document_result: dict[str, Any]) -> Path:
    """Atomically replace the canonical document JSON and keep its export pointer valid."""
    output_dir = Path(document_result.get("output_dir") or ".").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = str((document_result.get("exports") or {}).get("json") or "").strip()
    target = Path(existing).expanduser().resolve() if existing else output_dir / "document_result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document_result.setdefault("exports", {})["json"] = str(target)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(document_result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def background_failure_reason(
    return_code: int,
    payload: Mapping[str, Any] | None,
    stdout: str,
    stderr: str,
) -> str:
    """Return a concise actionable reason for a failed region worker."""
    payload_message = str((payload or {}).get("message") or "").strip()
    if payload_message:
        return payload_message
    lines = (stderr or stdout or "").strip().splitlines()
    if lines:
        return lines[-1]
    return f"后台进程退出码 {return_code}"


def _restore_document_summary(updated: dict[str, Any], original: Mapping[str, Any]) -> None:
    summary = updated.setdefault("summary", {})
    previous = original.get("summary") or {}
    summary["page_count"] = previous.get("page_count") or len(updated.get("pages", []))
    if "detection_error_count" in previous:
        summary["detection_error_count"] = previous["detection_error_count"]
