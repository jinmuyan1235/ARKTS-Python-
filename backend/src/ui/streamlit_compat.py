"""Small Streamlit compatibility helpers."""

from __future__ import annotations

from typing import Any

import streamlit as st


def dataframe_stretch(data: Any, **kwargs: Any) -> Any:
    """Render a dataframe full-width on new Streamlit with a legacy fallback."""
    try:
        return st.dataframe(data, width="stretch", **kwargs)
    except TypeError:
        return st.dataframe(data, use_container_width=True, **kwargs)


def image_stretch(image: Any, **kwargs: Any) -> Any:
    """Render an image full-width on new Streamlit with a legacy fallback."""
    try:
        return st.image(image, width="stretch", **kwargs)
    except TypeError:
        return st.image(image, use_container_width=True, **kwargs)


def segmented_control(label: str, options: list[str], default: str, key: str, **kwargs: Any) -> str:
    """Render a segmented control with a radio fallback for older Streamlit builds."""
    if default not in options:
        default = options[0]
    try:
        value = st.segmented_control(label, options, default=default, key=key, **kwargs)
        return str(value or default)
    except (AttributeError, TypeError):
        return str(
            st.radio(
                label,
                options,
                index=options.index(default),
                horizontal=True,
                key=key,
                **{name: value for name, value in kwargs.items() if name != "label_visibility"},
            )
        )
