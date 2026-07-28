"""JSON report exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"无法序列化类型：{type(value).__name__}")


def to_json_text(data: Any, indent: int = 2) -> str:
    """Serialize data to readable UTF-8 JSON text."""
    return json.dumps(data, ensure_ascii=False, indent=indent, default=_json_default)


def save_json(data: Any, output_path: str | Path) -> str:
    """Save data as UTF-8 JSON and return the absolute output path."""
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(to_json_text(data), encoding="utf-8")
    return str(destination)
