"""Load and validate the frozen, machine-readable production capability statement."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import config


CAPABILITY_FILE = config.PROJECT_ROOT / "config" / "model_capabilities.json"
EXPECTED_PRIMARY_BACKEND = "decimer"
EXPECTED_DECIMER_PROFILE = "raw"


@lru_cache(maxsize=1)
def load_model_capabilities(path: str | Path = CAPABILITY_FILE) -> dict[str, Any]:
    """Return a validated capability document without consulting evaluation data."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("dataset") != "ocsr-trusted-v0.2":
        raise ValueError("model capabilities must reference ocsr-trusted-v0.2")
    if payload.get("dataset_role") != "used_external_holdout":
        raise ValueError("model capabilities must identify the dataset as a used external holdout")
    if payload.get("fine_tuning_enabled") is not False:
        raise ValueError("the project model fine-tuning phase is closed")
    defaults = payload.get("production_defaults") or {}
    if defaults.get("primary_ocsr_backend") != EXPECTED_PRIMARY_BACKEND:
        raise ValueError("DECIMER must remain the primary production OCSR backend")
    if defaults.get("decimer_profile") != EXPECTED_DECIMER_PROFILE:
        raise ValueError("the frozen DECIMER production profile must remain raw")
    if defaults.get("ensemble_enabled") is not False:
        raise ValueError("the experimental ensemble cannot be enabled by default")
    return payload


def model_capability(backend: str) -> dict[str, Any]:
    return dict((load_model_capabilities().get("models") or {}).get(backend.lower()) or {})


def capability_version() -> str:
    return str(load_model_capabilities()["capability_version"])
