"""Conservative production routing for unverified OCSR model candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from rdkit import Chem

from src.chem.standardization import METAL_ATOMIC_NUMBERS
from src.ocsr.model_capabilities import capability_version, model_capability


TRUSTED_GROUND_TRUTH_ORIGINS = {"pubchem", "chembl", "supplementary_sdf", "trusted_database"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parseable(candidate: Mapping[str, Any] | None) -> bool:
    return bool(candidate and candidate.get("valid") and candidate.get("canonical_smiles"))


def _candidate_for(candidates: Iterable[Mapping[str, Any]], backend: str) -> dict[str, Any] | None:
    for candidate in candidates:
        if str(candidate.get("backend") or "").lower() == backend:
            return dict(candidate)
    return None


def _risk_flags(smiles: str | None) -> list[str]:
    if not smiles:
        return []
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return []
    flags: list[str] = []
    if len(Chem.GetMolFrags(mol)) > 1:
        flags.extend(["salt_or_multifragment", "high_risk_structure"])
    if Chem.GetFormalCharge(mol) != 0:
        flags.extend(["formal_charge", "high_risk_structure"])
    if Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False):
        flags.extend(["stereochemistry", "high_risk_structure"])
    if any(atom.GetAtomicNum() in METAL_ATOMIC_NUMBERS for atom in mol.GetAtoms()):
        flags.extend(["metal_coordination", "high_risk_structure"])
    return list(dict.fromkeys(flags))


def _audit_candidate(candidate: Mapping[str, Any] | None, role: str | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    backend = str(candidate.get("backend") or "unknown").lower()
    profile = candidate.get("input_profile") or model_capability(backend).get("profile") or "raw"
    return {
        "backend": backend,
        "candidate_role": role,
        "model_version": candidate.get("model_version") or candidate.get("package_version"),
        "model_hash": candidate.get("model_sha256"),
        "input_profile": profile,
        "execution_status": candidate.get("status") or "failed",
        "parse_status": "parseable" if _parseable(candidate) else "unparseable",
        "candidate_smiles": candidate.get("raw_smiles"),
        "canonical_smiles": candidate.get("canonical_smiles"),
        "inchikey": candidate.get("inchikey"),
        "latency_ms": candidate.get("inference_time_ms"),
    }


def route_model_candidates(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Route DECIMER/MolScribe candidates without promoting predictions to truth."""
    items = [dict(candidate) for candidate in candidates]
    primary = _candidate_for(items, "decimer")
    fallback = _candidate_for(items, "molscribe")
    primary_valid = _parseable(primary)
    fallback_valid = _parseable(fallback)
    selected: dict[str, Any] | None = None
    role: str | None = None
    agreement_status = "no_valid_candidate"
    review_required = False
    reason_codes: list[str] = []

    if primary_valid:
        selected, role = primary, "primary_candidate"
        agreement_status = "primary_only"
        if fallback_valid:
            if primary.get("inchikey") == fallback.get("inchikey") or primary.get("canonical_smiles") == fallback.get("canonical_smiles"):
                agreement_status = "agreement"
                reason_codes.append("model_agreement_not_ground_truth")
            else:
                agreement_status = "model_disagreement"
                review_required = True
                reason_codes.append("model_disagreement")
    elif fallback_valid:
        selected, role = fallback, "fallback_candidate"
        agreement_status = "fallback_only"
        review_required = True
        reason_codes.extend(["primary_unparseable", "fallback_requires_review"])
    else:
        reason_codes.append("recognition_failed")

    risk_flags = _risk_flags(str(selected.get("canonical_smiles"))) if selected else []
    if risk_flags:
        review_required = True
        reason_codes.append("high_risk_structure")
    decision = role or "recognition_failed"
    audit_candidates = [entry for entry in (
        _audit_candidate(primary, "primary_candidate"),
        _audit_candidate(fallback, "fallback_candidate"),
    ) if entry]
    return {
        "decision": decision,
        "selected_backend": selected.get("backend") if selected else None,
        "selected_smiles": selected.get("raw_smiles") if selected else None,
        "selected_canonical_smiles": selected.get("canonical_smiles") if selected else None,
        "selected_inchikey": selected.get("inchikey") if selected else None,
        "agreement_status": agreement_status,
        "agreement_increases_trust": False,
        "review_required": review_required,
        "structure_verified": False,
        "property_analysis_allowed": bool(selected) and role == "primary_candidate" and agreement_status != "model_disagreement",
        "risk_flags": risk_flags,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "dataset_capability_version": capability_version(),
        "candidates": audit_candidates,
        "timestamp": _utc_now(),
    }


def build_recognition_audit(route: Mapping[str, Any]) -> dict[str, Any]:
    """Build the persisted audit block; prediction fields are never truth fields."""
    selected_backend = route.get("selected_backend")
    selected = next(
        (item for item in route.get("candidates") or [] if item.get("backend") == selected_backend),
        {},
    )
    return {
        "backend": selected_backend,
        "model_version": selected.get("model_version"),
        "model_hash": selected.get("model_hash"),
        "input_profile": selected.get("input_profile"),
        "execution_status": selected.get("execution_status") or "failed",
        "parse_status": selected.get("parse_status") or "unparseable",
        "candidate_smiles": selected.get("candidate_smiles"),
        "canonical_smiles": selected.get("canonical_smiles"),
        "inchikey": selected.get("inchikey"),
        "risk_flags": list(route.get("risk_flags") or []),
        "agreement_status": route.get("agreement_status"),
        "review_required": bool(route.get("review_required")),
        "structure_verified": False,
        "dataset_capability_version": route.get("dataset_capability_version"),
        "latency_ms": selected.get("latency_ms"),
        "timestamp": route.get("timestamp") or _utc_now(),
    }


def assert_no_prediction_as_ground_truth(payload: Mapping[str, Any]) -> None:
    """Reject accidental truth/verification claims in model-produced audit data."""
    forbidden = {"ground_truth_smiles", "verified"}.intersection(payload)
    if forbidden:
        raise ValueError(f"model prediction audit contains forbidden truth fields: {sorted(forbidden)}")
    if payload.get("structure_verified") is True:
        raise ValueError("a model prediction cannot be marked structure_verified")
