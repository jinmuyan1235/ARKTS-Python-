"""Download official optional OCSR model weights."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MOLSCRIBE_REPO_ID = "yujieq/MolScribe"
MOLSCRIBE_DEFAULT_FILE = "swin_base_char_aux_1m.pth"
MOLSCRIBE_DEFAULT_REVISION = os.getenv("MOLSCRIBE_REVISION", "main")
DECIMER_MODEL_URLS = {
    "DECIMER": "https://zenodo.org/record/8300489/files/models.zip",
    "DECIMER_HandDrawn": "https://zenodo.org/records/10781330/files/DECIMER_HandDrawn_model.zip",
}
def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except (AttributeError, ValueError):
        return default


MAX_MODEL_DOWNLOAD_BYTES = _env_int("OCSR_MODEL_MAX_DOWNLOAD_BYTES", 4 * 1024 * 1024 * 1024)
MAX_ZIP_UNCOMPRESSED_BYTES = _env_int("OCSR_MODEL_MAX_ZIP_UNCOMPRESSED_BYTES", 8 * 1024 * 1024 * 1024)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256(path: Path, expected_sha256: str | None) -> str:
    digest = _sha256_path(path)
    if expected_sha256 and digest.lower() != expected_sha256.strip().lower():
        raise RuntimeError(
            f"SHA-256 校验失败：{path.name} 实际 {digest}，期望 {expected_sha256.strip().lower()}。"
        )
    return digest


def _manifest_path() -> Path:
    return PROJECT_ROOT / "models" / "manifest.json"


def _write_manifest_entry(entry: dict[str, object]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    else:
        manifest = {}
    records = manifest.setdefault("downloads", [])
    if not isinstance(records, list):
        records = []
        manifest["downloads"] = records
    records.append({**entry, "recorded_at": datetime.now(timezone.utc).isoformat()})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _atomic_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def download_molscribe(
    filename: str = MOLSCRIBE_DEFAULT_FILE,
    revision: str = MOLSCRIBE_DEFAULT_REVISION,
    expected_sha256: str | None = None,
) -> dict[str, str | None]:
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:
        raise RuntimeError("缺少 huggingface_hub，请先运行 setup_gpu_environment.sh 或 pip install huggingface_hub。") from exc
    model_dir = PROJECT_ROOT / "models" / "molscribe"
    model_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="molscribe_download_", dir=model_dir) as temporary_dir:
        downloaded = Path(
            hf_hub_download(
                repo_id=MOLSCRIBE_REPO_ID,
                filename=filename,
                revision=revision,
                local_dir=temporary_dir,
            )
        )
        digest = _verify_sha256(downloaded, expected_sha256)
        path = model_dir / Path(filename).name
        _atomic_replace(downloaded, path)
    if digest:
        path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    result = {
        "backend": "molscribe",
        "source": f"https://huggingface.co/{MOLSCRIBE_REPO_ID}",
        "repo_id": MOLSCRIBE_REPO_ID,
        "revision": revision,
        "filename": filename,
        "path": str(path.resolve()),
        "sha256": digest,
        "expected_sha256": expected_sha256,
    }
    _write_manifest_entry({
        **result,
        "license": "See upstream Hugging Face model card.",
        "summary": "MolScribe optional OCSR model weight.",
    })
    return {
        key: str(value) if value is not None else None
        for key, value in result.items()
    }


def _download_file(url: str, path: Path, max_bytes: int = MAX_MODEL_DOWNLOAD_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    downloaded = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url)
    if downloaded:
        request.add_header("Range", f"bytes={downloaded}-")
    mode = "ab" if downloaded else "wb"
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open(mode) as handle:
            if downloaded and getattr(response, "status", None) != 206:
                handle.close()
                partial.write_bytes(b"")
                downloaded = 0
                mode = "wb"
                with urllib.request.urlopen(url, timeout=120) as restarted, partial.open(mode) as restarted_handle:
                    while True:
                        chunk = restarted.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise RuntimeError(f"下载文件超过安全上限：{max_bytes} bytes")
                        restarted_handle.write(chunk)
                partial.replace(path)
                return
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise RuntimeError(f"下载文件超过安全上限：{max_bytes} bytes")
                handle.write(chunk)
        partial.replace(path)
    except Exception:
        if partial.exists() and partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
        raise


def _safe_extract_zip(archive_path: Path, destination: Path, max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES) -> list[str]:
    destination = destination.expanduser().resolve()
    extracted: list[str] = []
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        for member in members:
            total_size += int(member.file_size)
            if total_size > max_uncompressed_bytes:
                raise RuntimeError(f"ZIP 解压后大小超过安全上限：{max_uncompressed_bytes} bytes")
            target = (destination / member.filename).resolve()
            if destination not in (target, *target.parents):
                raise RuntimeError(f"ZIP 包含路径穿越条目：{member.filename}")
        temporary = destination / f".extracting_{archive_path.stem}"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        try:
            for member in members:
                target = (temporary / member.filename).resolve()
                if temporary not in (target, *target.parents):
                    raise RuntimeError(f"ZIP 包含路径穿越条目：{member.filename}")
                archive.extract(member, temporary)
                extracted.append(member.filename)
            for item in temporary.iterdir():
                target = destination / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                item.replace(target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    return extracted


def _expected_decimer_sha(model_name: str) -> str | None:
    env_name = f"{model_name.upper()}_ZIP_SHA256".replace("-", "_")
    return os.getenv(env_name) or os.getenv("DECIMER_ZIP_SHA256")


def download_decimer_models(force: bool = False, expected_sha256: dict[str, str | None] | None = None) -> dict[str, object]:
    cache_dir = Path.home() / ".data" / "DECIMER-V2"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {"cache_dir": str(cache_dir), "models": {}}
    model_results: dict[str, object] = {}
    for model_name, url in DECIMER_MODEL_URLS.items():
        target_dir = cache_dir / f"{model_name}_model"
        zip_name = "models.zip" if model_name == "DECIMER" else f"{model_name}_model.zip"
        zip_path = cache_dir / zip_name
        expected = (expected_sha256 or {}).get(model_name) or _expected_decimer_sha(model_name)
        if force or not (target_dir / "saved_model.pb").is_file():
            _download_file(url, zip_path)
            digest = _verify_sha256(zip_path, expected)
            extracted = _safe_extract_zip(zip_path, cache_dir)
            zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
            zip_path.unlink(missing_ok=True)
        else:
            digest = None
            extracted = []
        saved_model = target_dir / "saved_model.pb"
        item = {
            "source": url,
            "path": str(target_dir),
            "exists": saved_model.is_file(),
            "saved_model_size": saved_model.stat().st_size if saved_model.is_file() else None,
            "zip_sha256": digest,
            "expected_zip_sha256": expected,
            "extracted_entries": extracted[:20],
        }
        model_results[model_name] = item
        _write_manifest_entry({
            "backend": model_name,
            "source": url,
            "path": str(target_dir),
            "zip_sha256": digest,
            "expected_zip_sha256": expected,
            "license": "See upstream Zenodo record.",
            "summary": f"{model_name} optional OCSR model package.",
        })
    results["models"] = model_results
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Download official OCSR model weights.")
    parser.add_argument("--molscribe-file", default=MOLSCRIBE_DEFAULT_FILE)
    parser.add_argument("--molscribe-revision", default=MOLSCRIBE_DEFAULT_REVISION)
    parser.add_argument("--molscribe-sha256", default=os.getenv("MOLSCRIBE_MODEL_SHA256"))
    parser.add_argument("--decimer-sha256", default=os.getenv("DECIMER_ZIP_SHA256"))
    parser.add_argument("--decimer-handdrawn-sha256", default=os.getenv("DECIMER_HANDDRAWN_ZIP_SHA256"))
    parser.add_argument("--skip-molscribe", action="store_true")
    parser.add_argument("--skip-decimer", action="store_true")
    parser.add_argument("--force-decimer", action="store_true")
    args = parser.parse_args()
    result: dict[str, object] = {}
    if not args.skip_molscribe:
        result["molscribe"] = download_molscribe(args.molscribe_file, args.molscribe_revision, args.molscribe_sha256)
    if not args.skip_decimer:
        result["decimer"] = download_decimer_models(
            force=args.force_decimer,
            expected_sha256={
                "DECIMER": args.decimer_sha256,
                "DECIMER_HandDrawn": args.decimer_handdrawn_sha256,
            },
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
