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
from threading import Lock
from typing import Any, AsyncIterator, Literal
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
from src.chem.smiles_validator import validate_smiles
from src.runtime.run_store import (
    ImageRun,
    create_image_run_from_bytes,
    create_image_run_from_file,
    image_run_dir,
    report_output_dir,
    save_report_for_existing_run,
    save_run_report,
)
from src.storage.analysis_repository import AnalysisRepository, record_report, utc_now
from src.storage.auth_repository import AuthRepository


MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp"}
AVATAR_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_AVATAR_BYTES = 3 * 1024 * 1024
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


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(alias="displayName", min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="", max_length=40)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ProfileRequest(BaseModel):
    display_name: str = Field(alias="displayName", min_length=1, max_length=40)
    role: str = Field(default="", max_length=40)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(alias="oldPassword", min_length=1, max_length=128)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=128)


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


def _media_token(kind: str, resource_id: str) -> str:
    key = _configured_api_key()
    return hmac.new(key.encode("utf-8"), f"{kind}:{resource_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _check_media_token(kind: str, resource_id: str, token: str) -> None:
    expected = _media_token(kind, resource_id)
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="媒体访问令牌无效。")


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
        input_path.relative_to(report_path.parent)
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
        self.executor.submit(self._execute, str(job["jobId"]), generator, run)

    def _execute(self, job_id: str, generator: MoleculeReportGenerator, run: ImageRun) -> None:
        self.update(job_id, status="running", message="模型正在分析图片。")
        try:
            job = self.load(job_id) or {}
            owner_user_id = str(job.get("ownerUserId") or "") or None
            report = _run_analysis(generator, run, owner_user_id)
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
            self.update(job_id, status="failed", message=f"模型分析失败：{exc}")

    def reconcile_after_restart(self) -> None:
        repository = AnalysisRepository()
        for path in self.root.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or job.get("status") not in {"queued", "running"}:
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
            if report and report.get("status") == "success":
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
    app.state.job_store.reconcile_after_restart()
    try:
        yield
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Molecule Vision Local API", version="2.2.0", lifespan=lifespan)
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
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "appMode": config.APP_MODE,
        "backend": config.OCSR_BACKEND,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "apiVersion": "2.2",
    }


@auth_api.post("/register", status_code=201)
def register_user(payload: RegisterRequest, request: Request) -> dict[str, Any]:
    repository = AuthRepository()
    try:
        user = repository.register(
            payload.username,
            payload.display_name,
            payload.password,
            payload.role,
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
            payload.role,
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
def group_members(request: Request, _user: dict[str, Any] = Depends(require_user)) -> list[dict[str, Any]]:
    return [_user_dto(user, request) for user in AuthRepository().list_users()]


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


@api.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    store: JobStore = request.app.state.job_store
    job = store.load(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
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
    selected_owner_id = str(user["user_id"]) if scope == "mine" else owner_user_id
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


@api.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, request: Request) -> dict[str, Any]:
    repository = AnalysisRepository()
    row = repository.get_analysis(analysis_id)
    report = repository.load_report(analysis_id)
    if row is None or report is None:
        raise HTTPException(status_code=404, detail="分析记录不存在或报告文件不可用。")
    return _mobile_result(report, request, row)


@api.patch("/analyses/{analysis_id}/favorite")
@api.put("/analyses/{analysis_id}/favorite")
def update_favorite(analysis_id: str, payload: FavoriteRequest) -> dict[str, Any]:
    repository = AnalysisRepository()
    if repository.get_analysis(analysis_id) is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
    repository.set_favorite(analysis_id, payload.favorite)
    return {"analysisId": analysis_id, "isFavorite": payload.favorite}


@api.delete("/analyses/{analysis_id}", status_code=204)
def delete_analysis_index(analysis_id: str) -> Response:
    repository = AnalysisRepository()
    if repository.get_analysis(analysis_id) is None:
        raise HTTPException(status_code=404, detail="分析记录不存在。")
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
        report_path = Path(report_path_text).expanduser().resolve()
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
        structure_path.relative_to(report_root)
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
        input_path.relative_to(report_root)
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


app.include_router(public_api)
app.include_router(auth_api)
app.include_router(api)
