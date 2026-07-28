"""Serializable data structures for document page and region workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

RegionType = Literal[
    "molecule",
    "reaction",
    "reaction_arrow",
    "reaction_condition",
    "reaction_like",
    "text",
    "table",
    "figure",
    "figure_label",
    "multiple_molecules",
    "blank",
    "invalid_crop",
    "ignore",
    "unknown",
    "non_molecule",
]


@dataclass
class DocumentPage:
    """A rendered document page or uploaded page image."""

    document_id: str
    page_number: int
    image_path: str
    width: int
    height: int
    source_path: str | None = None
    source_type: str = "image"
    render_dpi: int | None = None
    page_label: str | None = None
    quality: dict[str, Any] = field(default_factory=dict)
    text_boxes: list[dict[str, Any]] = field(default_factory=list)
    figure_boxes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentRegion:
    """A detected or user-edited region that can be mapped back to a page."""

    document_id: str
    page_number: int
    region_id: str
    bbox: tuple[int, int, int, int]
    region_type: RegionType
    detection_confidence: float | None = None
    crop_path: str | None = None
    source: str = "detector"
    detector_name: str | None = None
    status: str = "detected"
    confirmed: bool = False
    message: str | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)
    ocsr: dict[str, Any] = field(default_factory=dict)
    final_result: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    screening: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    processing_time_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        return data


def normalize_bbox(bbox: tuple[int, int, int, int] | list[int], width: int, height: int) -> tuple[int, int, int, int]:
    """Clamp a bbox to page bounds and require a non-empty rectangle."""
    if len(bbox) != 4:
        raise ValueError("bbox must contain exactly four coordinates.")
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox must define a non-empty rectangle within the page.")
    return x1, y1, x2, y2


def relative_path(path: str | Path, root: str | Path) -> str:
    """Return a stable relative path when possible."""
    resolved = Path(path).expanduser().resolve()
    root_path = Path(root).expanduser().resolve()
    try:
        return str(resolved.relative_to(root_path))
    except ValueError:
        return str(resolved)
