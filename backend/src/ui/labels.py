"""Centralized Chinese display labels for UI-only presentation."""

from __future__ import annotations

from typing import Any

BACKEND_LABELS = {
    "demo": "演示模式（仅内置样例，不是真实识别）",
    "molscribe": "MolScribe 图像识别",
    "decimer": "DECIMER 图像识别",
    "ensemble": "多模型联合识别",
    "manual": "手动 SMILES 分析",
}

BACKEND_SHORT_LABELS = {
    "demo": "演示模式",
    "molscribe": "MolScribe",
    "decimer": "DECIMER",
    "ensemble": "多模型联合",
    "manual": "手动输入",
}

BACKEND_DESCRIPTIONS = {
    "demo": "只按内置样例文件名返回固定 SMILES，用于演示流程；不能识别任意图片。",
    "molscribe": "真实 OCSR 后端，需要安装 MolScribe 并配置模型权重。",
    "decimer": "真实 OCSR 后端，需要可用 DECIMER/TensorFlow 环境；CPU 会比较慢。",
    "ensemble": "同时运行多个真实 OCSR 后端并比较候选；至少需要两个真实后端可用。",
}

REGION_TYPE_LABELS = {
    "molecule": "分子结构",
    "reaction": "反应式",
    "reaction_arrow": "反应箭头",
    "reaction_condition": "反应条件",
    "reaction_like": "疑似反应式",
    "text": "文本",
    "table": "表格",
    "figure": "普通图像/插图",
    "figure_label": "图表内文字标签",
    "ignore": "忽略",
    "unknown": "未知区域",
    "non_molecule": "非分子区域",
}

STATUS_LABELS = {
    "detected": "已检测",
    "confirmed": "已确认",
    "recognized": "识别成功",
    "failed": "识别失败",
    "skipped": "已跳过",
    "edited": "已修改",
    "deleted": "已删除",
    "success": "成功",
    "unavailable": "不可用",
    "available": "可用",
    "pending": "待处理",
    "true": "是",
    "false": "否",
}

BATCH_COLUMN_LABELS = {
    "filename": "文件名",
    "status": "状态",
    "backend": "识别后端",
    "final_smiles": "最终 SMILES",
    "valid": "是否有效",
    "confidence": "置信度",
    "inference_time_ms": "推理耗时(ms)",
    "message": "失败原因",
    "recognition_decision": "识别决策",
    "recognition_risk_level": "风险等级",
    "manual_review_recommended": "建议人工确认",
    "image_quality_score": "图像质量分",
}

DOCUMENT_COLUMN_LABELS = {
    "document_id": "文档 ID",
    "page_number": "页码",
    "region_id": "区域 ID",
    "bbox_x1": "x1",
    "bbox_y1": "y1",
    "bbox_x2": "x2",
    "bbox_y2": "y2",
    "region_type": "区域类型",
    "detection_confidence": "检测置信度",
    "source": "来源",
    "status": "状态",
    "message": "说明",
    "crop_path": "裁剪图",
    "final_smiles": "最终 SMILES",
    "valid": "是否有效",
    "inference_time_ms": "推理耗时(ms)",
    "processing_time_ms": "区域耗时(ms)",
    "screening_passed": "通过二次筛选",
    "screening_reason": "筛选说明",
    "confirmed": "已确认",
    "annotation_status": "标注状态",
    "review_queued": "已入审核队列",
    "review_annotation_path": "审核标注",
}


def backend_label(backend: str | None, short: bool = False) -> str:
    """Return a user-facing backend label without changing internal values."""
    if backend is None:
        return "未选择"
    mapping = BACKEND_SHORT_LABELS if short else BACKEND_LABELS
    return mapping.get(str(backend), str(backend))


def region_type_label(value: str | None) -> str:
    return REGION_TYPE_LABELS.get(str(value), str(value or ""))


def status_label(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return STATUS_LABELS.get(str(value), str(value or ""))


def localize_region_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a UI-only localized copy of document region rows."""
    localized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["region_type"] = region_type_label(item.get("region_type"))
        item["status"] = status_label(item.get("status"))
        item["source"] = {"detector": "自动检测", "user": "人工添加"}.get(str(item.get("source")), item.get("source"))
        if "valid" in item:
            item["valid"] = status_label(item.get("valid"))
        if "screening_passed" in item:
            item["screening_passed"] = status_label(item.get("screening_passed"))
        localized.append({DOCUMENT_COLUMN_LABELS.get(key, key): value for key, value in item.items()})
    return localized


def localize_batch_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a UI-only localized copy of batch rows."""
    localized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["status"] = status_label(item.get("status"))
        item["backend"] = backend_label(item.get("backend"), short=True)
        item["valid"] = status_label(item.get("valid"))
        localized.append({BATCH_COLUMN_LABELS.get(key, key): value for key, value in item.items()})
    return localized


def real_backend_count(statuses: dict[str, dict[str, Any]]) -> int:
    return sum(1 for backend in ("molscribe", "decimer") if statuses.get(backend, {}).get("available"))


def runnable_backends(
    statuses: dict[str, dict[str, Any]],
    include_demo: bool = False,
    allow_demo_fallback: bool = True,
) -> list[str]:
    """Return backends that users may run from the main selector."""
    options: list[str] = []
    if statuses.get("decimer", {}).get("available"):
        options.append("decimer")
    if statuses.get("molscribe", {}).get("available"):
        options.append("molscribe")
    if real_backend_count(statuses) >= 2:
        options.append("ensemble")
    if include_demo or (allow_demo_fallback and not options):
        options.append("demo")
    return options


def default_backend(
    statuses: dict[str, dict[str, Any]],
    configured: str | None = None,
    allow_demo_fallback: bool = True,
) -> str:
    """Prefer the configured runnable backend, then a deterministic real fallback."""
    options = runnable_backends(statuses, allow_demo_fallback=allow_demo_fallback)
    if configured in options:
        return str(configured)
    if "molscribe" in options:
        return "molscribe"
    if "decimer" in options:
        return "decimer"
    if "ensemble" in options:
        return "ensemble"
    return "demo" if allow_demo_fallback else ""


def unavailable_backends(statuses: dict[str, dict[str, Any]]) -> list[str]:
    unavailable = [backend for backend in ("molscribe", "decimer") if not statuses.get(backend, {}).get("available")]
    if real_backend_count(statuses) < 2:
        unavailable.append("ensemble")
    return unavailable
