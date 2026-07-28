"""Unified decision layer for deciding whether OCSR output is trustworthy."""

from __future__ import annotations

from typing import Any, Literal

import config


RecognitionDecision = Literal["accepted", "accepted_with_warning", "review_needed", "rejected"]
RiskLevel = Literal["low", "medium", "high"]


def _confidence_value(ocsr: dict[str, Any]) -> tuple[float | None, bool]:
    calibrated = ocsr.get("calibrated_confidence")
    if calibrated is not None:
        try:
            return float(calibrated), True
        except (TypeError, ValueError):
            return None, True
    confidence = ocsr.get("confidence")
    if confidence is None:
        return None, False
    try:
        return float(confidence), False
    except (TypeError, ValueError):
        return None, False


def _decision_payload(
    decision: RecognitionDecision,
    risk_level: RiskLevel,
    reason_codes: list[str],
    manual_review_recommended: bool,
    calibrated_confidence: float | None,
    quality_score: float | None,
    message: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "risk_level": risk_level,
        "reason_codes": reason_codes,
        "manual_review_recommended": manual_review_recommended,
        "calibrated_confidence": calibrated_confidence,
        "quality_score": quality_score,
        "message": message,
    }


def decide_recognition(report: dict[str, Any]) -> dict[str, Any]:
    """Build a report-level recognition decision from OCSR, chemistry, and quality signals."""
    input_data = report.get("input") or {}
    if input_data.get("type") == "smiles":
        return _decision_payload(
            "accepted",
            "low",
            ["manual_smiles_input"],
            False,
            None,
            None,
            "手动 SMILES 输入由用户负责，不作为图像识别结果评估。",
        )

    routing = report.get("production_routing") or {}
    if routing:
        reasons = list(routing.get("reason_codes") or [])
        if routing.get("decision") == "recognition_failed":
            return _decision_payload(
                "rejected", "high", reasons, True, None, None,
                "Recognition failed: neither model produced a parseable candidate.",
            )
        if routing.get("review_required") or routing.get("decision") == "fallback_candidate":
            return _decision_payload(
                "review_needed", "high", reasons, True, None, None,
                "The model candidate requires review and is not verified ground truth.",
            )

    ocsr = report.get("ocsr") or {}
    validation = report.get("validation") or {}
    image_quality = report.get("image_quality") or {}
    quality_score = image_quality.get("quality_score")
    try:
        quality_score = float(quality_score) if quality_score is not None else None
    except (TypeError, ValueError):
        quality_score = None
    confidence, is_calibrated = _confidence_value(ocsr)
    calibrated_confidence = confidence if is_calibrated else None
    consensus = ocsr.get("consensus") or {}
    reason_codes = _quality_reason_codes(image_quality, quality_score)

    if ocsr.get("backend") == "ensemble" and consensus and consensus.get("decision") == "review_needed":
        return _decision_from_consensus(consensus, reason_codes, calibrated_confidence, quality_score)
    if ocsr.get("status") != "success" or not (ocsr.get("predicted_smiles") or ocsr.get("smiles")):
        return _decision_payload(
            "rejected",
            "high",
            sorted(set(reason_codes + ["no_valid_ocsr_result"])),
            True,
            calibrated_confidence,
            quality_score,
            "没有可采信的 OCSR 结果。",
        )
    if not validation.get("valid"):
        return _decision_payload(
            "rejected",
            "high",
            sorted(set(reason_codes + ["rdkit_invalid"])),
            True,
            calibrated_confidence,
            quality_score,
            "模型返回的 SMILES 不能通过 RDKit 校验。",
        )
    if ocsr.get("strategy_agreement") is False:
        return _decision_payload(
            "review_needed",
            "high",
            sorted(set(reason_codes + ["strategy_disagreement"])),
            True,
            calibrated_confidence,
            quality_score,
            "多个预处理策略返回了不同的有效结构，需要人工审核。",
        )
    if ocsr.get("backend") == "ensemble" and consensus:
        return _decision_from_consensus(consensus, reason_codes, calibrated_confidence, quality_score)

    structural_reasons = _structure_review_reasons(report)
    reason_codes.extend(structural_reasons)
    if quality_score is not None and quality_score < config.DECISION_MIN_IMAGE_QUALITY:
        return _decision_payload(
            "review_needed",
            "high",
            sorted(set(reason_codes)),
            True,
            calibrated_confidence,
            quality_score,
            "图片质量低，RDKit 合法性不足以自动确认与原图一致。",
        )
    if is_calibrated and confidence is not None and confidence >= config.DECISION_ACCEPT_THRESHOLD and not structural_reasons:
        return _decision_payload(
            "accepted",
            "low",
            sorted(set(reason_codes + ["calibrated_confidence_passed"])),
            False,
            calibrated_confidence,
            quality_score,
            "单模型结果通过已校准置信度阈值和质量检查。",
        )
    if is_calibrated and confidence is not None and confidence < config.DECISION_REVIEW_THRESHOLD:
        return _decision_payload(
            "review_needed",
            "high",
            sorted(set(reason_codes + ["calibrated_confidence_low"])),
            True,
            calibrated_confidence,
            quality_score,
            "模型置信度处于低区间，需要人工确认。",
        )
    if structural_reasons:
        return _decision_payload(
            "review_needed",
            "medium",
            sorted(set(reason_codes + ["structure_requires_review"])),
            True,
            calibrated_confidence,
            quality_score,
            "结构包含电荷、同位素、手性、多片段或结构提示，需要人工确认。",
        )
    if config.DECISION_REQUIRE_CALIBRATED_CONFIDENCE:
        return _decision_payload(
            "review_needed",
            "medium",
            sorted(set(reason_codes + ["calibrated_confidence_missing"])),
            True,
            calibrated_confidence,
            quality_score,
            "当前后端未提供已校准置信度，不能自动接受。",
        )
    return _decision_payload(
        "accepted_with_warning",
        "medium",
        sorted(set(reason_codes + ["single_backend_only", "uncalibrated_confidence"])),
        True,
        calibrated_confidence,
        quality_score,
        "单模型给出 RDKit 可解析结构，但未证明与原图一致，建议人工抽查。",
    )


def _quality_reason_codes(image_quality: dict[str, Any], quality_score: float | None) -> list[str]:
    reason_codes = [str(item) for item in image_quality.get("reason_codes") or []]
    if quality_score is not None and quality_score < config.DECISION_MIN_IMAGE_QUALITY:
        reason_codes.append("low_image_quality")
    return reason_codes


def _decision_from_consensus(
    consensus: dict[str, Any],
    quality_reasons: list[str],
    calibrated_confidence: float | None,
    quality_score: float | None,
) -> dict[str, Any]:
    decision = str(consensus.get("decision") or "")
    status = str(consensus.get("status") or "")
    reasons = list(quality_reasons)
    reasons.extend([str(item) for item in consensus.get("reason_codes") or []])
    if decision in {"accepted", "accepted_with_warning"} and status == "agreement":
        return _decision_payload(
            "accepted_with_warning",
            "medium",
            sorted(set(reasons + ["multi_backend_agreement", "agreement_not_ground_truth"])),
            True,
            calibrated_confidence,
            quality_score,
            "Model agreement is recorded, but it is not verified ground truth.",
        )
    if quality_score is not None and quality_score < config.DECISION_MIN_IMAGE_QUALITY:
        return _decision_payload(
            "review_needed",
            "high",
            sorted(set(reasons + ["low_image_quality"])),
            True,
            calibrated_confidence,
            quality_score,
            "即使候选可解析，图片质量不足，仍需人工确认。",
        )
    if decision == "accepted_with_warning" and config.DECISION_REQUIRE_CALIBRATED_CONFIDENCE:
        return _decision_payload(
            "review_needed",
            "medium",
            sorted(set(reasons + ["calibrated_confidence_missing", "single_backend_only"])),
            True,
            calibrated_confidence,
            quality_score,
            "只有一个有效候选且缺少已校准置信度，生产模式下需要人工确认。",
        )
    if decision in {"accepted", "accepted_with_warning"}:
        return _decision_payload(
            "accepted_with_warning",
            "medium",
            sorted(set(reasons + ["single_backend_only"])),
            True,
            calibrated_confidence,
            quality_score,
            "只有一个有效候选或证据不足，建议人工确认。",
        )
    if decision == "review_needed":
        return _decision_payload(
            "review_needed",
            "high",
            sorted(set(reasons + ["backend_disagreement"])),
            True,
            calibrated_confidence,
            quality_score,
            "多个后端返回不同有效结构，不能自动选择。",
        )
    return _decision_payload(
        "rejected",
        "high",
        sorted(set(reasons + ["ensemble_rejected"])),
        True,
        calibrated_confidence,
        quality_score,
        "ensemble 没有可采信候选。",
    )


def _structure_review_reasons(report: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    identity = report.get("chemical_identity") or {}
    if identity.get("fragment_count") and int(identity.get("fragment_count") or 0) > 1:
        reasons.append("multiple_fragments")
    if identity.get("formal_charge") not in {None, 0}:
        reasons.append("formal_charge_present")
    if identity.get("stereocenter_count") and int(identity.get("stereocenter_count") or 0) > 0:
        reasons.append("stereochemistry_present")
    warnings = report.get("structure_warnings") or []
    for warning in warnings:
        text = str(warning)
        if "wildcard" in text.lower() or "*" in text:
            reasons.append("wildcard_or_query_structure")
        if "metal" in text.lower() or "金属" in text:
            reasons.append("metal_or_coordination_structure")
    return reasons


def apply_recognition_decision(report: dict[str, Any]) -> dict[str, Any]:
    """Attach the unified decision block to a report and its OCSR block."""
    decision = decide_recognition(report)
    report["recognition_decision"] = decision
    if isinstance(report.get("ocsr"), dict):
        report["ocsr"]["decision"] = decision["decision"]
        report["ocsr"]["risk_level"] = decision["risk_level"]
        report["ocsr"]["manual_review_recommended"] = decision["manual_review_recommended"]
    return report
