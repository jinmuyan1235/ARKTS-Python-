"""Cached Streamlit state and service factories."""

from __future__ import annotations

from typing import Any

import streamlit as st

from config import DATA_DIR, OUTPUT_DIR
from src.analysis.batch_analyzer import BatchAnalyzer
from src.analysis.molecule_report import MoleculeReportGenerator
from src.documents.processor import DocumentOCSRProcessor
from src.runtime.gpu_manager import gpu_selection_options


RuntimeKey = tuple[str, str, str | None]


def runtime_key_from_selection(selection: str | None = None) -> RuntimeKey:
    """Return a hashable runtime key for cache separation."""
    selected = selection or st.session_state.get("gpu_device_selection", "auto")
    options = {option["value"]: option for option in gpu_selection_options()}
    option = options.get(selected, options["auto"])
    return (
        str(option["molscribe_device"]),
        str(option["decimer_device"]),
        option["visible_gpu_index"],
    )


def runtime_config_from_key(runtime_key: RuntimeKey) -> dict[str, Any]:
    """Convert a cache key into adapter runtime configuration."""
    molscribe_device, decimer_device, visible_gpu_index = runtime_key
    return {
        "molscribe_device": molscribe_device,
        "decimer_device": decimer_device,
        "visible_gpu_index": visible_gpu_index,
    }


def current_runtime_key() -> RuntimeKey:
    """Return the active user-selected runtime key."""
    return runtime_key_from_selection()


@st.cache_resource(show_spinner=False)
def _get_report_generator(backend: str, runtime_key: RuntimeKey) -> MoleculeReportGenerator:
    return MoleculeReportGenerator(backend, OUTPUT_DIR, runtime_config=runtime_config_from_key(runtime_key))


def get_report_generator(backend: str) -> MoleculeReportGenerator:
    return _get_report_generator(backend, current_runtime_key())


@st.cache_resource(show_spinner=False)
def _get_batch_analyzer(backend: str, runtime_key: RuntimeKey) -> BatchAnalyzer:
    return BatchAnalyzer(backend, OUTPUT_DIR, runtime_config=runtime_config_from_key(runtime_key))


def get_batch_analyzer(backend: str) -> BatchAnalyzer:
    return _get_batch_analyzer(backend, current_runtime_key())


@st.cache_resource(show_spinner=False)
def _get_document_processor(backend: str, runtime_key: RuntimeKey) -> DocumentOCSRProcessor:
    return DocumentOCSRProcessor(
        backend=backend,
        runtime_config=runtime_config_from_key(runtime_key),
        review_output_dir=DATA_DIR,
    )


def get_document_processor(backend: str) -> DocumentOCSRProcessor:
    return _get_document_processor(backend, current_runtime_key())


@st.cache_data(show_spinner=False, ttl=10)
def _get_backend_statuses(runtime_key: RuntimeKey) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for backend in ("demo", "molscribe", "decimer", "ensemble"):
        try:
            statuses[backend] = _get_report_generator(backend, runtime_key).recognizer.status()
        except Exception as exc:
            statuses[backend] = {
                "backend": backend,
                "available": False,
                "message": str(exc),
            }
    return statuses


def get_backend_statuses() -> dict[str, dict[str, Any]]:
    return _get_backend_statuses(current_runtime_key())


def remember_backend_status(backend: str) -> None:
    try:
        st.session_state["backend_last_status"] = get_report_generator(backend).recognizer.status()
    except Exception:
        return


def merged_backend_status(backend: str) -> dict[str, Any]:
    status = dict(get_backend_statuses().get(backend, {"backend": backend, "available": False}))
    latest = st.session_state.get("backend_last_status") or {}
    if latest.get("backend") == backend:
        status.update({key: value for key, value in latest.items() if value is not None})
    return status
