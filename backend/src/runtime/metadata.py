"""Lightweight runtime and artifact metadata for reports."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.metadata
import platform
from pathlib import Path
import subprocess
import sys
from typing import Any

import config


DEPENDENCY_PACKAGES: dict[str, tuple[str, ...]] = {
    "rdkit": ("rdkit",),
    "opencv": ("opencv-python-headless", "opencv-python"),
    "pillow": ("Pillow",),
    "numpy": ("numpy",),
    "pandas": ("pandas",),
    "scikit-learn": ("scikit-learn",),
    "streamlit": ("streamlit",),
    "torch": ("torch",),
    "tensorflow": ("tensorflow", "tensorflow-cpu"),
    "molscribe": ("molscribe", "MolScribe"),
    "decimer": ("decimer", "DECIMER"),
}


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    """Return the current Git commit SHA when available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _package_version(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        try:
            return importlib.metadata.version(candidate)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


@lru_cache(maxsize=1)
def dependency_versions() -> dict[str, str | None]:
    """Return installed package versions without importing heavy ML libraries."""
    return {name: _package_version(candidates) for name, candidates in DEPENDENCY_PACKAGES.items()}


@lru_cache(maxsize=32)
def _sha256_cached(path: str, size: int, mtime_ns: int) -> str | None:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def sha256_file(path: str | Path) -> str | None:
    """Return a SHA-256 digest for a local file, cached by path/size/mtime."""
    try:
        file_path = Path(path).expanduser().resolve()
        stat = file_path.stat()
        if not file_path.is_file():
            return None
        return _sha256_cached(str(file_path), int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        return None


def report_runtime_metadata() -> dict[str, Any]:
    """Return runtime metadata embedded into every generated report."""
    return {
        "app_mode": config.APP_MODE,
        "git_commit": git_commit(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "dependency_versions": dependency_versions(),
    }
