"""Safety checks for optional model download helpers."""

from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from scripts.download_ocsr_models import _safe_extract_zip, _verify_sha256


def test_sha256_verification_rejects_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "model.bin"
    path.write_bytes(b"model")

    with pytest.raises(RuntimeError, match="SHA-256"):
        _verify_sha256(path, "0" * 64)


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "nope")

    with pytest.raises(RuntimeError, match="路径穿越"):
        _safe_extract_zip(archive, tmp_path / "out")


def test_safe_extract_zip_rejects_uncompressed_size_limit(tmp_path: Path) -> None:
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("large.bin", b"x" * 128)

    with pytest.raises(RuntimeError, match="安全上限"):
        _safe_extract_zip(archive, tmp_path / "out", max_uncompressed_bytes=16)
