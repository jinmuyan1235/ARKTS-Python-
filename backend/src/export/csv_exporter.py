"""CSV exporter for flattened batch results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


def save_csv(rows: Iterable[Mapping[str, Any]] | pd.DataFrame, output_path: str | Path) -> str:
    """Save rows as UTF-8 BOM CSV for reliable spreadsheet display."""
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    frame.to_csv(destination, index=False, encoding="utf-8-sig")
    return str(destination)
