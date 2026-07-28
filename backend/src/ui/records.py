"""Safe record rendering helpers that avoid dataframe/native table components."""

from __future__ import annotations

import json
from typing import Any, Iterable

import streamlit as st


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _record_title(record: dict[str, Any], title_keys: Iterable[str], index: int) -> str:
    for key in title_keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return f"记录 {index}"


def render_records(
    records: list[dict[str, Any]],
    *,
    title_keys: Iterable[str] = (),
    summary_keys: Iterable[str] = (),
    max_records: int = 50,
) -> None:
    """Render a list of records without pandas, pyarrow, st.table or st.dataframe."""
    if not records:
        return

    visible_records = records[:max_records]
    for index, record in enumerate(visible_records, start=1):
        title = _record_title(record, title_keys, index)
        keys = [key for key in summary_keys if key in record]
        if not keys:
            keys = [key for key in record if key not in set(title_keys)][:4]
        summary = " ｜ ".join(f"**{key}**：{_format_value(record.get(key))}" for key in keys)
        st.markdown(f"**{title}**  \n{summary}")
        with st.expander("查看详情", expanded=False):
            for key, value in record.items():
                st.text(f"{key}: {_format_value(value)}")

    remaining = len(records) - len(visible_records)
    if remaining > 0:
        st.caption(f"还有 {remaining} 条记录未在页面展开，请下载 CSV/JSON 查看完整结果。")
