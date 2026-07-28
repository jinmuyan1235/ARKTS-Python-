"""Validation and safe extraction helpers for batch image inputs."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import shutil
import zipfile
from typing import Any, Iterable

from PIL import Image

import config
from src.utils.file_utils import ensure_directory, safe_stem


BATCH_MAX_FILES = max(1, int(os.getenv("BATCH_MAX_FILES", "500")))
BATCH_MAX_FILE_SIZE_MB = max(0.1, float(os.getenv("BATCH_MAX_FILE_SIZE_MB", "20")))
BATCH_MAX_TOTAL_SIZE_MB = max(BATCH_MAX_FILE_SIZE_MB, float(os.getenv("BATCH_MAX_TOTAL_SIZE_MB", "500")))
BATCH_MAX_IMAGE_PIXELS = max(1, int(os.getenv("BATCH_MAX_IMAGE_PIXELS", str(config.DOCUMENT_MAX_PIXELS))))
BATCH_MIN_FREE_SPACE_MB = max(1.0, float(os.getenv("BATCH_MIN_FREE_SPACE_MB", "512")))
ZIP_MAX_COMPRESSION_RATIO = max(2.0, float(os.getenv("BATCH_ZIP_MAX_COMPRESSION_RATIO", "100")))


def inspect_batch_uploads(uploads: Iterable[tuple[str, bytes]]) -> dict[str, Any]:
    """Return validated image entries from images or ZIP uploads without writing files."""
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    total_bytes = 0
    for upload_index, (upload_name, content) in enumerate(uploads, start=1):
        name = str(upload_name or "upload")
        source_key = f"{upload_index}:{name}"
        suffix = Path(name).suffix.lower()
        if suffix == ".zip":
            zip_entries, zip_errors = _inspect_zip(name, content, source_prefix=source_key)
            entries.extend(zip_entries)
            errors.extend(zip_errors)
            total_bytes += sum(int(item["size_bytes"]) for item in zip_entries)
        else:
            entries.append(_inspect_image(name, content, source=source_key))
            total_bytes += len(content)

    if len(entries) > BATCH_MAX_FILES:
        errors.append(f"图片数量 {len(entries)} 超过上限 {BATCH_MAX_FILES}。")
    if total_bytes > _megabytes(BATCH_MAX_TOTAL_SIZE_MB):
        errors.append(f"解压后的图片总大小超过 {BATCH_MAX_TOTAL_SIZE_MB:g} MB。")

    first_by_hash: dict[str, str] = {}
    duplicate_count = 0
    for item in entries:
        digest = str(item.get("sha256") or "")
        if not digest or not item.get("valid"):
            continue
        if digest in first_by_hash:
            item["duplicate_of"] = first_by_hash[digest]
            duplicate_count += 1
        else:
            first_by_hash[digest] = str(item.get("name") or "")
    invalid = [item for item in entries if not item.get("valid")]
    errors.extend(str(item.get("message")) for item in invalid if item.get("message"))
    return {
        "entries": entries,
        "errors": list(dict.fromkeys(errors)),
        "total_files": len(entries),
        "valid_files": sum(bool(item.get("valid")) for item in entries),
        "duplicate_files": duplicate_count,
        "total_bytes": total_bytes,
        "limits": batch_input_limits(),
    }


def batch_upload_previews(uploads: Iterable[tuple[str, bytes]], limit: int = 12) -> list[tuple[str, bytes]]:
    """Return validated image bytes for a bounded thumbnail preview."""
    previews: list[tuple[str, bytes]] = []
    for upload_name, content in uploads:
        if len(previews) >= limit:
            break
        if Path(upload_name).suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    for info in archive.infolist():
                        if len(previews) >= limit:
                            break
                        if info.is_dir() or Path(info.filename).suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
                            continue
                        data = archive.read(info)
                        if _inspect_image(Path(info.filename).name, data, source=info.filename)["valid"]:
                            previews.append((f"{upload_name}/{info.filename}", data))
            except (zipfile.BadZipFile, OSError):
                continue
        elif _inspect_image(upload_name, content, source=upload_name)["valid"]:
            previews.append((upload_name, content))
    return previews


def extract_batch_uploads(
    uploads: Iterable[tuple[str, bytes]],
    destination: str | Path,
) -> tuple[list[Path], dict[str, Any]]:
    """Validate uploads and safely materialize all image entries for a batch worker."""
    upload_list = list(uploads)
    inspection = inspect_batch_uploads(upload_list)
    if inspection["errors"]:
        raise ValueError("批量输入校验失败：" + "；".join(inspection["errors"][:5]))
    if not inspection["entries"]:
        raise ValueError("没有找到可处理的 PNG/JPG/JPEG 图片。")
    output = ensure_directory(destination)
    _check_disk_space(output, int(inspection["total_bytes"]))
    content_by_source: dict[str, bytes] = {}
    for upload_index, (upload_name, content) in enumerate(upload_list, start=1):
        source_key = f"{upload_index}:{upload_name}"
        if Path(upload_name).suffix.lower() == ".zip":
            with zipfile.ZipFile(BytesIO(content)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or Path(info.filename).suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
                        continue
                    content_by_source[f"{source_key}/{info.filename}"] = archive.read(info)
        else:
            content_by_source[source_key] = content

    paths: list[Path] = []
    for index, item in enumerate(inspection["entries"], start=1):
        source = str(item["source"])
        content = content_by_source[source]
        suffix = str(item["extension"])
        stem = safe_stem(Path(str(item["name"])).stem, f"image_{index:04d}")
        path = output / f"{index:04d}_{stem}{suffix}"
        path.write_bytes(content)
        paths.append(path)
    return paths, inspection


def inspect_batch_folder(folder: str | Path) -> dict[str, Any]:
    """Validate a local folder recursively and return a compact input manifest."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"输入文件夹不存在或不是目录：{root}")
    candidates = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in config.SUPPORTED_IMAGE_EXTENSIONS),
        key=lambda path: str(path.relative_to(root)).lower(),
    )
    if len(candidates) > BATCH_MAX_FILES:
        raise ValueError(f"图片数量 {len(candidates)} 超过上限 {BATCH_MAX_FILES}。")
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for path in candidates:
        size = path.stat().st_size
        total_bytes += size
        if size > _megabytes(BATCH_MAX_FILE_SIZE_MB):
            raise ValueError(f"文件 {path.name} 超过单文件 {BATCH_MAX_FILE_SIZE_MB:g} MB 上限。")
        entries.append(_inspect_image(path.name, path.read_bytes(), source=str(path)))
    errors = [str(item["message"]) for item in entries if not item.get("valid")]
    if errors:
        raise ValueError("文件夹输入校验失败：" + "；".join(errors[:5]))
    if not entries:
        raise ValueError("文件夹中没有 PNG/JPG/JPEG 图片。")
    if total_bytes > _megabytes(BATCH_MAX_TOTAL_SIZE_MB):
        raise ValueError(f"图片总大小超过 {BATCH_MAX_TOTAL_SIZE_MB:g} MB。")
    _check_disk_space(root, total_bytes)
    return {
        "entries": entries,
        "errors": [],
        "total_files": len(entries),
        "valid_files": len(entries),
        "duplicate_files": len(entries) - len({str(item["sha256"]) for item in entries}),
        "total_bytes": total_bytes,
        "limits": batch_input_limits(),
    }


def batch_input_limits() -> dict[str, int | float]:
    return {
        "max_files": BATCH_MAX_FILES,
        "max_file_size_mb": BATCH_MAX_FILE_SIZE_MB,
        "max_total_size_mb": BATCH_MAX_TOTAL_SIZE_MB,
        "max_image_pixels": BATCH_MAX_IMAGE_PIXELS,
        "min_free_space_mb": BATCH_MIN_FREE_SPACE_MB,
    }


def check_batch_disk_space(path: str | Path, input_bytes: int) -> None:
    """Fail early when the batch output volume cannot safely hold generated artifacts."""
    _check_disk_space(ensure_directory(path), int(input_bytes))


def _inspect_zip(
    upload_name: str,
    content: bytes,
    *,
    source_prefix: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    if len(content) > _megabytes(BATCH_MAX_FILE_SIZE_MB):
        return [], [f"ZIP 文件 {upload_name} 超过 {BATCH_MAX_FILE_SIZE_MB:g} MB 上限。"]
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                pure = PurePosixPath(info.filename.replace("\\", "/"))
                if pure.is_absolute() or ".." in pure.parts:
                    errors.append(f"ZIP 包含不安全路径：{info.filename}")
                    continue
                if info.flag_bits & 0x1:
                    errors.append(f"ZIP 包含加密文件，无法处理：{info.filename}")
                    continue
                suffix = Path(info.filename).suffix.lower()
                if suffix not in config.SUPPORTED_IMAGE_EXTENSIONS:
                    continue
                if info.file_size > _megabytes(BATCH_MAX_FILE_SIZE_MB):
                    errors.append(f"ZIP 内文件 {info.filename} 超过 {BATCH_MAX_FILE_SIZE_MB:g} MB 上限。")
                    continue
                compressed = max(1, int(info.compress_size))
                if info.file_size / compressed > ZIP_MAX_COMPRESSION_RATIO:
                    errors.append(f"ZIP 内文件压缩比异常：{info.filename}")
                    continue
                data = archive.read(info)
                prefix = source_prefix or upload_name
                entries.append(_inspect_image(Path(info.filename).name, data, source=f"{prefix}/{info.filename}"))
    except (zipfile.BadZipFile, OSError) as exc:
        errors.append(f"ZIP 文件 {upload_name} 无法读取：{exc}")
    return entries, errors


def _inspect_image(name: str, content: bytes, *, source: str) -> dict[str, Any]:
    suffix = Path(name).suffix.lower()
    item: dict[str, Any] = {
        "name": name,
        "source": source,
        "extension": suffix,
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "format": "",
        "width": 0,
        "height": 0,
        "valid": False,
        "message": "",
        "duplicate_of": None,
    }
    if suffix not in config.SUPPORTED_IMAGE_EXTENSIONS:
        item["message"] = f"不支持的图片格式：{name}"
        return item
    if not content:
        item["message"] = f"文件为空：{name}"
        return item
    if len(content) > _megabytes(BATCH_MAX_FILE_SIZE_MB):
        item["message"] = f"文件 {name} 超过 {BATCH_MAX_FILE_SIZE_MB:g} MB 上限。"
        return item
    try:
        with Image.open(BytesIO(content)) as image:
            item["format"] = str(image.format or suffix.lstrip(".")).upper()
            item["width"], item["height"] = image.size
            if image.width * image.height > BATCH_MAX_IMAGE_PIXELS:
                item["message"] = f"图片 {name} 像素数超过 {BATCH_MAX_IMAGE_PIXELS} 上限。"
                return item
            image.verify()
    except Exception as exc:
        item["message"] = f"图片 {name} 无法解码：{exc}"
        return item
    item["valid"] = True
    item["message"] = "格式校验通过"
    return item


def _check_disk_space(path: Path, input_bytes: int) -> None:
    free = shutil.disk_usage(path).free
    required = max(_megabytes(BATCH_MIN_FREE_SPACE_MB), input_bytes * 3)
    if free < required:
        raise OSError(
            f"磁盘剩余空间不足：需要至少 {required / 1024 / 1024:.1f} MB，"
            f"当前约 {free / 1024 / 1024:.1f} MB。"
        )


def _megabytes(value: float) -> int:
    return int(value * 1024 * 1024)
