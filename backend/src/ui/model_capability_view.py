"""Pure presentation helpers for OCSR capability and verification states."""

from __future__ import annotations

from typing import Any, Mapping

from src.ocsr.model_capabilities import load_model_capabilities


def model_result_status(report: Mapping[str, Any]) -> dict[str, Any]:
    routing = report.get("production_routing") or {}
    audit = report.get("recognition_audit") or {}
    decision = routing.get("decision")
    role_label = {
        "primary_candidate": "Primary model candidate",
        "fallback_candidate": "Fallback candidate",
        "recognition_failed": "Recognition failed",
    }.get(decision, "Model prediction")
    return {
        "candidate_role": role_label,
        "candidate_smiles": routing.get("selected_smiles"),
        "backend_execution_succeeded": audit.get("execution_status") == "success",
        "valid_smiles_produced": audit.get("parse_status") == "parseable",
        "structure_verified": bool(audit.get("structure_verified", False)),
        "requires_review": bool(routing.get("review_required")),
        "prediction_notice": "Prediction is not verified ground truth",
        "ensemble_label": "Experimental ensemble",
        "agreement_status": routing.get("agreement_status"),
        "risk_flags": list(routing.get("risk_flags") or []),
    }


def capability_panel_data() -> dict[str, Any]:
    payload = load_model_capabilities()
    return {
        "capability_version": payload["capability_version"],
        "dataset": payload["dataset"],
        "dataset_role": payload["dataset_role"],
        "scope_notice": payload["scope_notice"],
        "production_defaults": payload["production_defaults"],
        "models": payload["models"],
        "training_supported": False,
        "real_pmc_accuracy_verified": False,
        "style_notice": "Rendered-clean accuracy differs substantially from official-style images; real paper crops remain a separate input domain.",
    }
