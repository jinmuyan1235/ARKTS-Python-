"""Human region-edit helpers with audit records."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.documents.models import normalize_bbox


REACTION_TYPES = {"reaction", "reaction_arrow", "reaction_condition", "reaction_like"}
IGNORE_TYPES = {"ignore", "non_molecule", "figure", "unknown"}


def _audit(operation: str, before: dict[str, Any] | None, after: dict[str, Any] | None, note: str | None = None) -> dict[str, Any]:
    return {
        "operation": operation,
        "before": before,
        "after": after,
        "note": note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "user",
    }


def is_region_confirmed(region: dict[str, Any]) -> bool:
    """Return whether a region has been explicitly accepted for downstream use."""
    return bool(region.get("confirmed")) or str(region.get("annotation_status") or "").lower() == "confirmed"


def document_detection_label(region_type: str | None) -> str:
    """Map internal document region types to compact detection-training labels."""
    normalized = str(region_type or "unknown").strip().lower()
    if normalized == "molecule":
        return "molecule"
    if normalized == "text":
        return "text"
    if normalized == "table":
        return "table"
    if normalized in REACTION_TYPES:
        return "reaction"
    return "ignore"


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "confirmed"}


def _region_snapshot(region: dict[str, Any]) -> dict[str, Any]:
    return {
        "region_id": region.get("region_id"),
        "bbox": region.get("bbox"),
        "region_type": region.get("region_type"),
        "status": region.get("status"),
        "confirmed": bool(region.get("confirmed")),
    }


def _clear_region_outputs(region: dict[str, Any]) -> None:
    region["crop_path"] = None
    region["ocsr"] = {}
    region["final_result"] = {}
    region["report"] = None
    region["screening"] = {}
    region["review"] = {}
    region["processing_time_ms"] = None


def _set_confirmation(region: dict[str, Any], confirmed: bool, status: str | None = None) -> None:
    region["confirmed"] = bool(confirmed)
    region["annotation_status"] = "confirmed" if confirmed else "pending"
    if status is not None:
        region["status"] = status


def _new_region_id(regions: list[dict[str, Any]], page_number: int, suffix: str = "user") -> str:
    existing = [region for region in regions if int(region.get("page_number", 0)) == page_number]
    used = {str(region.get("region_id")) for region in regions}
    index = len(existing) + 1
    while True:
        candidate = f"p{page_number:03d}_r{index:03d}_{suffix}"
        if candidate not in used:
            return candidate
        index += 1


def _split_bbox(
    bbox: list[int] | tuple[int, int, int, int],
    direction: str,
    split_at: int | float | None,
) -> tuple[list[int], list[int]]:
    x1, y1, x2, y2 = [int(value) for value in bbox]
    direction = str(direction or "vertical").strip().lower()
    if direction not in {"vertical", "horizontal"}:
        raise ValueError("split direction must be vertical or horizontal.")
    if direction == "vertical":
        if split_at is None:
            split = (x1 + x2) // 2
        elif isinstance(split_at, float) and 0 < split_at < 1:
            split = int(round(x1 + (x2 - x1) * split_at))
        else:
            split = int(round(float(split_at)))
        if split <= x1 or split >= x2:
            raise ValueError("vertical split must fall inside the region bbox.")
        return [x1, y1, split, y2], [split, y1, x2, y2]
    if split_at is None:
        split = (y1 + y2) // 2
    elif isinstance(split_at, float) and 0 < split_at < 1:
        split = int(round(y1 + (y2 - y1) * split_at))
    else:
        split = int(round(float(split_at)))
    if split <= y1 or split >= y2:
        raise ValueError("horizontal split must fall inside the region bbox.")
    return [x1, y1, x2, split], [x1, split, x2, y2]


def apply_region_edits(document_result: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply add/update/delete/mark edits without mutating the original result."""
    updated = deepcopy(document_result)
    pages_by_number = {int(page["page_number"]): page for page in updated.get("pages", [])}
    regions = list(updated.get("regions", []))

    for edit in edits:
        action = str(edit.get("action", "")).strip().lower()
        region_id = edit.get("region_id")
        if action == "merge" and region_id is None:
            merge_region_ids = list(edit.get("region_ids") or [])
            region_id = merge_region_ids[0] if merge_region_ids else None
        if action == "confirm_page":
            page_number = edit.get("page_number")
            if page_number is None and region_id:
                region_for_page = next((region for region in regions if region.get("region_id") == region_id), None)
                if region_for_page is None:
                    raise ValueError(f"Unknown region_id for edit: {region_id}")
                page_number = region_for_page.get("page_number")
            if page_number is None:
                raise ValueError("confirm_page requires page_number or region_id.")
            page_regions = [
                region for region in regions
                if int(region.get("page_number", 0)) == int(page_number) and region.get("status") != "deleted"
            ]
            for region in page_regions:
                page_before = _region_snapshot(region)
                _set_confirmation(region, True, "confirmed")
                region.setdefault("audit", []).append(
                    _audit("confirm_page", page_before, _region_snapshot(region), edit.get("note"))
                )
            continue
        if action == "add":
            page_number = int(edit["page_number"])
            page = pages_by_number[page_number]
            bbox = normalize_bbox(edit["bbox"], int(page["width"]), int(page["height"]))
            confirmed = _bool(edit.get("confirmed"), False)
            new_id = edit.get("new_region_id") or _new_region_id(regions, page_number)
            region = {
                "document_id": updated["document_id"],
                "page_number": page_number,
                "region_id": new_id,
                "bbox": list(bbox),
                "region_type": edit.get("region_type", "molecule"),
                "detection_confidence": None,
                "crop_path": None,
                "source": "user",
                "detector_name": None,
                "status": "confirmed" if confirmed else "edited",
                "confirmed": confirmed,
                "annotation_status": "confirmed" if confirmed else "pending",
                "message": edit.get("note") or "Added by user.",
                "audit": [_audit("add", None, {"bbox": list(bbox), "region_type": edit.get("region_type", "molecule")}, edit.get("note"))],
                "ocsr": {},
                "final_result": {},
                "report": None,
                "screening": {},
                "review": {},
                "processing_time_ms": None,
            }
            regions.append(region)
            continue

        target = next((region for region in regions if region.get("region_id") == region_id), None)
        if target is None:
            raise ValueError(f"Unknown region_id for edit: {region_id}")
        before = _region_snapshot(target)
        if action == "delete":
            target["status"] = "deleted"
            target["confirmed"] = False
            target["annotation_status"] = "deleted"
            target["region_type"] = "non_molecule"
            target.setdefault("audit", []).append(_audit("delete", before, {"status": "deleted"}, edit.get("note")))
        elif action == "mark":
            target["region_type"] = edit.get("region_type", "non_molecule")
            confirmed = _bool(edit.get("confirmed"), is_region_confirmed(target))
            _set_confirmation(target, confirmed, "confirmed" if confirmed else "edited")
            _clear_region_outputs(target)
            target.setdefault("audit", []).append(
                _audit("mark", before, _region_snapshot(target), edit.get("note"))
            )
        elif action == "update":
            page = pages_by_number[int(target["page_number"])]
            target["bbox"] = list(normalize_bbox(edit.get("bbox", target["bbox"]), int(page["width"]), int(page["height"])))
            if edit.get("region_type"):
                target["region_type"] = edit["region_type"]
            confirmed = _bool(edit.get("confirmed"), is_region_confirmed(target))
            _set_confirmation(target, confirmed, "confirmed" if confirmed else "edited")
            _clear_region_outputs(target)
            target.setdefault("audit", []).append(
                _audit("update_bbox", before, _region_snapshot(target), edit.get("note"))
            )
        elif action == "confirm":
            if edit.get("region_type"):
                target["region_type"] = edit["region_type"]
            _set_confirmation(target, True, "confirmed")
            target.setdefault("audit", []).append(
                _audit("confirm", before, _region_snapshot(target), edit.get("note"))
            )
        elif action == "recognize":
            if target.get("region_type") != "molecule" or not is_region_confirmed(target):
                raise ValueError("recognize requires a confirmed molecule region.")
            target["status"] = "confirmed"
            target.setdefault("audit", []).append(
                _audit("recognize_requested", before, _region_snapshot(target), edit.get("note"))
            )
        elif action == "unconfirm":
            _set_confirmation(target, False, "edited")
            _clear_region_outputs(target)
            target.setdefault("audit", []).append(
                _audit("unconfirm", before, _region_snapshot(target), edit.get("note"))
            )
        elif action == "merge":
            region_ids = [str(item) for item in (edit.get("region_ids") or [])]
            if region_id and str(region_id) not in region_ids:
                region_ids.insert(0, str(region_id))
            selected = [region for region in regions if str(region.get("region_id")) in set(region_ids)]
            if len(selected) < 2:
                raise ValueError("merge requires at least two regions.")
            page_numbers = {int(region.get("page_number", 0)) for region in selected}
            if len(page_numbers) != 1:
                raise ValueError("merge requires regions from the same page.")
            x1 = min(int(region["bbox"][0]) for region in selected)
            y1 = min(int(region["bbox"][1]) for region in selected)
            x2 = max(int(region["bbox"][2]) for region in selected)
            y2 = max(int(region["bbox"][3]) for region in selected)
            region_types = {str(region.get("region_type") or "unknown") for region in selected}
            target = selected[0]
            merge_before = [_region_snapshot(region) for region in selected]
            target["bbox"] = [x1, y1, x2, y2]
            target["region_type"] = edit.get("region_type") or (region_types.pop() if len(region_types) == 1 else "unknown")
            _set_confirmation(target, _bool(edit.get("confirmed"), False), "confirmed" if _bool(edit.get("confirmed"), False) else "edited")
            target["message"] = edit.get("note") or f"Merged {len(selected)} regions."
            _clear_region_outputs(target)
            target.setdefault("audit", []).append(
                _audit("merge", {"regions": merge_before}, _region_snapshot(target), edit.get("note"))
            )
            for merged in selected[1:]:
                merged_before = _region_snapshot(merged)
                merged["status"] = "deleted"
                merged["confirmed"] = False
                merged["annotation_status"] = "merged"
                merged.setdefault("audit", []).append(
                    _audit("merged_into", merged_before, {"merged_into": target.get("region_id")}, edit.get("note"))
                )
        elif action == "split":
            page = pages_by_number[int(target["page_number"])]
            bbox = normalize_bbox(target.get("bbox", []), int(page["width"]), int(page["height"]))
            if edit.get("bbox_a") and edit.get("bbox_b"):
                bbox_a = list(normalize_bbox(edit["bbox_a"], int(page["width"]), int(page["height"])))
                bbox_b = list(normalize_bbox(edit["bbox_b"], int(page["width"]), int(page["height"])))
            else:
                bbox_a, bbox_b = _split_bbox(list(bbox), str(edit.get("direction") or "vertical"), edit.get("split_at"))
            split_type = edit.get("region_type") or target.get("region_type") or "molecule"
            confirmed = _bool(edit.get("confirmed"), False)
            target["bbox"] = bbox_a
            target["region_type"] = split_type
            _set_confirmation(target, confirmed, "confirmed" if confirmed else "edited")
            _clear_region_outputs(target)
            target.setdefault("audit", []).append(
                _audit("split_primary", before, _region_snapshot(target), edit.get("note"))
            )
            new_id = edit.get("new_region_id") or _new_region_id(regions, int(target["page_number"]), "split")
            new_region = {
                "document_id": updated["document_id"],
                "page_number": int(target["page_number"]),
                "region_id": new_id,
                "bbox": bbox_b,
                "region_type": split_type,
                "detection_confidence": None,
                "crop_path": None,
                "source": "user",
                "detector_name": None,
                "status": "confirmed" if confirmed else "edited",
                "confirmed": confirmed,
                "annotation_status": "confirmed" if confirmed else "pending",
                "message": edit.get("note") or f"Split from {target.get('region_id')}.",
                "audit": [_audit("split_secondary", before, {"bbox": bbox_b, "region_type": split_type, "confirmed": confirmed}, edit.get("note"))],
                "ocsr": {},
                "final_result": {},
                "report": None,
                "screening": {},
                "review": {},
                "processing_time_ms": None,
            }
            regions.append(new_region)
        elif action == "noop":
            continue
        elif action == "":
            raise ValueError("Region edit action is required.")
        else:
            raise ValueError(f"Unsupported region edit action: {action}")

    updated["regions"] = regions
    updated["summary"] = summarize_regions(regions)
    return updated


def summarize_regions(regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize region statuses and types for document-level reporting."""
    active = [region for region in regions if region.get("status") != "deleted"]
    molecule_regions = [region for region in active if region.get("region_type") == "molecule"]
    confirmed = [region for region in active if is_region_confirmed(region)]
    confirmed_molecules = [region for region in molecule_regions if is_region_confirmed(region)]
    recognized = [region for region in molecule_regions if (region.get("report") or {}).get("status") == "success"]
    failed = [region for region in molecule_regions if region.get("report") and (region.get("report") or {}).get("status") != "success"]
    queued_for_review = [region for region in active if (region.get("review") or {}).get("queued")]
    by_type: dict[str, int] = {}
    for region in active:
        region_type = str(region.get("region_type") or "unknown")
        by_type[region_type] = by_type.get(region_type, 0) + 1
    return {
        "page_count": None,
        "region_count": len(active),
        "molecule_region_count": len(molecule_regions),
        "confirmed_region_count": len(confirmed),
        "confirmed_molecule_region_count": len(confirmed_molecules),
        "pending_region_count": max(0, len(active) - len(confirmed)),
        "recognized_region_count": len(recognized),
        "failed_region_count": len(failed),
        "review_queue_count": len(queued_for_review),
        "region_type_distribution": by_type,
    }
