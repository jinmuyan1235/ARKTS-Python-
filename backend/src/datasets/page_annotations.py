"""Independent page-level bounding-box ground truth storage."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAGE_REGION_CLASSES = (
    "molecule", "reaction", "multiple_molecules", "text", "table",
    "figure", "logo", "ignore",
)


class PageAnnotationStore:
    """Persist one atomic annotation record per page, separate from crop reviews."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.manifest_path = self.root / "manifest.csv"
        self.protocol_path = self.root / "protocol.json"
        self.annotations_path = self.root / "annotations.json"
        if not self.annotations_path.is_file():
            raise FileNotFoundError(f"Page annotation workspace is not prepared: {self.annotations_path}")

    def load(self) -> dict[str, Any]:
        return json.loads(self.annotations_path.read_text(encoding="utf-8"))

    def page(self, page_id: str) -> dict[str, Any]:
        payload = self.load()
        try:
            return deepcopy(payload["pages"][page_id])
        except KeyError as exc:
            raise KeyError(f"Unknown annotation page: {page_id}") from exc

    def page_ids(self) -> list[str]:
        return sorted(self.load().get("pages", {}))

    def save_page(
        self,
        page_id: str,
        annotations: list[dict[str, Any]],
        *,
        annotator: str = "",
        layout_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self.load()
        if page_id not in payload.get("pages", {}):
            raise KeyError(f"Unknown annotation page: {page_id}")
        normalized = [self._normalize_box(item, index) for index, item in enumerate(annotations, start=1)]
        page = payload["pages"][page_id]
        width, height = int(page["width"]), int(page["height"])
        if any(item["bbox"][2] > width or item["bbox"][3] > height for item in normalized):
            raise ValueError(f"Annotation bbox exceeds page bounds {width}x{height}.")
        page["annotations"] = normalized
        page["annotation_status"] = "completed"
        page["annotator"] = str(annotator).strip()
        page["layout_tags"] = sorted(set(layout_tags or []))
        page["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = self.annotations_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.annotations_path)
        return deepcopy(page)

    @staticmethod
    def add_box(annotations: list[dict[str, Any]], bbox: list[int], region_class: str) -> list[dict[str, Any]]:
        result = deepcopy(annotations)
        result.append({"bbox": bbox, "class": region_class})
        return result

    @staticmethod
    def update_box(
        annotations: list[dict[str, Any]], index: int, *, bbox: list[int] | None = None,
        region_class: str | None = None,
    ) -> list[dict[str, Any]]:
        result = deepcopy(annotations)
        if bbox is not None:
            result[index]["bbox"] = bbox
        if region_class is not None:
            result[index]["class"] = region_class
        return result

    @staticmethod
    def delete_box(annotations: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
        result = deepcopy(annotations)
        del result[index]
        return result

    @staticmethod
    def _normalize_box(item: dict[str, Any], index: int) -> dict[str, Any]:
        region_class = str(item.get("class") or item.get("region_class") or "").strip()
        if region_class not in PAGE_REGION_CLASSES:
            raise ValueError(f"Invalid page region class: {region_class}")
        raw_bbox = item.get("bbox")
        if raw_bbox is None:
            raw_bbox = [item.get(key) for key in ("x1", "y1", "x2", "y2")]
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            raise ValueError("Each annotation must contain bbox=[x1,y1,x2,y2].")
        x1, y1, x2, y2 = [int(value) for value in raw_bbox]
        if x2 <= x1 or y2 <= y1 or min(x1, y1) < 0:
            raise ValueError(f"Invalid bbox: {[x1, y1, x2, y2]}")
        return {
            "annotation_id": str(item.get("annotation_id") or f"a{index:04d}"),
            "bbox": [x1, y1, x2, y2],
            "class": region_class,
        }
