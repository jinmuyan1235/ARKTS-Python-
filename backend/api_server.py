"""Local HTTP API used by the HarmonyOS ArkTS client.

The JSON API is protected by ``X-API-Key``.  Image resources are exposed
through signed ``/media`` URLs because ArkUI's Image component cannot attach
custom HTTP headers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
from threading import Lock
import time
from typing import Any, AsyncIterator, Callable, Iterable, Literal, Mapping
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import config
from src.analysis.correction import (
    apply_smiles_correction,
    confirm_structure,
    current_final_smiles,
    human_review_state,
    mark_structure_unable_to_confirm,
    revoke_structure_confirmation,
)
from src.analysis.molecule_report import MoleculeReportGenerator
from src.analysis.batch_analyzer import flatten_report
from src.export.mobile_export_center import (
    EXPORT_FORMATS,
    FORMAL_ONLY_FORMATS,
    build_batch_exports,
    build_single_exports,
)
from src.feedback.review_service import FeedbackReviewService
from src.chem.smiles_validator import validate_smiles
from src.documents.input_loader import DocumentInputError
from src.documents.mobile_store import MobileDocumentStore
from src.documents.processor import DocumentOCSRProcessor
from src.documents.region_editing import apply_region_edits, is_region_confirmed
from src.documents.region_review import persist_document_result_atomic
from src.runtime.run_store import (
    ImageRun,
    create_image_run_from_bytes,
    create_image_run_from_file,
    image_run_dir,
    report_output_dir,
    save_report_for_existing_run,
    save_run_report,
)
from src.runtime.batch_inputs import batch_input_limits
from src.runtime.batch_job_store import BatchJobStore
from src.runtime.job_registry import (
    cancel_batch_job,
    load_batch_job_result,
    pause_batch_job,
    refresh_batch_job,
    resume_batch_job,
    start_batch_job_from_uploads,
    start_batch_retry_job,
)
from src.runtime.mobile_export_store import MobileExportStore
from src.runtime.health import run_production_health_check
from src.storage.analysis_repository import AnalysisRepository, record_report, utc_now
from src.storage.auth_repository import AuthRepository
from src.utils.file_utils import safe_stem


MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp"}
AVATAR_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_AVATAR_BYTES = 3 * 1024 * 1024
MAX_DOCUMENT_BYTES = int(config.DOCUMENT_MAX_FILE_SIZE_MB * 1024 * 1024)
MOBILE_EXPORT_TTL_SECONDS = max(60, int(os.getenv("MOBILE_EXPORT_TTL_SECONDS", "300")))
ANALYSIS_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
JOB_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
INFERENCE_LOCK = Lock()
SAMPLE_LABELS = {
    "aspirin": "阿司匹林",
    "caffeine": "咖啡因",
    "benzene": "苯",
    "ethanol": "乙醇",
}


class SmilesRequest(BaseModel):
    smiles: str = Field(min_length=1, max_length=4096)


class FavoriteRequest(BaseModel):
    favorite: bool


class ReviewRequest(BaseModel):
    smiles: str | None = Field(default=None, max_length=4096)
    confirm: bool = True
    action: Literal["confirm", "unable_to_confirm", "revoke"] = "confirm"
    reason: Literal[
        "image_unclear",
        "structure_incomplete",
        "multiple_molecules",
        "model_result_unreliable",
        "other",
    ] | None = None
    note: str | None = Field(default=None, max_length=300)


class FeedbackReviewRequest(BaseModel):
    action: Literal["approve", "return", "reject", "duplicate", "license_unclear"]
    note: str = Field(default="", max_length=500)
    duplicate_of: str = Field(default="", alias="duplicateOf", max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(alias="displayName", min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="", max_length=40)
    account_type: Literal["user", "developer"] = Field(default="developer", alias="accountType")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    account_type: Literal["user", "developer"] | None = Field(default=None, alias="accountType")


class ProfileRequest(BaseModel):
    display_name: str = Field(alias="displayName", min_length=1, max_length=40)
    role: str = Field(default="", max_length=40)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(alias="oldPassword", min_length=1, max_length=128)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=128)


class DocumentRegionEditRequest(BaseModel):
    bbox: list[int]
    region_type: Literal[
        "molecule", "reaction_like", "text", "table", "figure", "unknown", "non_molecule"
    ] = Field(alias="regionType")
    confirmed: bool = False
    note: str | None = Field(default=None, max_length=300)


class DocumentRegionRecognizeRequest(BaseModel):
    # This explicit acknowledgement is the human gate before OCSR.  A region
    # merely classified as molecule by the detector is never sufficient.
    confirmed: bool
    note: str | None = Field(default=None, max_length=300)


def _configured_api_key() -> str:
    return os.environ.get("HARMONY_API_KEY", "").strip()


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = _configured_api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="服务端尚未配置 HARMONY_API_KEY。")
    # compare_digest(str, str) only accepts ASCII. Encoding both values keeps
    # constant-time comparison semantics and makes an accidental Unicode key
    # return a normal 401 instead of crashing the request dependency.
    supplied = (x_api_key or "").encode("utf-8")
    configured = expected.encode("utf-8")
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="API 密钥错误或缺失。")


def _bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def require_user(authorization: str | None = Header(default=None, alias="Authorization")) -> dict[str, Any]:
    token = _bearer_token(authorization)
    user = AuthRepository().user_for_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="登录状态无效或已过期，请重新登录。")
    return user


def require_developer(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if str(user.get("account_type") or "developer") != "developer":
        raise HTTPException(status_code=403, detail="此功能仅对开发者开放。")
    return user


def _is_developer(user: Mapping[str, Any]) -> bool:
    return str(user.get("account_type") or "developer") == "developer"


def _ensure_analysis_access(row: Mapping[str, Any], user: Mapping[str, Any]) -> None:
    if _is_developer(user):
        return
    if str(row.get("owner_user_id") or "") != str(user.get("user_id") or ""):
        raise HTTPException(status_code=404, detail="分析记录不存在或无权访问。")


def _media_token(kind: str, resource_id: str) -> str:
    key = _configured_api_key()
    return hmac.new(key.encode("utf-8"), f"{kind}:{resource_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _check_media_token(kind: str, resource_id: str, token: str) -> None:
    expected = _media_token(kind, resource_id)
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="媒体访问令牌无效。")


def _expiring_media_token(kind: str, resource_id: str, expires: int) -> str:
    key = _configured_api_key()
    payload = f"{kind}:{resource_id}:{expires}"
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _check_expiring_media_token(kind: str, resource_id: str, expires: int, token: str) -> None:
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="下载链接已过期，请返回导出中心刷新。")
    expected = _expiring_media_token(kind, resource_id, expires)
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="下载令牌无效。")


def _user_dto(user: dict[str, Any], request: Request) -> dict[str, Any]:
    user_id = str(user.get("user_id") or "")
    avatar_url = None
    if user.get("avatar_path") and user_id:
        url = request.url_for("media_user_avatar", user_id=user_id)
        avatar_url = str(url.include_query_params(
            token=_media_token("avatar", user_id),
            version=str(user.get("updated_at") or ""),
        ))
    return {
        "userId": user_id,
        "username": str(user.get("username") or ""),
        "displayName": str(user.get("display_name") or ""),
        "role": str(user.get("role") or ""),
        "accountType": str(user.get("account_type") or "developer"),
        "avatarUrl": avatar_url,
        "createdAt": str(user.get("created_at") or ""),
    }


def _auth_response(user: dict[str, Any], token: str, request: Request) -> dict[str, Any]:
    return {
        "token": token,
        "expiresInDays": 7,
        "user": _user_dto(user, request),
    }


def _clean_filename(filename: str | None, content_type: str | None) -> str:
    name = Path(filename or "molecule.png").name
    suffix = Path(name).suffix.lower()
    if suffix in config.SUPPORTED_IMAGE_EXTENSIONS:
        return name
    suffix_by_type = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    return f"molecule{suffix_by_type.get(content_type or '', '.png')}"


def _validate_image(payload: bytes) -> None:
    if not payload:
        raise HTTPException(status_code=400, detail="上传图片为空。")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 15 MB。")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="文件不是可识别的图片。") from exc


def _validate_document_upload(payload: bytes) -> tuple[str, str]:
    if not payload:
        raise HTTPException(status_code=400, detail="上传文档为空。")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文档不能超过 {config.DOCUMENT_MAX_FILE_SIZE_MB:g} MB。",
        )
    signatures: list[tuple[bytes, str, str]] = [
        (b"%PDF", ".pdf", "application/pdf"),
        (b"PK\x03\x04", ".zip", "application/zip"),
        (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
        (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    ]
    for signature, suffix, detected_content_type in signatures:
        if payload.startswith(signature):
            return suffix, detected_content_type
    raise HTTPException(status_code=415, detail="仅支持 PDF、ZIP、PNG 和 JPG 文档。")


def _number(block: dict[str, Any], key: str) -> float | int | None:
    value = block.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _admet_summary(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("admet")
    if not isinstance(value, dict):
        return {"status": "unavailable", "message": "ADMET 未启用或没有可用结果。", "items": []}
    items: list[dict[str, Any]] = []
    raw_items = value.get("predictions")
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                items.append({
                    "target": item.get("target") or item.get("name"),
                    "taskType": item.get("task_type") or item.get("taskType"),
                    "prediction": item.get("prediction"),
                    "probability": item.get("probability"),
                    "insideDomain": (item.get("applicability_domain") or {}).get("inside")
                    if isinstance(item.get("applicability_domain"), dict)
                    else item.get("insideDomain"),
                    "message": item.get("message"),
                })
    elif value.get("target") or value.get("prediction") is not None:
        items.append({
            "target": value.get("target"),
            "taskType": value.get("task_type"),
            "prediction": value.get("prediction"),
            "probability": value.get("probability"),
            "insideDomain": (value.get("applicability_domain") or {}).get("inside")
            if isinstance(value.get("applicability_domain"), dict)
            else None,
            "message": value.get("message"),
        })
    return {
        "status": str(value.get("status") or ("available" if items else "unavailable")),
        "message": str(value.get("message") or value.get("disclaimer") or ""),
        "items": items,
    }


def _structure_media_url(report: dict[str, Any], request: Request) -> str | None:
    analysis_id = str(report.get("analysis_id") or "")
    structure_path = (report.get("images") or {}).get("redrawn_molecule")
    if not structure_path or not ANALYSIS_ID_PATTERN.fullmatch(analysis_id):
        return None
    url = request.url_for("media_analysis_structure", analysis_id=analysis_id)
    return str(url.include_query_params(token=_media_token("analysis", analysis_id)))


def _managed_analysis_media_path(path: Path, report_root: Path) -> bool:
    """Allow report-local files and managed batch/run files, never arbitrary host paths."""
    for root in (report_root, Path(config.DATA_DIR).expanduser().resolve()):
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _source_media_url(
    report: dict[str, Any],
    request: Request,
    record: dict[str, Any] | None = None,
) -> str | None:
    analysis_id = str(report.get("analysis_id") or "")
    input_data = report.get("input") or {}
    if (
        input_data.get("type") != "image"
        or not input_data.get("path")
        or not ANALYSIS_ID_PATTERN.fullmatch(analysis_id)
    ):
        return None
    try:
        input_path = Path(str(input_data.get("path"))).expanduser().resolve()
        report_path = Path(str((record or {}).get("report_path") or "")).expanduser().resolve()
        if not _managed_analysis_media_path(input_path, report_path.parent):
            return None
    except (OSError, ValueError, TypeError):
        return None
    if not input_path.is_file() or input_path.suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
        return None
    url = request.url_for("media_analysis_input", analysis_id=analysis_id)
    return str(url.include_query_params(token=_media_token("analysis-input", analysis_id)))


def _identity_detail(report: dict[str, Any]) -> dict[str, Any]:
    identity = report.get("chemical_identity") or {}
    descriptors = report.get("descriptors") or {}
    return {
        "rawSmiles": identity.get("raw_smiles"),
        "canonicalSmiles": identity.get("canonical_smiles"),
        "standardizedSmiles": identity.get("standardized_smiles"),
        "isomericSmiles": identity.get("isomeric_smiles"),
        "inchi": identity.get("inchi"),
        "inchiKey": identity.get("inchikey"),
        "formula": descriptors.get("formula") or identity.get("formula"),
        "formalCharge": _number(descriptors, "formal_charge")
        if _number(descriptors, "formal_charge") is not None
        else _number(identity, "formal_charge"),
        "fragmentCount": _number(descriptors, "fragment_count")
        if _number(descriptors, "fragment_count") is not None
        else _number(identity, "fragment_count"),
        "stereocenterCount": _number(identity, "stereocenter_count"),
        "heavyAtomCount": _number(descriptors, "heavy_atom_count"),
    }


def _image_quality_detail(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("image_quality") or {}
    reasons = value.get("reason_codes") or []
    if not isinstance(reasons, list):
        reasons = []
    return {
        "score": _number(value, "quality_score"),
        "passed": value.get("passed") if isinstance(value.get("passed"), bool) else None,
        "width": _number(value, "width"),
        "height": _number(value, "height"),
        "contrast": _number(value, "contrast"),
        "blurVariance": _number(value, "blur_variance"),
        "inkRatio": _number(value, "ink_ratio"),
        "borderInkRatio": _number(value, "border_ink_ratio"),
        "reasonCodes": [str(item) for item in reasons],
    }


def _lipinski_detail(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("lipinski") or {}
    checks: list[dict[str, Any]] = []
    raw_checks = value.get("checks") or []
    if isinstance(raw_checks, list):
        for item in raw_checks:
            if not isinstance(item, dict):
                continue
            checks.append({
                "name": str(item.get("name") or ""),
                "value": item.get("value")
                if isinstance(item.get("value"), (int, float)) and not isinstance(item.get("value"), bool)
                else None,
                "limit": item.get("limit")
                if isinstance(item.get("limit"), (int, float)) and not isinstance(item.get("limit"), bool)
                else None,
                "passed": item.get("passed") if isinstance(item.get("passed"), bool) else None,
                "message": str(item.get("message") or ""),
            })
    return {
        "passed": value.get("passed") if isinstance(value.get("passed"), bool) else None,
        "summary": str(value.get("summary") or ""),
        "checks": checks,
    }


def _recognition_trace(report: dict[str, Any]) -> dict[str, Any]:
    ocsr = report.get("ocsr") or {}
    final = report.get("final") or {}
    standardization = report.get("standardization") or {}
    warnings = standardization.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    return {
        "modelName": ocsr.get("model_name"),
        "modelVersion": ocsr.get("model_version") or ocsr.get("package_version"),
        "resultOrigin": ocsr.get("result_origin"),
        "selectedStrategy": ocsr.get("selected_strategy") or ocsr.get("preprocessing_strategy"),
        "attemptCount": _number(ocsr, "strategy_attempt_count")
        if _number(ocsr, "strategy_attempt_count") is not None
        else _number(ocsr, "attempt_count"),
        "strategyAgreement": ocsr.get("strategy_agreement")
        if isinstance(ocsr.get("strategy_agreement"), bool)
        else None,
        "finalSource": final.get("source"),
        "standardizationChanged": standardization.get("changed")
        if isinstance(standardization.get("changed"), bool)
        else None,
        "standardizationWarnings": [str(item) for item in warnings],
    }


def _review_actor_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "userId": str(user.get("user_id") or ""),
        "displayName": str(user.get("display_name") or user.get("username") or "组员"),
        "role": str(user.get("role") or ""),
    }


def _review_detail(report: dict[str, Any]) -> dict[str, Any]:
    review = human_review_state(report)
    raw_events = report.get("review_events") or []
    events: list[dict[str, Any]] = []
    if isinstance(raw_events, list):
        for item in reversed(raw_events[-20:]):
            if not isinstance(item, dict):
                continue
            actor = item.get("actor") if isinstance(item.get("actor"), dict) else None
            events.append({
                "action": str(item.get("action") or ""),
                "smiles": item.get("smiles"),
                "createdAt": str(item.get("created_at") or ""),
                "reason": item.get("reason"),
                "note": item.get("note"),
                "actor": actor,
            })
    reviewed_by = review.get("reviewed_by")
    return {
        "required": bool(review.get("required")),
        "status": str(review.get("status") or "unconfirmed"),
        "confirmed": bool(review.get("confirmed")),
        "reviewedAt": review.get("reviewed_at"),
        "lastConfirmedAt": review.get("last_confirmed_at"),
        "reason": review.get("reason"),
        "note": review.get("note"),
        "reviewedBy": reviewed_by if isinstance(reviewed_by, dict) else None,
        "events": events,
    }


def _attach_review_audit(
    report: dict[str, Any],
    user: dict[str, Any],
    reason: str | None,
    note: str | None,
) -> None:
    actor = _review_actor_snapshot(user)
    review = report.get("human_review")
    if isinstance(review, dict):
        review["reviewed_by"] = actor
        review["reason"] = reason
        review["note"] = note
    events = report.get("review_events")
    if isinstance(events, list) and events and isinstance(events[-1], dict):
        events[-1]["actor"] = actor
        events[-1]["reason"] = reason
        events[-1]["note"] = note


def _mobile_result(
    report: dict[str, Any],
    request: Request,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ocsr = report.get("ocsr") or {}
    final = report.get("final") or {}
    identity = report.get("chemical_identity") or {}
    descriptors = report.get("descriptors") or {}
    lipinski = report.get("lipinski") or {}
    image_quality = report.get("image_quality") or {}
    decision = report.get("recognition_decision") or {}
    input_data = report.get("input") or {}
    review = human_review_state(report)
    analysis_id = str(report.get("analysis_id") or "")

    predicted_smiles = ocsr.get("predicted_smiles") or ocsr.get("smiles")
    final_smiles = current_final_smiles(report) or identity.get("standardized_smiles") or predicted_smiles
    needs_review = bool(
        not review.get("confirmed")
        and (
            review.get("required")
            or ocsr.get("manual_review_recommended")
            or decision.get("manual_review_recommended")
        )
    )
    warnings = report.get("structure_warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    reason_codes = decision.get("reason_codes") or []
    if not isinstance(reason_codes, list):
        reason_codes = []

    return {
        "analysisId": analysis_id,
        "createdAt": str(report.get("created_at") or (record or {}).get("created_at") or ""),
        "inputType": str(input_data.get("type") or (record or {}).get("input_type") or ""),
        "filename": input_data.get("filename") or (record or {}).get("filename"),
        "status": str(report.get("status") or "failed"),
        "message": str(report.get("message") or "分析未完成。"),
        "backend": str(ocsr.get("backend") or config.OCSR_BACKEND),
        "device": ocsr.get("device"),
        "smiles": final_smiles,
        "predictedSmiles": predicted_smiles,
        "canonicalSmiles": identity.get("canonical_smiles"),
        "confidence": _number(ocsr, "confidence"),
        "inferenceTimeMs": _number(ocsr, "inference_time_ms"),
        "imageQualityScore": _number(image_quality, "quality_score"),
        "needsReview": needs_review,
        "confirmed": bool(review.get("confirmed")),
        "reviewStatus": str(review.get("status") or "unconfirmed"),
        "isFavorite": bool((record or {}).get("is_favorite", False)),
        "decision": decision.get("decision") or ocsr.get("decision"),
        "riskLevel": decision.get("risk_level") or ocsr.get("risk_level"),
        "decisionMessage": decision.get("message"),
        "reasonCodes": [str(item) for item in reason_codes],
        "formula": descriptors.get("formula") or identity.get("formula"),
        "molecularWeight": _number(descriptors, "molecular_weight"),
        "logP": _number(descriptors, "logp"),
        "tpsa": _number(descriptors, "tpsa"),
        "hbd": _number(descriptors, "hbd"),
        "hba": _number(descriptors, "hba"),
        "rotatableBonds": _number(descriptors, "rotatable_bonds"),
        "ringCount": _number(descriptors, "ring_count"),
        "lipinskiPassed": lipinski.get("passed") if isinstance(lipinski.get("passed"), bool) else None,
        "lipinskiViolations": [str(item) for item in (lipinski.get("violations") or [])],
        "structureImageUrl": _structure_media_url(report, request),
        "sourceImageUrl": _source_media_url(report, request, record),
        "warnings": [str(item) for item in warnings],
        "admet": _admet_summary(report),
        "createdBy": _created_by(record, request),
        "identity": _identity_detail(report),
        "imageQuality": _image_quality_detail(report),
        "lipinskiDetail": _lipinski_detail(report),
        "recognitionTrace": _recognition_trace(report),
        "review": _review_detail(report),
    }


def _created_by(record: dict[str, Any] | None, request: Request) -> dict[str, Any] | None:
    owner_user_id = str((record or {}).get("owner_user_id") or "")
    if not owner_user_id:
        return None
    owner = AuthRepository().get_user(owner_user_id)
    return _user_dto(owner, request) if owner is not None else None


def _history_item(
    row: dict[str, Any],
    request: Request,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = human_review_state(report) if report else {}
    return {
        "analysisId": str(row.get("analysis_id") or ""),
        "createdAt": str(row.get("created_at") or ""),
        "inputType": str(row.get("input_type") or ""),
        "filename": str(row.get("filename") or ""),
        "backend": str(row.get("backend") or ""),
        "decision": str(row.get("decision") or ""),
        "status": str(row.get("status") or ""),
        "smiles": str(row.get("final_smiles") or ""),
        "isFavorite": bool(row.get("is_favorite")),
        "confirmed": bool(review.get("confirmed", False)),
        "needsReview": bool(review and review.get("required") and not review.get("confirmed")),
        "artifactStatus": str(row.get("artifact_status") or ""),
        "createdBy": _created_by(row, request),
    }


def _run_analysis(
    generator: MoleculeReportGenerator,
    run: ImageRun,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """Run the blocking model while reusing one warmed model instance."""
    with INFERENCE_LOCK:
        generator.output_dir = run.run_dir
        report = generator.generate(image_path=run.input_path, analysis_id=run.analysis_id)
        report_path = save_run_report(report, run)
        record_report(report, report_path, owner_user_id=owner_user_id)
        return report


def _persist_manual_report(
    report: dict[str, Any],
    run_dir: Path,
    owner_user_id: str | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.json"
    report["run"] = {
        "analysis_id": report["analysis_id"],
        "run_dir": str(run_dir.resolve()),
        "report_path": str(report_path.resolve()),
        "protected": False,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    record_report(report, report_path, owner_user_id=owner_user_id)
    return report_path


class JobStore:
    """Small durable JSON job store with one serial inference worker."""

    def __init__(self, root: Path, executor: ThreadPoolExecutor) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = executor
        self.lock = Lock()
        self.work_items: dict[str, Callable[[], None]] = {}
        self.work_items_lock = Lock()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def create(
        self,
        input_kind: str,
        analysis_id: str,
        owner_user_id: str | None = None,
        message: str = "任务已进入队列。",
    ) -> dict[str, Any]:
        now = utc_now()
        job = {
            "jobId": uuid4().hex,
            "status": "queued",
            "message": message,
            "inputKind": input_kind,
            "analysisId": analysis_id,
            "ownerUserId": owner_user_id,
            "createdAt": now,
            "updatedAt": now,
        }
        self.save(job)
        return job

    def save(self, job: dict[str, Any]) -> None:
        job["updatedAt"] = utc_now()
        target = self._path(str(job["jobId"]))
        temporary = target.with_suffix(".tmp")
        with self.lock:
            temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)

    def load(self, job_id: str) -> dict[str, Any] | None:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            return None
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                jobs.append(payload)
        jobs.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        return jobs[:limit]

    def update(self, job_id: str, **values: Any) -> dict[str, Any]:
        job = self.load(job_id)
        if job is None:
            raise KeyError(job_id)
        job.update(values)
        self.save(job)
        return job

    def submit(
        self,
        job: dict[str, Any],
        generator: MoleculeReportGenerator,
        run: ImageRun,
    ) -> None:
        job_id = str(job["jobId"])
        self._remember_work(job_id, lambda: self._execute(job_id, generator, run))
        self._enqueue(job_id)

    def submit_task(
        self,
        job: dict[str, Any],
        task: Callable[[], dict[str, Any]],
        running_message: str,
        completed_message: str,
        failed_message: str = "任务执行失败，请重试。",
    ) -> None:
        """Run a non-image task on the same serial worker as model inference."""
        job_id = str(job["jobId"])
        self._remember_work(
            job_id,
            lambda: self._execute_task(
                job_id, task, running_message, completed_message, failed_message
            ),
        )
        self._enqueue(job_id)

    def _remember_work(self, job_id: str, work: Callable[[], None]) -> None:
        with self.work_items_lock:
            self.work_items[job_id] = work

    def _forget_work(self, job_id: str) -> None:
        with self.work_items_lock:
            self.work_items.pop(job_id, None)

    def _enqueue(self, job_id: str) -> None:
        with self.work_items_lock:
            work = self.work_items.get(job_id)
        if work is not None:
            self.executor.submit(work)

    def pause(self, job_id: str) -> dict[str, Any]:
        job = self.load(job_id)
        if job is None:
            raise KeyError(job_id)
        status = str(job.get("status") or "")
        if status == "queued":
            return self.update(job_id, status="paused", message="任务已暂停，可稍后继续。")
        if status == "running":
            return self.update(
                job_id,
                status="pausing",
                message="正在等待当前推理到达安全边界；完成前仍可终止。",
            )
        if status in {"paused", "pausing"}:
            return job
        raise ValueError("当前任务状态不能暂停。")

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.load(job_id)
        if job is None:
            raise KeyError(job_id)
        status = str(job.get("status") or "")
        if status == "paused":
            updated = self.update(job_id, status="queued", message="任务已继续，正在等待执行。")
            self._enqueue(job_id)
            return updated
        if status == "pausing":
            return self.update(job_id, status="running", message="已取消暂停请求，任务继续执行。")
        if status in {"queued", "running"}:
            return job
        raise ValueError("当前任务状态不能继续。")

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.load(job_id)
        if job is None:
            raise KeyError(job_id)
        status = str(job.get("status") or "")
        if status in {"queued", "paused"}:
            self._forget_work(job_id)
            return self.update(job_id, status="cancelled", message="任务已终止。")
        if status in {"running", "pausing"}:
            return self.update(
                job_id,
                status="cancelling",
                message="已请求终止；当前推理返回后将丢弃结果。",
            )
        if status in {"cancelled", "cancelling"}:
            return job
        raise ValueError("当前任务状态不能终止。")

    def _may_start(self, job_id: str) -> bool:
        job = self.load(job_id)
        return job is not None and str(job.get("status") or "") == "queued"

    def _cancel_requested(self, job_id: str) -> bool:
        job = self.load(job_id) or {}
        return str(job.get("status") or "") in {"cancelled", "cancelling"}

    def _execute_task(
        self,
        job_id: str,
        task: Callable[[], dict[str, Any]],
        running_message: str,
        completed_message: str,
        failed_message: str,
    ) -> None:
        if not self._may_start(job_id):
            return
        self.update(job_id, status="running", message=running_message)
        try:
            result = task()
            if self._cancel_requested(job_id):
                self.update(job_id, status="cancelled", message="任务已终止，结果未保存到移动端任务。")
                return
            values: dict[str, Any] = {
                "status": "completed",
                "message": completed_message,
                "result": result,
            }
            analysis_id = str(result.get("analysisId") or "")
            if analysis_id:
                values["analysisId"] = analysis_id
            self.update(job_id, **values)
        except Exception:
            # Do not send filesystem paths, dependency details or model stack
            # information to the mobile UI. Server logs remain the diagnostic
            # source for an operator.
            if self._cancel_requested(job_id):
                self.update(job_id, status="cancelled", message="任务已终止。")
            else:
                self.update(job_id, status="failed", message=failed_message)
        finally:
            self._forget_work(job_id)

    def _execute(self, job_id: str, generator: MoleculeReportGenerator, run: ImageRun) -> None:
        if not self._may_start(job_id):
            return
        self.update(job_id, status="running", message="模型正在分析图片。")
        try:
            job = self.load(job_id) or {}
            owner_user_id = str(job.get("ownerUserId") or "") or None
            report = _run_analysis(generator, run, owner_user_id)
            if self._cancel_requested(job_id):
                self.update(job_id, status="cancelled", message="任务已终止，结果未保存到移动端任务。")
                return
            if report.get("status") == "success":
                self.update(
                    job_id,
                    status="completed",
                    message=str(report.get("message") or "分析完成。"),
                    analysisId=str(report.get("analysis_id") or run.analysis_id),
                )
            else:
                self.update(
                    job_id,
                    status="failed",
                    message=str(report.get("message") or "模型未能完成分析。"),
                    analysisId=str(report.get("analysis_id") or run.analysis_id),
                )
        except Exception as exc:
            if self._cancel_requested(job_id):
                self.update(job_id, status="cancelled", message="任务已终止。")
            else:
                self.update(job_id, status="failed", message=f"模型分析失败：{exc}")
        finally:
            self._forget_work(job_id)

    def reconcile_after_restart(self) -> None:
        repository = AnalysisRepository()
        for path in self.root.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or job.get("status") not in {
                "queued", "running", "paused", "pausing", "cancelling"
            }:
                continue
            analysis_id = str(job.get("analysisId") or "")
            report = repository.load_report(analysis_id) if analysis_id else None
            if report is None and ANALYSIS_ID_PATTERN.fullmatch(analysis_id):
                report_path = image_run_dir(analysis_id, config.RUNS_DIR) / "report.json"
                try:
                    loaded = json.loads(report_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and loaded.get("analysis_id") == analysis_id:
                        report = loaded
                        record_report(
                            report,
                            report_path,
                            owner_user_id=str(job.get("ownerUserId") or "") or None,
                        )
                except (OSError, json.JSONDecodeError):
                    report = None
            if job.get("status") == "cancelling":
                job["status"] = "cancelled"
                job["message"] = "服务重启后已完成终止请求。"
            elif report and report.get("status") == "success":
                job["status"] = "completed"
                job["message"] = "服务重启后已从报告恢复完成状态。"
            else:
                job["status"] = "failed"
                job["message"] = "服务重启中断了推理，请重新提交任务。"
            self.save(job)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not _configured_api_key():
        raise RuntimeError("HARMONY_API_KEY 不能为空；请使用 start_harmony_api.ps1 -ApiKey <密钥> 启动。")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="harmony-inference")
    app.state.generator = MoleculeReportGenerator(config.OCSR_BACKEND, config.RUNS_DIR)
    app.state.executor = executor
    app.state.job_store = JobStore(config.DATA_DIR / "api_jobs", executor)
    app.state.batch_job_store = BatchJobStore(config.DATA_DIR / "api_batch_jobs")
    app.state.mobile_export_store = MobileExportStore(config.DATA_DIR / "mobile_exports")
    app.state.document_store = MobileDocumentStore(config.DATA_DIR / "mobile_documents")
    app.state.document_processor = DocumentOCSRProcessor(
        backend=config.OCSR_BACKEND,
        output_dir=config.DOCUMENT_OUTPUT_DIR,
        report_generator=app.state.generator,
    )
    app.state.job_store.reconcile_after_restart()
    try:
        yield
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Molecule Vision Local API", version="2.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)
public_api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
auth_api = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(require_api_key)])
api = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key), Depends(require_user)],
)


@public_api.get("/health")
def health(request: Request) -> dict[str, Any]:
    detected = run_production_health_check(
        backend=config.OCSR_BACKEND,
        production=config.IS_PRODUCTION_MODE,
        warmup=config.PRODUCTION_HEALTH_WARMUP if config.IS_PRODUCTION_MODE else False,
        load_model=config.PRODUCTION_HEALTH_LOAD_MODEL if config.IS_PRODUCTION_MODE else False,
        use_cache=True,
    )
    return _mobile_health_status(request, detected)


@auth_api.post("/register", status_code=201)
def register_user(payload: RegisterRequest, request: Request) -> dict[str, Any]:
    repository = AuthRepository()
    try:
        user = repository.register(
            payload.username,
            payload.display_name,
            payload.password,
            payload.role,
            payload.account_type,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "已经注册" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc
    token = repository.create_session(str(user["user_id"]))
    return _auth_response(user, token, request)


@auth_api.post("/login")
def login_user(payload: LoginRequest, request: Request) -> dict[str, Any]:
    repository = AuthRepository()
    user = repository.authenticate_password(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    if payload.account_type is not None and user.get("account_type") != payload.account_type:
        label = "开发者" if payload.account_type == "developer" else "普通用户"
        raise HTTPException(status_code=403, detail=f"该账号不是{label}账号，请切换登录入口。")
    token = repository.create_session(str(user["user_id"]))
    return _auth_response(user, token, request)


@auth_api.get("/me")
def current_user(request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return _user_dto(user, request)


@auth_api.patch("/me")
@auth_api.put("/me")
def update_current_user(
    payload: ProfileRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    try:
        updated = AuthRepository().update_profile(
            str(user["user_id"]),
            payload.display_name,
            payload.role if _is_developer(user) else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _user_dto(updated, request)


@auth_api.post("/change-password")
def change_current_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repository = AuthRepository()
    try:
        updated = repository.change_password(
            str(user["user_id"]),
            payload.old_password,
            payload.new_password,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = repository.create_session(str(user["user_id"]))
    return _auth_response(updated, token, request)


@auth_api.get("/members")
def group_members(request: Request) -> list[dict[str, Any]]:
    """List developer profiles for the dedicated developer login screen."""
    return [_user_dto(user, request) for user in AuthRepository().list_users("developer")]


@auth_api.post("/logout", status_code=204)
def logout_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    _user: dict[str, Any] = Depends(require_user),
) -> Response:
    AuthRepository().revoke_session(_bearer_token(authorization))
    return Response(status_code=204)


@auth_api.post("/me/avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if file.content_type and file.content_type.lower() not in AVATAR_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="头像仅支持 PNG、JPEG 或 WEBP。")
    payload = await file.read(MAX_AVATAR_BYTES + 1)
    await file.close()
    if not payload:
        raise HTTPException(status_code=400, detail="头像文件为空。")
    if len(payload) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="头像不能超过 3 MB。")
    try:
        with Image.open(BytesIO(payload)) as source:
            source.load()
            avatar = ImageOps.fit(source.convert("RGB"), (256, 256))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="文件不是可识别的头像图片。") from exc

    avatar_dir = (config.DATA_DIR / "avatars").resolve()
    avatar_dir.mkdir(parents=True, exist_ok=True)
    user_id = str(user["user_id"])
    avatar_path = avatar_dir / f"{user_id}.png"
    temporary = avatar_path.with_suffix(".tmp.png")
    avatar.save(temporary, format="PNG", optimize=True)
    temporary.replace(avatar_path)
    updated = AuthRepository().update_avatar(user_id, avatar_path)
    return _user_dto(updated, request)


def _mobile_document_store(request: Request) -> MobileDocumentStore:
    return request.app.state.document_store


def _load_mobile_document_result(
    request: Request,
    document_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return _mobile_document_store(request).load_result(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文档不存在。") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="文档尚未完成区域检测。") from exc


def _document_page_path(request: Request, document_id: str, page_number: int) -> Path:
    _manifest, result = _load_mobile_document_result(request, document_id)
    page = next(
        (item for item in result.get("pages", []) if int(item.get("page_number") or 0) == page_number),
        None,
    )
    if not isinstance(page, dict):
        raise HTTPException(status_code=404, detail="文档页面不存在。")
    try:
        path = Path(str(page.get("image_path") or "")).expanduser().resolve()
        path.relative_to(Path(config.DOCUMENT_OUTPUT_DIR).expanduser().resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="文档页面不存在。") from exc
    if not path.is_file() or path.suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="文档页面不存在。")
    return path


def _document_dto(request: Request, manifest: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    if result is not None:
        for page in result.get("pages", []):
            page_number = int(page.get("page_number") or 0)
            resource_id = f"{manifest['documentId']}:{page_number}"
            url = request.url_for(
                "media_document_page",
                document_id=str(manifest["documentId"]),
                page_number=str(page_number),
            )
            pages.append({
                "pageNumber": page_number,
                "width": int(page.get("width") or 0),
                "height": int(page.get("height") or 0),
                "previewUrl": str(url.include_query_params(token=_media_token("document-page", resource_id))),
            })
        for region in result.get("regions", []):
            if region.get("status") == "deleted":
                continue
            report = region.get("report") if isinstance(region.get("report"), dict) else {}
            regions.append({
                "regionId": str(region.get("region_id") or ""),
                "pageNumber": int(region.get("page_number") or 0),
                "bbox": [int(value) for value in (region.get("bbox") or [])],
                "regionType": str(region.get("region_type") or "unknown"),
                "confidence": region.get("detection_confidence"),
                "confirmed": bool(region.get("confirmed")),
                "status": str(region.get("status") or "detected"),
                "message": str(region.get("message") or ""),
                "recognitionJobId": str(region.get("recognition_job_id") or ""),
                "analysisId": str(report.get("analysis_id") or ""),
            })
    owner = AuthRepository().get_user(str(manifest.get("ownerUserId") or ""))
    summary = result.get("summary", {}) if result else {}
    return {
        "documentId": str(manifest["documentId"]),
        "filename": str(manifest.get("filename") or "document"),
        "contentType": str(manifest.get("contentType") or ""),
        "status": str(manifest.get("status") or "uploaded"),
        "message": str(manifest.get("message") or ""),
        "detectionJobId": str(manifest.get("detectionJobId") or ""),
        "createdAt": str(manifest.get("createdAt") or ""),
        "updatedAt": str(manifest.get("updatedAt") or ""),
        "createdBy": _user_dto(owner, request) if owner else None,
        "pageCount": int(summary.get("page_count") or len(pages)),
        "regionCount": int(summary.get("region_count") or len(regions)),
        "pages": pages,
        "regions": regions,
    }


@api.get("/samples")
def samples(request: Request) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for sample_id, display_name in SAMPLE_LABELS.items():
        path = config.SAMPLE_DIR / f"{sample_id}.png"
        if path.is_file():
            url = request.url_for("media_sample_image", sample_id=sample_id)
            result.append({
                "id": sample_id,
                "name": display_name,
                "imageUrl": str(url.include_query_params(token=_media_token("sample", sample_id))),
            })
    return result


def _sample_file(sample_id: str) -> Path:
    if sample_id not in SAMPLE_LABELS:
        raise HTTPException(status_code=404, detail="内置示例不存在。")
    path = (config.SAMPLE_DIR / f"{sample_id}.png").resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="内置示例图片不存在。")
    return path


@api.get("/samples/{sample_id}/image", name="sample_image")
def sample_image(sample_id: str) -> FileResponse:
    path = _sample_file(sample_id)
    return FileResponse(path, media_type="image/png", filename=f"{sample_id}.png")


@app.get("/media/v1/samples/{sample_id}/image", name="media_sample_image")
def media_sample_image(sample_id: str, token: str = Query(default="")) -> FileResponse:
    _check_media_token("sample", sample_id, token)
    path = _sample_file(sample_id)
    return FileResponse(path, media_type="image/png", filename=f"{sample_id}.png")


def _create_image_job(
    request: Request,
    run: ImageRun,
    input_kind: str,
    owner_user_id: str,
) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    generator: MoleculeReportGenerator = request.app.state.generator
    job = store.create(
        input_kind=input_kind,
        analysis_id=run.analysis_id,
        owner_user_id=owner_user_id,
    )
    store.submit(job, generator, run)
    return job


def _batch_store(request: Request) -> BatchJobStore:
    return request.app.state.batch_job_store


def _owned_batch_job(request: Request, job_id: str, user: Mapping[str, Any]) -> dict[str, Any]:
    store = _batch_store(request)
    try:
        state = store.read(job_id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="批量任务不存在。") from exc
    if str(state.get("owner_user_id") or "") != str(user.get("user_id") or ""):
        # Do not disclose whether another team member owns this opaque task ID.
        raise HTTPException(status_code=404, detail="批量任务不存在。")
    return state


def _batch_reports(store: BatchJobStore, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    job_id = str(state.get("job_id") or "")
    try:
        result = load_batch_job_result(job_id, store)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        result = None
    reports = list((result or {}).get("reports") or [])
    if reports:
        return _refresh_batch_report_snapshots(reports)
    checkpoint_path = store.checkpoint_path(job_id)
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    reports_by_path = checkpoint.get("reports_by_path") if isinstance(checkpoint, dict) else {}
    if not isinstance(reports_by_path, dict):
        return []
    return _refresh_batch_report_snapshots(reports_by_path.values())


def _refresh_batch_report_snapshots(reports: Iterable[Any]) -> list[dict[str, Any]]:
    """Overlay immutable batch snapshots with the latest independently reviewed reports."""
    repository = AnalysisRepository()
    refreshed: list[dict[str, Any]] = []
    for raw_report in reports:
        if not isinstance(raw_report, Mapping):
            continue
        report = dict(raw_report)
        analysis_id = str(report.get("analysis_id") or "").strip()
        if analysis_id:
            try:
                latest = repository.load_report(analysis_id)
            except (OSError, json.JSONDecodeError, ValueError):
                latest = None
            if isinstance(latest, Mapping):
                report = dict(latest)
        refreshed.append(report)
    return refreshed


def _batch_item_category(row: Mapping[str, Any]) -> str:
    status = str(row.get("status") or "").lower()
    if status == "skipped":
        return "skipped"
    if status != "success":
        return "failed"
    decision = str(row.get("recognition_decision") or "").lower()
    if decision in {"accepted", "accepted_with_warning", "review_needed", "rejected"}:
        return decision
    if row.get("manual_review_recommended"):
        return "review_needed"
    return "accepted"


def _batch_item_dto(report: dict[str, Any]) -> dict[str, Any]:
    row = flatten_report(report)
    ocsr = report.get("ocsr") if isinstance(report.get("ocsr"), Mapping) else {}
    status = str(report.get("status") or "failed")
    review = human_review_state(report)
    confirmed = bool(review.get("confirmed"))
    category = _batch_item_category(row)
    return {
        "analysisId": str(report.get("analysis_id") or ""),
        "filename": str((report.get("input") or {}).get("filename") or ""),
        "status": status,
        "category": category,
        "message": "识别失败，可重试此图片。" if status == "failed" else str(report.get("message") or ""),
        "smiles": row.get("final_smiles") or row.get("smiles"),
        "canonicalSmiles": row.get("canonical_smiles"),
        "confidence": _number(ocsr, "confidence"),
        "valid": bool(row.get("valid")),
        "needsReview": not confirmed and category in {"accepted_with_warning", "review_needed"},
        "confirmed": confirmed,
        "reviewStatus": str(review.get("status") or "unconfirmed"),
    }


def _batch_job_dto(store: BatchJobStore, state: Mapping[str, Any]) -> dict[str, Any]:
    total = max(0, int(state.get("total") or 0))
    completed = max(0, int(state.get("completed") or 0))
    summary = state.get("summary") if isinstance(state.get("summary"), Mapping) else {}
    reports = _batch_reports(store, state)
    categories = {
        "accepted": int(state.get("accepted") or summary.get("accepted") or 0),
        "acceptedWithWarning": int(
            state.get("accepted_with_warning") or summary.get("accepted_with_warning") or 0
        ),
        "reviewNeeded": int(state.get("review_needed") or summary.get("review_needed") or 0),
        "rejected": int(state.get("rejected") or summary.get("rejected") or 0),
        "failed": int(state.get("failed") or summary.get("failed") or 0),
        "skipped": int(state.get("skipped") or summary.get("skipped") or 0),
    }
    status = str(state.get("status") or "failed")
    if reports and status in {"completed", "failed", "cancelled"}:
        categories = {
            "accepted": 0,
            "acceptedWithWarning": 0,
            "reviewNeeded": 0,
            "rejected": 0,
            "failed": 0,
            "skipped": 0,
        }
        for report in reports:
            item = _batch_item_dto(report)
            if item["confirmed"]:
                categories["accepted"] += 1
            elif item["category"] == "accepted_with_warning":
                categories["acceptedWithWarning"] += 1
            elif item["category"] == "review_needed":
                categories["reviewNeeded"] += 1
            elif item["category"] == "rejected":
                categories["rejected"] += 1
            elif item["category"] == "skipped":
                categories["skipped"] += 1
            elif item["category"] == "accepted":
                categories["accepted"] += 1
            else:
                categories["failed"] += 1
    message = str(state.get("message") or state.get("error") or "")
    if status == "failed":
        message = "批量识别失败，请重试失败项或在电脑端查看日志。"
    if not message:
        message = {
            "queued": "批量任务已进入队列。",
            "running": "正在逐张识别图片。",
            "paused": "批量任务已暂停。",
            "cancelling": "正在取消批量任务。",
            "cancelled": "批量任务已取消。",
            "completed": "批量识别完成。",
            "failed": "批量识别失败。",
        }.get(status, "批量任务状态已更新。")
    return {
        "jobId": str(state.get("job_id") or ""),
        "status": status,
        "message": message,
        "backend": str(state.get("backend") or config.OCSR_BACKEND),
        "source": str(state.get("source") or "upload"),
        "parentJobId": state.get("parent_job_id"),
        "total": total,
        "completed": completed,
        "progress": round(completed / total, 4) if total else 0.0,
        "currentFile": state.get("current_file"),
        "currentIndex": state.get("current_index"),
        "categoryStats": categories,
        "results": [_batch_item_dto(report) for report in reports],
        "createdAt": str(state.get("created_at") or ""),
        "startedAt": state.get("started_at"),
        "finishedAt": state.get("finished_at"),
        "updatedAt": str(state.get("updated_at") or ""),
    }


def _refresh_owned_batch_job(
    request: Request,
    job_id: str,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    state = _owned_batch_job(request, job_id, user)
    if state.get("status") in {"queued", "running", "paused", "cancelling"}:
        try:
            state = refresh_batch_job(job_id, _batch_store(request))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="批量任务不存在。") from exc
    return state


def _export_label(export_format: str, content_type: str | None = None) -> str:
    if export_format == "mol" and content_type == "application/zip":
        return "MOL 文件包"
    labels = {
        "csv": "CSV 结果表",
        "json": "JSON 数据",
        "pdf": "PDF 报告",
        "smi": "SMI 正式结构",
        "mol": "MOL 正式结构",
        "sdf": "SDF 正式结构",
        "zip": "正式结构 ZIP",
    }
    return labels.get(export_format, export_format.upper())


def _mobile_export_manifest(
    request: Request,
    scope: str,
    resource_id: str,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    store: MobileExportStore = request.app.state.mobile_export_store
    items: list[dict[str, Any]] = []
    for export_format in EXPORT_FORMATS:
        artifact = artifacts.get(export_format) or {}
        available = bool(artifact.get("available"))
        item: dict[str, Any] = {
            "format": export_format,
            "label": _export_label(export_format, str(artifact.get("content_type") or "")),
            "available": available,
            "reason": str(artifact.get("reason") or ""),
            "formalOnly": export_format in FORMAL_ONLY_FORMATS,
            "fileName": artifact.get("filename"),
            "contentType": artifact.get("content_type"),
            "sizeBytes": 0,
            "downloadUrl": None,
            "expiresAt": None,
        }
        if available:
            registered = store.register(
                str(artifact["path"]),
                str(artifact["filename"]),
                str(artifact["content_type"]),
                ttl_seconds=MOBILE_EXPORT_TTL_SECONDS,
            )
            export_id = str(registered["export_id"])
            expires = int(registered["expires_at"])
            url = request.url_for("media_export", export_id=export_id)
            item.update({
                "fileName": str(registered["filename"]),
                "contentType": str(registered["content_type"]),
                "sizeBytes": int(registered["size_bytes"]),
                "downloadUrl": str(url.include_query_params(
                    expires=str(expires),
                    token=_expiring_media_token("export", export_id, expires),
                )),
                "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            })
        items.append(item)
    return {
        "scope": scope,
        "resourceId": resource_id,
        "expiresInSeconds": MOBILE_EXPORT_TTL_SECONDS,
        "items": items,
    }


def _analysis_exports(analysis_id: str, request: Request) -> dict[str, Any]:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    report = repository.load_report(analysis_id)
    if row is None or report is None:
        raise HTTPException(status_code=404, detail="分析记录不存在或报告文件不可用。")
    output_dir = config.DATA_DIR / "mobile_export_work" / "analyses" / analysis_id
    try:
        artifacts = build_single_exports(report, output_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="导出文件生成失败，请稍后重试。") from exc
    return _mobile_export_manifest(request, "analysis", analysis_id, artifacts)


def _batch_exports(
    job_id: str,
    request: Request,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    state = _refresh_owned_batch_job(request, job_id, user)
    if state.get("status") not in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="请等待批量任务结束后再导出。")
    store = _batch_store(request)
    result = load_batch_job_result(job_id, store)
    reports = _batch_reports(store, state)
    if result is None:
        result = {
            "summary": state.get("summary") or {},
        }
    else:
        result = dict(result)
    # Reviews happen after BatchAnalyzer writes its completion snapshot. Always
    # export from the latest report so formal structures never use stale state.
    result["reports"] = reports
    if not result.get("reports"):
        raise HTTPException(status_code=409, detail="批量任务还没有可导出的逐项结果。")
    output_dir = config.DATA_DIR / "mobile_export_work" / "batch_jobs" / job_id
    try:
        artifacts = build_batch_exports(result, output_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="批量导出文件生成失败，请稍后重试。") from exc
    return _mobile_export_manifest(request, "batch", job_id, artifacts)


def _export_item(manifest: Mapping[str, Any], export_format: str) -> dict[str, Any]:
    normalized = export_format.lower()
    if normalized not in EXPORT_FORMATS:
        raise HTTPException(status_code=404, detail="不支持的导出格式。")
    item = next((entry for entry in manifest.get("items") or [] if entry.get("format") == normalized), None)
    if not isinstance(item, dict):
        raise HTTPException(status_code=404, detail="导出格式不存在。")
    if not item.get("available"):
        raise HTTPException(status_code=409, detail=str(item.get("reason") or "该格式暂不可导出。"))
    return item


def _health_check(health: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return next(
        (item for item in (health.get("checks") or []) if isinstance(item, Mapping) and item.get("name") == name),
        {},
    )


def _health_state_label(state: str) -> str:
    return {
        "ready": "真实模型可用",
        "degraded": "检测到警告",
        "unavailable": "真实模型不可用",
        "demo": "演示后端（非真实模型）",
        "pass": "通过",
        "fail": "失败",
        "warn": "警告",
        "skip": "本次未执行",
    }.get(state, "尚未检测")


def _task_queue_status(request: Request) -> dict[str, Any]:
    single_jobs: list[dict[str, Any]] = request.app.state.job_store.list_jobs(limit=200)
    batch_jobs: list[dict[str, Any]] = request.app.state.batch_job_store.list_jobs(limit=200)

    def counts(items: list[Mapping[str, Any]]) -> dict[str, int]:
        return {
            status: sum(1 for item in items if str(item.get("status") or "") == status)
            for status in (
                "queued", "running", "paused", "pausing", "cancelling",
                "completed", "failed", "cancelled",
            )
        }

    single_counts = counts(single_jobs)
    batch_counts = counts(batch_jobs)
    failure_reasons: list[str] = []
    for item in [*single_jobs, *batch_jobs]:
        if item.get("status") != "failed":
            continue
        reason = str(item.get("error") or item.get("message") or "任务失败。").strip()
        if reason and reason not in failure_reasons:
            failure_reasons.append(reason)
        if len(failure_reasons) >= 5:
            break
    queued = single_counts["queued"] + batch_counts["queued"]
    running = single_counts["running"] + batch_counts["running"]
    paused = single_counts["paused"] + batch_counts["paused"]
    pausing = single_counts["pausing"] + batch_counts["pausing"]
    cancelling = single_counts["cancelling"] + batch_counts["cancelling"]
    return {
        "queued": queued,
        "running": running,
        "paused": paused,
        "pausing": pausing,
        "cancelling": cancelling,
        "active": queued + running + paused + pausing + cancelling,
        "failed": single_counts["failed"] + batch_counts["failed"],
        "single": single_counts,
        "batch": batch_counts,
        "recentFailureReasons": failure_reasons,
    }


def _mobile_health_status(request: Request, detected: Mapping[str, Any]) -> dict[str, Any]:
    backend = str(detected.get("backend") or config.OCSR_BACKEND)
    backend_status = detected.get("backend_status") if isinstance(detected.get("backend_status"), Mapping) else {}
    available_check = _health_check(detected, "backend.available")
    model_file_check = _health_check(detected, "backend.model_file")
    model_load_check = _health_check(detected, "backend.model_load")
    device_check = _health_check(detected, "runtime.device")
    warmup_check = _health_check(detected, "warmup")
    if backend == "demo":
        runtime_state = "demo"
    elif bool(detected.get("ready")) and available_check.get("status") == "pass":
        runtime_state = "ready"
    elif available_check.get("status") == "pass":
        runtime_state = "degraded"
    else:
        runtime_state = "unavailable"

    if model_load_check.get("status") == "pass":
        weight_state = "pass"
        weight_label = "权重已加载"
    elif model_file_check.get("status") == "pass":
        weight_state = "warn" if model_load_check.get("status") == "skip" else str(model_load_check.get("status") or "warn")
        weight_label = "权重文件已检测，模型尚未加载" if weight_state == "warn" else _health_state_label(weight_state)
    elif model_file_check.get("status") == "fail" or model_load_check.get("status") == "fail":
        weight_state = "fail"
        weight_label = "权重检测失败"
    else:
        weight_state = str(model_load_check.get("status") or model_file_check.get("status") or "skip")
        weight_label = "后端未报告独立权重文件" if weight_state == "skip" else _health_state_label(weight_state)

    failure_reasons = [str(value) for value in (detected.get("failures") or []) if str(value).strip()]
    return {
        "status": "ok" if runtime_state in {"ready", "demo"} else "degraded",
        "appMode": config.APP_MODE,
        "backend": backend,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "apiVersion": "2.5",
        "modelRuntime": {
            "state": runtime_state,
            "label": _health_state_label(runtime_state),
            "modelName": backend_status.get("model_name") or backend_status.get("name") or backend,
            "modelVersion": backend_status.get("model_version") or backend_status.get("package_version"),
            "device": backend_status.get("device") or (device_check.get("details") or {}).get("resolved_device"),
            "message": str(available_check.get("message") or backend_status.get("message") or "未返回后端诊断信息。"),
            "checkedAt": detected.get("created_at"),
            "cached": bool(detected.get("cached")),
            "weights": {
                "state": weight_state,
                "label": weight_label,
                "message": str(model_load_check.get("message") or model_file_check.get("message") or "未返回权重诊断。"),
                "sha256": detected.get("model_sha256"),
            },
            "warmup": {
                "state": str(warmup_check.get("status") or "skip"),
                "label": _health_state_label(str(warmup_check.get("status") or "skip")),
                "message": str(warmup_check.get("message") or "本次健康检查未执行 Warm-up。"),
                "durationMs": (warmup_check.get("details") or {}).get("total_warmup_time_ms"),
            },
            "failureReasons": failure_reasons,
        },
        "taskQueue": _task_queue_status(request),
    }


def _feedback_service() -> FeedbackReviewService:
    return FeedbackReviewService(config.DATA_DIR)


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _feedback_training_eligibility(item: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if str(item.get("review_status") or "") != "verified":
        reasons.append("样本尚未通过独立人工核验。")
    if not str(item.get("reviewer") or "").strip():
        reasons.append("缺少审核人记录。")
    if not _truthy(item.get("include_in_training")):
        reasons.append("审核结果未标记为可进入训练集。")
    corrected = str(item.get("corrected_smiles") or "").strip()
    if not corrected or not validate_smiles(corrected).get("valid"):
        reasons.append("人工修正 SMILES 缺失或无效。")
    if not item.get("image_path_abs"):
        reasons.append("反馈原图不可用。")
    if _truthy(item.get("duplicate_image")):
        reasons.append("样本被标记为重复项。")
    eligible = not reasons
    return {
        "eligible": eligible,
        "label": "已核验，可进入训练 Manifest" if eligible else "不可进入训练集",
        "reasons": reasons,
    }


def _feedback_image_url(item: Mapping[str, Any], request: Request) -> str | None:
    if not item.get("image_path_abs"):
        return None
    analysis_id = str(item.get("analysis_id") or "")
    if not analysis_id:
        return None
    url = request.url_for("media_feedback_image", analysis_id=analysis_id)
    return str(url.include_query_params(token=_media_token("feedback-image", analysis_id)))


def _feedback_item_dto(item: Mapping[str, Any], request: Request, *, detail: bool = False) -> dict[str, Any]:
    prediction = item.get("prediction") if isinstance(item.get("prediction"), Mapping) else {}
    feedback = item.get("feedback") if isinstance(item.get("feedback"), Mapping) else {}
    original = item.get("original_input") if isinstance(item.get("original_input"), Mapping) else {}
    dto: dict[str, Any] = {
        "analysisId": str(item.get("analysis_id") or ""),
        "savedAt": str(item.get("saved_at") or ""),
        "reviewStatus": str(item.get("review_status") or "pending"),
        "predictedSmiles": item.get("predicted_smiles"),
        "correctedSmiles": item.get("corrected_smiles"),
        "sourceReference": item.get("source_reference") or "",
        "sourceLicense": item.get("source_license") or "",
        "reviewer": item.get("reviewer") or "",
        "reviewedAt": item.get("reviewed_at") or None,
        "revision": int(item.get("revision") or 1),
        "imageUrl": _feedback_image_url(item, request),
        "filename": original.get("filename") or "",
        "trainingEligibility": _feedback_training_eligibility(item),
    }
    if detail:
        history: list[dict[str, Any]] = []
        for event in item.get("history") or []:
            if not isinstance(event, Mapping):
                continue
            history.append({
                "operation": str(event.get("operation") or event.get("action") or "记录"),
                "createdAt": event.get("created_at") or event.get("at") or "",
                "reviewer": event.get("reviewer") or event.get("revised_by") or "",
                "oldStatus": event.get("old_status") or event.get("previous_review_status") or "",
                "newStatus": event.get("new_status") or event.get("new_review_status") or "",
                "notes": event.get("reviewer_notes") or event.get("notes") or event.get("reason") or "",
            })
        dto.update({
            "modelName": prediction.get("model_name") or item.get("model_name"),
            "modelVersion": prediction.get("model_version") or item.get("model_version"),
            "backend": prediction.get("backend") or item.get("backend"),
            "confidence": prediction.get("confidence"),
            "correctionType": feedback.get("correction_type") or item.get("correction_type") or "other",
            "feedbackAction": feedback.get("feedback_action") or item.get("feedback_action") or "correction_only",
            "duplicateImage": _truthy(feedback.get("duplicate_image") or item.get("duplicate_image")),
            "duplicateOf": feedback.get("duplicate_of") or item.get("duplicate_of") or "",
            "notes": feedback.get("notes") or item.get("notes") or "",
            "privacyNotes": feedback.get("privacy_notes") or item.get("privacy_notes") or "",
            "history": history,
        })
    return dto


@api.post("/jobs/samples/{sample_id}", status_code=202)
def create_sample_job(
    sample_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    path = _sample_file(sample_id)
    run = create_image_run_from_file(path, original_filename=path.name, runs_root=config.RUNS_DIR)
    return _create_image_job(request, run, "sample", str(user["user_id"]))


@api.post("/jobs/images", status_code=202)
async def create_image_job(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if file.content_type and file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="仅支持 PNG、JPEG、WEBP 或 BMP 图片。")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    _validate_image(payload)
    filename = _clean_filename(file.filename, file.content_type)
    run = create_image_run_from_bytes(payload, filename, runs_root=config.RUNS_DIR)
    return _create_image_job(request, run, "image", str(user["user_id"]))


@api.post("/batch-jobs", status_code=202)
async def create_batch_job(
    request: Request,
    files: list[UploadFile] = File(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    limits = batch_input_limits()
    max_files = int(limits["max_files"])
    max_file_bytes = int(float(limits["max_file_size_mb"]) * 1024 * 1024)
    max_total_bytes = int(float(limits["max_total_size_mb"]) * 1024 * 1024)
    if not files:
        raise HTTPException(status_code=422, detail="请至少选择一张图片。")
    if len(files) > max_files:
        raise HTTPException(status_code=413, detail=f"图片数量不能超过 {max_files} 张。")
    uploads: list[tuple[str, bytes]] = []
    total_bytes = 0
    try:
        for index, file in enumerate(files, start=1):
            payload = await file.read(max_file_bytes + 1)
            if len(payload) > max_file_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"第 {index} 张图片超过 {float(limits['max_file_size_mb']):g} MB 上限。",
                )
            total_bytes += len(payload)
            if total_bytes > max_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"图片总大小超过 {float(limits['max_total_size_mb']):g} MB 上限。",
                )
            uploads.append((_clean_filename(file.filename, file.content_type), payload))
    finally:
        for file in files:
            await file.close()
    try:
        state = start_batch_job_from_uploads(
            uploads,
            config.OCSR_BACKEND,
            store=_batch_store(request),
            owner_user_id=str(user["user_id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="批量任务暂时无法启动，请检查电脑端存储空间。") from exc
    return _batch_job_dto(_batch_store(request), state)


@api.get("/batch-jobs/{job_id}")
def get_batch_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    state = _refresh_owned_batch_job(request, job_id, user)
    return _batch_job_dto(_batch_store(request), state)


@api.post("/batch-jobs/{job_id}/pause")
def pause_mobile_batch_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    state = _refresh_owned_batch_job(request, job_id, user)
    if state.get("status") not in {"queued", "running", "paused"}:
        raise HTTPException(status_code=409, detail="当前批量任务不能暂停。")
    state = pause_batch_job(job_id, _batch_store(request))
    return _batch_job_dto(_batch_store(request), state)


@api.post("/batch-jobs/{job_id}/resume")
def resume_mobile_batch_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    state = _owned_batch_job(request, job_id, user)
    if state.get("status") not in {"paused", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前批量任务不需要继续。")
    try:
        state = resume_batch_job(job_id, _batch_store(request))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _batch_job_dto(_batch_store(request), state)


@api.post("/batch-jobs/{job_id}/cancel")
def cancel_mobile_batch_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    state = _owned_batch_job(request, job_id, user)
    if state.get("status") in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前批量任务已经结束。")
    state = cancel_batch_job(job_id, _batch_store(request), force=False)
    return _batch_job_dto(_batch_store(request), state)


@api.post("/batch-jobs/{job_id}/retry-failed", status_code=202)
def retry_failed_mobile_batch_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    state = _refresh_owned_batch_job(request, job_id, user)
    if state.get("status") not in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="请等待当前批量任务结束后再重试失败项。")
    result = load_batch_job_result(job_id, _batch_store(request))
    if result is None:
        result = {"reports": _batch_reports(_batch_store(request), state)}
    try:
        retry = start_batch_retry_job(
            result,
            str(state.get("backend") or config.OCSR_BACKEND),
            "failed",
            state.get("runtime_config") or {},
            store=_batch_store(request),
            parent_job_id=job_id,
            owner_user_id=str(user["user_id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="失败项重试暂时无法启动。") from exc
    return _batch_job_dto(_batch_store(request), retry)


@api.get("/batch-jobs/{job_id}/exports")
def get_batch_export_center(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return _batch_exports(job_id, request, user)


@api.get("/batch-jobs/{job_id}/exports/{export_format}")
def get_batch_export_format(
    job_id: str,
    export_format: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return _export_item(_batch_exports(job_id, request, user), export_format)


@api.post("/documents", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    original_name = Path(file.filename or "document").name
    payload = await file.read(MAX_DOCUMENT_BYTES + 1)
    await file.close()
    suffix, content_type = _validate_document_upload(payload)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._")[:80]
    filename = f"{safe_stem or 'document'}{suffix}"
    manifest = _mobile_document_store(request).create(
        payload,
        filename,
        content_type,
        str(user["user_id"]),
    )
    return _document_dto(request, manifest, None)


@api.post("/documents/{document_id}/detect", status_code=202)
def detect_document_regions(
    document_id: str,
    request: Request,
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    document_store = _mobile_document_store(request)
    manifest = document_store.load(document_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="文档不存在。")
    job_store: JobStore = request.app.state.job_store
    existing_job_id = str(manifest.get("detectionJobId") or "")
    existing_job = job_store.load(existing_job_id) if existing_job_id else None
    if existing_job and existing_job.get("status") in {"queued", "running"}:
        return existing_job

    job = job_store.create(
        input_kind="document_detection",
        analysis_id="",
        owner_user_id=str(manifest.get("ownerUserId") or "") or None,
        message="文档已进入区域检测队列。",
    )
    job.update({"documentId": document_id})
    job_store.save(job)
    document_store.update(
        document_id,
        status="detecting",
        message="正在渲染页面并检测候选区域。",
        detectionJobId=str(job["jobId"]),
    )

    def run_detection() -> dict[str, Any]:
        current = document_store.load(document_id)
        if current is None:
            raise RuntimeError("文档已不存在。")
        processor: DocumentOCSRProcessor = request.app.state.document_processor
        try:
            result = processor.process(str(current["inputPath"]), run_ocsr=False)
            result_path = persist_document_result_atomic(result)
            document_store.update(
                document_id,
                status="review_pending",
                message="区域检测完成，请人工审核后再识别 molecule 区域。",
                resultPath=str(result_path),
            )
            return {
                "documentId": document_id,
                "pageCount": int((result.get("summary") or {}).get("page_count") or 0),
                "regionCount": int((result.get("summary") or {}).get("region_count") or 0),
            }
        except Exception:
            document_store.update(document_id, status="failed", message="文档区域检测失败，请检查文件后重试。")
            raise

    job_store.submit_task(
        job,
        run_detection,
        "正在渲染文档并检测候选区域。",
        "区域检测完成，等待人工审核。",
        "文档区域检测失败，请检查文件格式或电脑端依赖后重试。",
    )
    return job


@api.get("/documents/{document_id}")
def get_document(
    document_id: str,
    request: Request,
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    store = _mobile_document_store(request)
    manifest = store.load(document_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="文档不存在。")
    result: dict[str, Any] | None = None
    if manifest.get("resultPath"):
        try:
            _manifest, result = store.load_result(document_id)
        except FileNotFoundError:
            result = None
    return _document_dto(request, manifest, result)


@api.patch("/documents/{document_id}/regions/{region_id}")
@api.put("/documents/{document_id}/regions/{region_id}")
def edit_document_region(
    document_id: str,
    region_id: str,
    payload: DocumentRegionEditRequest,
    request: Request,
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    if len(payload.bbox) != 4:
        raise HTTPException(status_code=422, detail="区域坐标必须包含 x1、y1、x2、y2。")
    manifest, result = _load_mobile_document_result(request, document_id)
    try:
        updated = apply_region_edits(result, [{
            "action": "update",
            "region_id": region_id,
            "bbox": payload.bbox,
            "region_type": payload.region_type,
            "confirmed": payload.confirmed,
            "note": payload.note,
        }])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated.setdefault("summary", {})["page_count"] = len(updated.get("pages", []))
    updated["summary"]["detection_error_count"] = len(updated.get("detection_errors", []))
    persist_document_result_atomic(updated)
    document = _document_dto(request, manifest, updated)
    return next(region for region in document["regions"] if region["regionId"] == region_id)


@api.post("/documents/{document_id}/regions/{region_id}/recognize", status_code=202)
def recognize_document_region(
    document_id: str,
    region_id: str,
    payload: DocumentRegionRecognizeRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="请先人工确认该区域为单个 molecule。")
    document_store = _mobile_document_store(request)
    _manifest, result = _load_mobile_document_result(request, document_id)
    region = next((item for item in result.get("regions", []) if str(item.get("region_id")) == region_id), None)
    if not isinstance(region, dict):
        raise HTTPException(status_code=404, detail="区域不存在。")
    region_type = str(region.get("region_type") or "unknown")
    if region_type == "reaction_like" or region_type.startswith("reaction"):
        raise HTTPException(status_code=409, detail="reaction_like 区域只做反应流程分流，本阶段不解析。")
    if region_type != "molecule":
        raise HTTPException(status_code=422, detail="只有人工确认的 molecule 区域才能进入 OCSR。")

    job_store: JobStore = request.app.state.job_store
    existing_job_id = str(region.get("recognition_job_id") or "")
    existing_job = job_store.load(existing_job_id) if existing_job_id else None
    if existing_job and existing_job.get("status") in {"queued", "running"}:
        return existing_job

    try:
        updated = apply_region_edits(result, [{
            "action": "confirm",
            "region_id": region_id,
            "region_type": "molecule",
            "note": payload.note or "Confirmed from HarmonyOS region review.",
        }])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = job_store.create(
        input_kind="document_region",
        analysis_id="",
        owner_user_id=str(user["user_id"]),
        message="已确认分子区域，等待 OCSR。",
    )
    job.update({"documentId": document_id, "regionId": region_id})
    job_store.save(job)
    queued_region = next(item for item in updated["regions"] if str(item.get("region_id")) == region_id)
    queued_region["status"] = "queued"
    queued_region["recognition_job_id"] = str(job["jobId"])
    persist_document_result_atomic(updated)

    def run_region_ocsr() -> dict[str, Any]:
        _current_manifest, current = document_store.load_result(document_id)
        current_region = next(
            (item for item in current.get("regions", []) if str(item.get("region_id")) == region_id),
            None,
        )
        if not isinstance(current_region, dict):
            raise RuntimeError("待识别区域不存在。")
        if current_region.get("region_type") != "molecule" or not is_region_confirmed(current_region):
            raise RuntimeError("区域尚未被人工确认为 molecule。")
        processor: DocumentOCSRProcessor = request.app.state.document_processor
        analysis_id = uuid4().hex
        run_dir = image_run_dir(analysis_id, config.RUNS_DIR)
        run_dir.mkdir(parents=True, exist_ok=True)
        processor.report_generator.output_dir = run_dir
        try:
            with INFERENCE_LOCK:
                processor.recognize_region(
                    current_region,
                    current.get("pages", []),
                    str(current.get("output_dir") or config.DOCUMENT_OUTPUT_DIR),
                    screen=True,
                    analysis_id=analysis_id,
                )
            report = current_region.get("report") if isinstance(current_region.get("report"), dict) else {}
            crop_path = Path(str(current_region.get("crop_path") or ""))
            if crop_path.is_file():
                copied_input = run_dir / f"input{crop_path.suffix.lower() or '.png'}"
                shutil.copy2(crop_path, copied_input)
                report.setdefault("input", {})["path"] = str(copied_input)
            report["analysis_id"] = analysis_id
            _persist_manual_report(report, run_dir, str(user["user_id"]))
            if current_region.get("status") != "recognized":
                raise RuntimeError(str(current_region.get("message") or "区域识别未成功。"))
            return {"documentId": document_id, "regionId": region_id, "analysisId": analysis_id}
        finally:
            persist_document_result_atomic(current)

    job_store.submit_task(
        job,
        run_region_ocsr,
        "已通过人工确认，正在进行区域 OCSR。",
        "区域识别完成。",
        "区域识别未完成，请检查区域边界后重试。",
    )
    return job


def _owned_job(store: JobStore, job_id: str, user: dict[str, Any]) -> dict[str, Any]:
    job = store.load(job_id)
    if job is None or (
        job.get("ownerUserId") and str(job.get("ownerUserId")) != str(user.get("user_id"))
    ):
        raise HTTPException(status_code=404, detail="任务不存在。")
    return job


@api.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    _owned_job(store, job_id, user)
    try:
        return store.pause(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    _owned_job(store, job_id, user)
    try:
        return store.resume(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    _owned_job(store, job_id, user)
    try:
        return store.cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    job = _owned_job(store, job_id, user)
    response = dict(job)
    if job.get("status") == "completed" and job.get("analysisId"):
        repository = AnalysisRepository()
        analysis_id = str(job["analysisId"])
        report = repository.load_report(analysis_id)
        row = repository.get_analysis(analysis_id)
        if report:
            response["result"] = _mobile_result(report, request, row)
        else:
            response["status"] = "failed"
            response["message"] = "任务已完成，但分析报告不存在。"
    return response


@api.post("/analyze-smiles")
async def analyze_smiles(
    payload: SmilesRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    validation = validate_smiles(payload.smiles)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail=validation["error"])
    analysis_id = uuid4().hex
    run_dir = image_run_dir(analysis_id, config.RUNS_DIR)

    def generate() -> dict[str, Any]:
        generator = MoleculeReportGenerator("manual", run_dir)
        report = generator.generate(smiles=payload.smiles.strip(), analysis_id=analysis_id)
        _persist_manual_report(report, run_dir, str(user["user_id"]))
        return report

    try:
        report = await run_in_threadpool(generate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SMILES 分析失败：{exc}") from exc
    row = AnalysisRepository().get_analysis(analysis_id)
    return _mobile_result(report, request, row)


@api.get("/analyses")
def list_analyses(
    request: Request,
    query: str = "",
    status: str = "all",
    scope: str = "all",
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    favorites_only: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if status not in {"all", "success", "review_needed", "rejected", "failed"}:
        raise HTTPException(status_code=400, detail="不支持的历史状态筛选。")
    if scope not in {"all", "mine"}:
        raise HTTPException(status_code=400, detail="scope 仅支持 all 或 mine。")
    if _is_developer(user):
        selected_owner_id = str(user["user_id"]) if scope == "mine" else owner_user_id
    else:
        if owner_user_id and owner_user_id != str(user["user_id"]):
            raise HTTPException(status_code=403, detail="普通用户只能查看自己的历史记录。")
        selected_owner_id = str(user["user_id"])
    repository = AnalysisRepository()
    if status == "review_needed":
        # The repository's legacy filter only looks at recognition_decision.
        # Mobile review state also includes an otherwise accepted image that
        # has not yet been human-confirmed.
        candidates = repository.list_analyses(
            query=query,
            status_filter="all",
            favorites_only=favorites_only,
            owner_user_id=selected_owner_id,
            limit=1000,
            offset=0,
        )
        reviewed = [
            (row, repository.load_report(str(row["analysis_id"])))
            for row in candidates
        ]
        filtered = [
            (row, report)
            for row, report in reviewed
            if report is not None
            and human_review_state(report).get("required")
            and not human_review_state(report).get("confirmed")
        ]
        page = filtered[offset:offset + limit + 1]
        has_more = len(page) > limit
        page = page[:limit]
        return {
            "items": [_history_item(row, request, report) for row, report in page],
            "offset": offset,
            "limit": limit,
            "hasMore": has_more,
            "nextOffset": offset + len(page) if has_more else None,
        }
    rows = repository.list_analyses(
        query=query,
        status_filter=status,
        favorites_only=favorites_only,
        owner_user_id=selected_owner_id,
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            _history_item(row, request, repository.load_report(str(row["analysis_id"])))
            for row in rows
        ],
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": offset + len(rows) if has_more else None,
    }


@api.get("/feedback")
def list_feedback_items(
    request: Request,
    status: str = Query(default="pending"),
    query: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    allowed_statuses = {"all", "pending", "verified", "rejected", "returned", "duplicate", "license_unclear"}
    if status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="不支持的反馈审核状态。")
    service = _feedback_service()
    all_items = service.list_items(status=status, query=query, limit=10000)
    page = all_items[offset:offset + limit]
    status_counts: dict[str, int] = {}
    for item in service.list_items(status="all", limit=10000):
        item_status = str(item.get("review_status") or "pending")
        status_counts[item_status] = status_counts.get(item_status, 0) + 1
    return {
        "items": [_feedback_item_dto(item, request) for item in page],
        "offset": offset,
        "limit": limit,
        "total": len(all_items),
        "hasMore": offset + len(page) < len(all_items),
        "statusCounts": status_counts,
    }


@api.get("/feedback/manifest")
def export_feedback_training_manifest(
    request: Request,
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    destination = config.DATA_DIR / "mobile_export_work" / "feedback" / "verified_feedback_manifest.csv"
    result = _feedback_service().export_verified_manifest(destination, split="train")
    store: MobileExportStore = request.app.state.mobile_export_store
    registered = store.register(
        destination,
        "verified_feedback_manifest.csv",
        "text/csv; charset=utf-8",
        ttl_seconds=MOBILE_EXPORT_TTL_SECONDS,
    )
    export_id = str(registered["export_id"])
    expires = int(registered["expires_at"])
    url = request.url_for("media_export", export_id=export_id)
    return {
        "exportedCount": int(result.get("exported_count") or 0),
        "skipped": result.get("skipped") or {},
        "item": {
            "format": "csv",
            "label": "已核验训练 Manifest",
            "available": True,
            "reason": "",
            "formalOnly": True,
            "fileName": str(registered["filename"]),
            "contentType": str(registered["content_type"]),
            "sizeBytes": int(registered["size_bytes"]),
            "downloadUrl": str(url.include_query_params(
                expires=str(expires),
                token=_expiring_media_token("export", export_id, expires),
            )),
            "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        },
    }


@api.get("/feedback/{analysis_id}")
def get_feedback_item(
    analysis_id: str,
    request: Request,
    _user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    item = _feedback_service().get_item(analysis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="反馈样本不存在。")
    return _feedback_item_dto(item, request, detail=True)


@api.patch("/feedback/{analysis_id}/review")
@api.put("/feedback/{analysis_id}/review")
def review_feedback_item(
    analysis_id: str,
    payload: FeedbackReviewRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_developer),
) -> dict[str, Any]:
    service = _feedback_service()
    reviewer = str(user.get("display_name") or user.get("username") or user.get("user_id") or "").strip()
    try:
        if payload.action == "approve":
            service.approve_for_dataset(analysis_id, payload.note, reviewer=reviewer)
        elif payload.action == "return":
            service.return_for_revision(analysis_id, payload.note, reviewer=reviewer)
        elif payload.action == "reject":
            service.reject_sample(analysis_id, payload.note, reviewer=reviewer)
        elif payload.action == "duplicate":
            if not payload.duplicate_of.strip():
                raise ValueError("标记重复项时必须填写 duplicateOf。")
            service.mark_duplicate(
                analysis_id,
                duplicate_of=payload.duplicate_of.strip(),
                reviewer_notes=payload.note,
                reviewer=reviewer,
            )
        else:
            service.mark_license_unclear(analysis_id, reviewer_notes=payload.note, reviewer=reviewer)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="反馈样本不存在或标注文件不可用。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = service.get_item(analysis_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="反馈样本不存在。")
    return _feedback_item_dto(updated, request, detail=True)


@api.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    report = repository.load_report(analysis_id)
    if row is None or report is None:
        raise HTTPException(status_code=404, detail="分析记录不存在或报告文件不可用。")
    _ensure_analysis_access(row, user)
    return _mobile_result(report, request, row)


@api.get("/analyses/{analysis_id}/exports")
def get_analysis_export_center(
    analysis_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    row = AnalysisRepository().get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    _ensure_analysis_access(row, user)
    return _analysis_exports(analysis_id, request)


@api.get("/analyses/{analysis_id}/exports/{export_format}")
def get_analysis_export_format(
    analysis_id: str,
    export_format: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    row = AnalysisRepository().get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    _ensure_analysis_access(row, user)
    return _export_item(_analysis_exports(analysis_id, request), export_format)


@api.patch("/analyses/{analysis_id}/favorite")
@api.put("/analyses/{analysis_id}/favorite")
def update_favorite(
    analysis_id: str,
    payload: FavoriteRequest,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    _ensure_analysis_access(row, user)
    repository.set_favorite(analysis_id, payload.favorite)
    return {"analysisId": analysis_id, "isFavorite": payload.favorite}


@api.delete("/analyses/{analysis_id}", status_code=204)
def delete_analysis_index(
    analysis_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> Response:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    _ensure_analysis_access(row, user)
    repository.delete_analysis(analysis_id)
    return Response(status_code=204)


@api.post("/analyses/{analysis_id}/review")
def review_analysis(
    analysis_id: str,
    payload: ReviewRequest,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    report = repository.load_report(analysis_id)
    if row is None or report is None:
        raise HTTPException(status_code=404, detail="分析记录不存在或报告文件不可用。")
    _ensure_analysis_access(row, user)
    if (report.get("input") or {}).get("type") != "image":
        raise HTTPException(status_code=409, detail="手动 SMILES 结果不需要图片结构复核。")

    note = (payload.note or "").strip() or None
    if payload.action != "confirm" and (payload.smiles or "").strip():
        raise HTTPException(status_code=422, detail="只有确认结构时才能提交修正 SMILES。")
    if payload.action == "unable_to_confirm":
        if payload.reason is None:
            raise HTTPException(status_code=422, detail="请选择无法确认的原因。")
        if payload.reason == "other" and note is None:
            raise HTTPException(status_code=422, detail="选择其他原因时必须填写备注。")
        if human_review_state(report).get("confirmed"):
            raise HTTPException(status_code=409, detail="请先撤销已有确认，再标记为无法确认。")
    elif payload.reason is not None:
        raise HTTPException(status_code=422, detail="无法确认原因只适用于无法确认操作。")

    updated = report
    corrected = (payload.smiles or "").strip()
    if payload.action == "confirm" and corrected and corrected != (current_final_smiles(report) or ""):
        validation = validate_smiles(corrected)
        if not validation["valid"]:
            raise HTTPException(status_code=422, detail=validation["error"])
        updated = apply_smiles_correction(report, corrected, output_dir=report_output_dir(report))
        correction = updated.get("correction") or {}
        if correction.get("last_error"):
            raise HTTPException(status_code=422, detail=str(correction["last_error"]))
    if payload.action == "confirm":
        updated = confirm_structure(updated)
        review = human_review_state(updated)
        if not review.get("confirmed"):
            raise HTTPException(status_code=422, detail=str(review.get("last_error") or "结构无法确认。"))
    elif payload.action == "unable_to_confirm":
        updated = mark_structure_unable_to_confirm(updated)
    elif payload.action == "revoke":
        if not human_review_state(updated).get("confirmed"):
            raise HTTPException(status_code=409, detail="当前结构尚未确认，无需撤销。")
        updated = revoke_structure_confirmation(updated)

    _attach_review_audit(updated, user, payload.reason, note)

    report_path = save_report_for_existing_run(updated)
    if report_path is None:
        report_path_text = str(row.get("report_path") or "").strip()
        if not report_path_text:
            raise HTTPException(status_code=500, detail="无法定位报告文件。")
        indexed_path = Path(report_path_text).expanduser().resolve()
        # Legacy batch rows may still point at the shared batch container.
        # Never replace that container with one reviewed report.
        if indexed_path.name == "batch_ui_result.json":
            report_path = (
                indexed_path.parent / "batch_ui_result_reports" / f"{analysis_id}.json"
            ).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            report_path = indexed_path
        report_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    record_report(updated, report_path)
    refreshed = AnalysisRepository().get_analysis(analysis_id)
    return _mobile_result(updated, request, refreshed)


# Compatibility endpoints for the old one-page ArkTS prototype.
@api.post("/analyze-sample/{sample_id}")
async def analyze_sample(
    sample_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    path = _sample_file(sample_id)
    run = create_image_run_from_file(path, original_filename=path.name, runs_root=config.RUNS_DIR)
    try:
        generator: MoleculeReportGenerator = request.app.state.generator
        report = await run_in_threadpool(_run_analysis, generator, run, str(user["user_id"]))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型分析失败：{exc}") from exc
    return _mobile_result(report, request, AnalysisRepository().get_analysis(run.analysis_id))


@api.post("/analyze-image")
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if file.content_type and file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="仅支持 PNG、JPEG、WEBP 或 BMP 图片。")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    _validate_image(payload)
    filename = _clean_filename(file.filename, file.content_type)
    run = create_image_run_from_bytes(payload, filename, runs_root=config.RUNS_DIR)
    try:
        generator: MoleculeReportGenerator = request.app.state.generator
        report = await run_in_threadpool(_run_analysis, generator, run, str(user["user_id"]))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型分析失败：{exc}") from exc
    return _mobile_result(report, request, AnalysisRepository().get_analysis(run.analysis_id))


def _analysis_structure_file(analysis_id: str) -> Path:
    if not ANALYSIS_ID_PATTERN.fullmatch(analysis_id):
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    repository = AnalysisRepository()
    report = repository.load_report(analysis_id)
    row = repository.get_analysis(analysis_id)
    if report is None or row is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    try:
        structure_value = (report.get("images") or {}).get("redrawn_molecule")
        structure_path = Path(str(structure_value)).expanduser().resolve()
        report_root = Path(str(row.get("report_path") or "")).expanduser().resolve().parent
        if not _managed_analysis_media_path(structure_path, report_root):
            raise ValueError("structure path is outside managed roots")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="结构图不存在。") from exc
    if not structure_path.is_file():
        raise HTTPException(status_code=404, detail="结构图不存在。")
    return structure_path


def _analysis_input_file(analysis_id: str) -> Path:
    if not ANALYSIS_ID_PATTERN.fullmatch(analysis_id):
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    repository = AnalysisRepository()
    report = repository.load_report(analysis_id)
    row = repository.get_analysis(analysis_id)
    if report is None or row is None or (report.get("input") or {}).get("type") != "image":
        raise HTTPException(status_code=404, detail="原始图片不存在。")
    try:
        input_value = (report.get("input") or {}).get("path")
        input_path = Path(str(input_value)).expanduser().resolve()
        report_root = Path(str(row.get("report_path") or "")).expanduser().resolve().parent
        if not _managed_analysis_media_path(input_path, report_root):
            raise ValueError("input path is outside managed roots")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="原始图片不存在。") from exc
    if not input_path.is_file() or input_path.suffix.lower() not in config.SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="原始图片不存在。")
    return input_path


@api.get("/analyses/{analysis_id}/structure", name="analysis_structure")
def analysis_structure(analysis_id: str) -> FileResponse:
    path = _analysis_structure_file(analysis_id)
    return FileResponse(path, media_type="image/png", filename=f"{analysis_id}.png")


@app.get("/media/v1/analyses/{analysis_id}/structure", name="media_analysis_structure")
def media_analysis_structure(analysis_id: str, token: str = Query(default="")) -> FileResponse:
    _check_media_token("analysis", analysis_id, token)
    path = _analysis_structure_file(analysis_id)
    return FileResponse(path, media_type="image/png", filename=f"{analysis_id}.png")


@app.get("/media/v1/analyses/{analysis_id}/input", name="media_analysis_input")
def media_analysis_input(analysis_id: str, token: str = Query(default="")) -> FileResponse:
    _check_media_token("analysis-input", analysis_id, token)
    path = _analysis_input_file(analysis_id)
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
        filename=f"{analysis_id}{path.suffix.lower()}",
    )


@app.get("/media/v1/avatars/{user_id}", name="media_user_avatar")
def media_user_avatar(user_id: str, token: str = Query(default="")) -> FileResponse:
    _check_media_token("avatar", user_id, token)
    user = AuthRepository().get_user(user_id)
    if user is None or not user.get("avatar_path"):
        raise HTTPException(status_code=404, detail="头像不存在。")
    avatar_root = (config.DATA_DIR / "avatars").resolve()
    try:
        path = Path(str(user["avatar_path"])).expanduser().resolve()
        path.relative_to(avatar_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="头像不存在。") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在。")
    return FileResponse(path, media_type="image/png", filename=f"{user_id}.png")


@app.get("/media/v1/exports/{export_id}", name="media_export")
def media_export(
    export_id: str,
    request: Request,
    expires: int = Query(...),
    token: str = Query(default=""),
) -> FileResponse:
    _check_expiring_media_token("export", export_id, expires, token)
    store: MobileExportStore = request.app.state.mobile_export_store
    metadata = store.load(export_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="导出文件不存在。")
    if int(metadata.get("expires_at") or 0) != expires:
        raise HTTPException(status_code=401, detail="下载令牌与导出文件不匹配。")
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="下载链接已过期，请返回导出中心刷新。")
    return FileResponse(
        str(metadata["path"]),
        media_type=str(metadata["content_type"]),
        filename=str(metadata["filename"]),
    )


@app.get("/media/v1/feedback/{analysis_id}/image", name="media_feedback_image")
def media_feedback_image(
    analysis_id: str,
    token: str = Query(default=""),
) -> FileResponse:
    _check_media_token("feedback-image", analysis_id, token)
    service = _feedback_service()
    item = service.get_item(analysis_id)
    if item is None or not item.get("image_path_abs"):
        raise HTTPException(status_code=404, detail="反馈原图不存在。")
    path = Path(str(item["image_path_abs"])).expanduser().resolve()
    try:
        path.relative_to((service.root / "images").resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="反馈原图路径无效。") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="反馈原图不存在。")
    media_type = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=f"feedback_{safe_stem(analysis_id, 'sample')}.png")


@app.get(
    "/media/v1/documents/{document_id}/pages/{page_number}",
    name="media_document_page",
)
def media_document_page(
    document_id: str,
    page_number: int,
    request: Request,
    token: str = Query(default=""),
) -> FileResponse:
    resource_id = f"{document_id}:{page_number}"
    _check_media_token("document-page", resource_id, token)
    path = _document_page_path(request, document_id, page_number)
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "image/png"),
        filename=f"{document_id}_p{page_number:03d}{path.suffix.lower()}",
    )


app.include_router(public_api)
app.include_router(auth_api)
app.include_router(api)
