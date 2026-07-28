"""Multi-backend OCSR candidate collection, comparison and ranking."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from itertools import combinations
from typing import Any, Callable, Mapping

from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem, rdMolDescriptors

import config
from src.chem.smiles_validator import smiles_to_mol, suppress_rdkit_parse_errors, validate_smiles
from src.runtime.metadata import dependency_versions, git_commit

from .base import BaseOCSRAdapter, OCSRResult
from .decimer_adapter import DECIMERAdapter
from .molscribe_adapter import MolScribeAdapter


AdapterFactory = Callable[[], BaseOCSRAdapter]


DEFAULT_FACTORIES: dict[str, AdapterFactory] = {
    "molscribe": MolScribeAdapter,
    "decimer": DECIMERAdapter,
}


def _default_factories(runtime_config: Mapping[str, Any] | None = None) -> dict[str, AdapterFactory]:
    runtime = {
        "molscribe_device": config.OCSR_DEVICE or "auto",
        "decimer_device": config.DECIMER_DEVICE or "auto",
        "visible_gpu_index": None,
        **dict(runtime_config or {}),
    }
    return {
        "molscribe": lambda: MolScribeAdapter(device=runtime.get("molscribe_device")),
        "decimer": lambda: DECIMERAdapter(
            device=runtime.get("decimer_device"),
            visible_gpu_index=runtime.get("visible_gpu_index"),
        ),
    }


def _normalized_backends(backends: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = list(backends or config.OCSR_ENSEMBLE_BACKENDS)
    normalized = [backend.strip().lower() for backend in requested if backend.strip()]
    return normalized


def _priority_index(backend: str, priority: list[str]) -> int:
    return priority.index(backend) if backend in priority else len(priority)


def _mol_identity(smiles: str | None) -> dict[str, Any]:
    validation = validate_smiles(smiles)
    identity: dict[str, Any] = {
        "valid": validation["valid"],
        "canonical_smiles": validation["canonical_smiles"],
        "error": validation["error"],
        "inchikey": None,
        "formula": None,
        "atom_count": None,
        "formal_charge": None,
    }
    if not validation["valid"]:
        return identity
    mol = smiles_to_mol(str(validation["canonical_smiles"]))
    if mol is None:
        identity["valid"] = False
        identity["error"] = "RDKit 无法构建分子对象。"
        return identity
    try:
        with suppress_rdkit_parse_errors():
            identity["inchikey"] = Chem.MolToInchiKey(mol)
    except Exception as exc:
        identity["inchikey_error"] = str(exc)
    try:
        identity["formula"] = rdMolDescriptors.CalcMolFormula(mol)
    except Exception as exc:
        identity["formula_error"] = str(exc)
    identity["atom_count"] = int(mol.GetNumAtoms())
    identity["formal_charge"] = int(Chem.GetFormalCharge(mol))
    return identity


def candidate_from_result(result: OCSRResult) -> dict[str, Any]:
    """Convert one backend result into a traceable candidate dictionary."""
    identity = _mol_identity(result.smiles)
    error = None
    if result.status != "success":
        error = result.message
    elif not identity["valid"]:
        error = identity["error"]
    candidate = {
        "backend": result.backend,
        "raw_smiles": result.smiles,
        "canonical_smiles": identity["canonical_smiles"],
        "valid": bool(identity["valid"]),
        "confidence": result.confidence,
        "inference_time_ms": result.inference_time_ms,
        "model_name": result.model_name,
        "model_version": result.model_version,
        "model_sha256": result.model_sha256,
        "device": result.device,
        "package_version": result.package_version,
        "status": result.status,
        "message": result.message,
        "error": error,
        "result_origin": result.result_origin,
        "inference_strategy": result.result_origin or "backend_default",
        "inchikey": identity["inchikey"],
        "formula": identity["formula"],
        "atom_count": identity["atom_count"],
        "formal_charge": identity["formal_charge"],
        "raw_result": result.to_dict(),
    }
    candidate["risk_hints"] = _candidate_risk_hints(candidate)
    return candidate


def _candidate_risk_hints(candidate: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if candidate.get("status") != "success":
        hints.append("recognition_failed")
    if not candidate.get("raw_smiles"):
        hints.append("missing_smiles")
    if candidate.get("raw_smiles") and not candidate.get("valid"):
        hints.append("rdkit_invalid")
    confidence = candidate.get("confidence")
    if confidence is None:
        hints.append("uncalibrated_or_missing_confidence")
    else:
        try:
            if float(confidence) < config.DECISION_REVIEW_THRESHOLD:
                hints.append("low_confidence")
        except (TypeError, ValueError):
            hints.append("invalid_confidence")
    if candidate.get("formal_charge") not in {None, 0}:
        hints.append("formal_charge_present")
    return sorted(set(hints))


def compare_candidates(candidate_a: dict[str, Any], candidate_b: dict[str, Any]) -> dict[str, Any]:
    """Return explanatory chemistry differences for two candidates."""
    analysis: dict[str, Any] = {
        "backend_a": candidate_a.get("backend"),
        "backend_b": candidate_b.get("backend"),
        "both_valid": bool(candidate_a.get("valid") and candidate_b.get("valid")),
        "canonical_smiles_equal": bool(
            candidate_a.get("canonical_smiles")
            and candidate_a.get("canonical_smiles") == candidate_b.get("canonical_smiles")
        ),
        "inchikey_equal": bool(
            candidate_a.get("inchikey") and candidate_a.get("inchikey") == candidate_b.get("inchikey")
        ),
        "formula_equal": bool(candidate_a.get("formula") and candidate_a.get("formula") == candidate_b.get("formula")),
        "atom_count_delta": None,
        "charge_delta": None,
        "morgan_tanimoto": None,
        "note": "这些指标只解释候选差异，不能证明哪一个候选与原图一致。",
    }
    if candidate_a.get("atom_count") is not None and candidate_b.get("atom_count") is not None:
        analysis["atom_count_delta"] = int(candidate_a["atom_count"]) - int(candidate_b["atom_count"])
    if candidate_a.get("formal_charge") is not None and candidate_b.get("formal_charge") is not None:
        analysis["charge_delta"] = int(candidate_a["formal_charge"]) - int(candidate_b["formal_charge"])
    if not analysis["both_valid"]:
        return analysis
    mol_a = smiles_to_mol(str(candidate_a.get("canonical_smiles")))
    mol_b = smiles_to_mol(str(candidate_b.get("canonical_smiles")))
    if mol_a is None or mol_b is None:
        return analysis
    try:
        fp_a = AllChem.GetMorganFingerprintAsBitVect(mol_a, 2, nBits=2048)
        fp_b = AllChem.GetMorganFingerprintAsBitVect(mol_b, 2, nBits=2048)
        analysis["morgan_tanimoto"] = round(float(DataStructs.TanimotoSimilarity(fp_a, fp_b)), 6)
    except Exception as exc:
        analysis["tanimoto_error"] = str(exc)
    return analysis


def build_similarity_analysis(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare every pair of valid or attempted backend candidates."""
    return [compare_candidates(a, b) for a, b in combinations(candidates, 2)]


def rank_candidates(
    candidates: list[dict[str, Any]],
    backend_priority: list[str] | None = None,
    reliability_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Decide whether ensemble candidates can be accepted automatically."""
    priority = backend_priority or list(config.OCSR_ENSEMBLE_BACKEND_PRIORITY)
    successful = [candidate for candidate in candidates if candidate.get("status") == "success" and candidate.get("raw_smiles")]
    valid = [candidate for candidate in successful if candidate.get("valid")]
    if not successful:
        return {
            "status": "all_failed",
            "decision": "rejected",
            "risk_level": "high",
            "reason_codes": ["all_backends_failed"],
            "manual_review_recommended": True,
            "recommended_smiles": None,
            "recommended_backend": None,
            "reason": "所有启用的 OCSR 后端均未返回 SMILES。",
            "confidence_policy": "未比较跨模型 confidence。",
        }
    if not valid:
        return {
            "status": "invalid_candidates",
            "decision": "rejected",
            "risk_level": "high",
            "reason_codes": ["rdkit_invalid_candidates"],
            "manual_review_recommended": True,
            "recommended_smiles": None,
            "recommended_backend": None,
            "reason": "至少一个后端返回了 SMILES，但均无法被 RDKit 解析。",
            "confidence_policy": "未比较跨模型 confidence。",
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in valid:
        key = str(candidate.get("inchikey") or candidate.get("canonical_smiles"))
        grouped.setdefault(key, []).append(candidate)
    agreed_groups = [group for group in grouped.values() if len(group) > 1]
    if agreed_groups:
        group = sorted(
            agreed_groups,
            key=lambda members: (
                -len(members),
                _priority_index(str(members[0].get("backend")), priority),
            ),
        )[0]
        representative = sorted(group, key=lambda candidate: _priority_index(str(candidate.get("backend")), priority))[0]
        return {
            "status": "agreement",
            "decision": "accepted_with_warning",
            "risk_level": "medium",
            "reason_codes": ["multi_backend_agreement", "agreement_not_ground_truth"],
            "manual_review_recommended": True,
            "recommended_smiles": representative.get("canonical_smiles"),
            "recommended_backend": "consensus",
            "supporting_backends": [candidate.get("backend") for candidate in group],
            "warning": "Model agreement is not verified ground truth and does not raise the trust level.",
            "reason": "多个后端生成相同标准化分子。",
            "confidence_policy": "未比较跨模型 confidence；一致性来自 RDKit 标准化/InChIKey。",
        }
    if len(valid) == 1:
        candidate = valid[0]
        return {
            "status": "single_valid",
            "decision": "accepted_with_warning",
            "risk_level": "medium",
            "reason_codes": ["single_backend_only", "uncalibrated_confidence"],
            "manual_review_recommended": True,
            "decision_note": "accepted_with_single_backend",
            "recommended_smiles": candidate.get("canonical_smiles"),
            "recommended_backend": candidate.get("backend"),
            "supporting_backends": [candidate.get("backend")],
            "reason": "只有一个后端返回 RDKit 可解析的 SMILES；自动采用，但建议人工抽查。",
            "warning": "仅一个真实后端给出有效结构，可信度低于多模型一致结果。",
            "confidence_policy": "未比较跨模型 confidence。",
        }
    ranked_for_review = sorted(
        valid,
        key=lambda candidate: (
            _priority_index(str(candidate.get("backend")), priority),
        ),
    )
    return {
        "status": "disagreement",
        "decision": "review_needed",
        "risk_level": "high",
        "reason_codes": ["backend_disagreement"],
        "manual_review_recommended": True,
        "recommended_smiles": None,
        "recommended_backend": None,
        "review_candidates": [
            {
                "backend": candidate.get("backend"),
                "canonical_smiles": candidate.get("canonical_smiles"),
                "raw_smiles": candidate.get("raw_smiles"),
                "confidence": candidate.get("confidence"),
                "model_name": candidate.get("model_name"),
            }
            for candidate in ranked_for_review
        ],
        "supporting_backends": [],
        "reason": (
            "多个后端返回了不同的有效分子；系统不会自动选择最终结构，请人工确认。"
        ),
        "warning": "模型分歧未被自动解决，请人工确认。",
        "confidence_policy": "未比较未经校准的跨模型 confidence。",
    }


def combine_ensemble_results(
    raw_results: list[OCSRResult],
    *,
    enabled_backends: list[str] | tuple[str, ...],
    backend_priority: list[str] | tuple[str, ...] | None = None,
    reliability_weights: Mapping[str, float] | None = None,
    elapsed_ms: float | None = None,
) -> OCSRResult:
    """Build the ensemble audit record from already-executed backend outputs."""
    candidates = [candidate_from_result(result) for result in raw_results]
    consensus = rank_candidates(candidates, list(backend_priority or config.OCSR_ENSEMBLE_BACKEND_PRIORITY), reliability_weights)
    similarity = build_similarity_analysis(candidates)
    recommended = consensus.get("recommended_smiles")
    decision = str(consensus.get("decision") or ("accepted" if recommended else "rejected"))
    status = "success" if decision in {"accepted", "accepted_with_warning"} and recommended else "failed"
    return OCSRResult(
        smiles=str(recommended) if recommended else None,
        confidence=None,
        backend="ensemble",
        status=status,
        message=str(consensus.get("reason") or "ensemble inference completed."),
        inference_time_ms=elapsed_ms,
        model_name=f"ensemble({'+'.join(enabled_backends)})",
        model_version=None,
        device="mixed",
        package_version=None,
        git_commit=git_commit(),
        dependency_versions=dependency_versions(),
        result_origin="real_model_ensemble",
        candidates=candidates,
        consensus=consensus,
        similarity_analysis=similarity,
        decision=decision if decision in {"accepted", "accepted_with_warning", "review_needed", "rejected"} else None,
        risk_level=consensus.get("risk_level"),
        manual_review_recommended=consensus.get("manual_review_recommended"),
    )


class EnsembleOCSRAdapter(BaseOCSRAdapter):
    """Run multiple optional OCSR backends and produce a consensus result."""

    backend_name = "ensemble"
    preferred_image_stage = "original"

    def __init__(
        self,
        backends: list[str] | tuple[str, ...] | None = None,
        backend_priority: list[str] | tuple[str, ...] | None = None,
        reliability_weights: Mapping[str, float] | None = None,
        parallel: bool | None = None,
        continue_on_error: bool | None = None,
        total_timeout_seconds: float | None = None,
        adapter_factories: Mapping[str, AdapterFactory] | None = None,
        runtime_config: Mapping[str, Any] | None = None,
        release_after_each_backend: bool | None = None,
    ) -> None:
        factories = dict(adapter_factories or _default_factories(runtime_config))
        self.adapter_factories = factories
        self.runtime_config = dict(runtime_config or {})
        self.enabled_backends = [backend for backend in _normalized_backends(backends) if backend in factories]
        self.backend_priority = list(backend_priority or config.OCSR_ENSEMBLE_BACKEND_PRIORITY)
        self.reliability_weights = dict(reliability_weights or config.OCSR_ENSEMBLE_RELIABILITY_WEIGHTS)
        self.parallel = config.OCSR_ENSEMBLE_PARALLEL if parallel is None else parallel
        self.continue_on_error = config.OCSR_ENSEMBLE_CONTINUE_ON_ERROR if continue_on_error is None else continue_on_error
        self.total_timeout_seconds = float(total_timeout_seconds or config.OCSR_ENSEMBLE_TOTAL_TIMEOUT_SECONDS)
        self.release_after_each_backend = (not self.parallel) if release_after_each_backend is None else bool(release_after_each_backend)
        self.adapters: dict[str, BaseOCSRAdapter] = {}
        self.last_inference_time_ms: float | None = None

    def _adapter(self, backend: str) -> BaseOCSRAdapter:
        if backend not in self.adapters:
            self.adapters[backend] = self.adapter_factories[backend]()
        return self.adapters[backend]

    def release_adapter(self, backend: str) -> None:
        """Drop one cached child adapter reference after serial health probing."""
        adapter = self.adapters.pop(backend, None)
        close = getattr(adapter, "close", None)
        if callable(close):
            close()

    def release_adapters(self) -> None:
        """Drop all cached child adapter references."""
        for backend in list(self.adapters):
            self.release_adapter(backend)

    def _failure_result(self, backend: str, message: str, elapsed_ms: float | None = None) -> OCSRResult:
        return OCSRResult(
            smiles=None,
            confidence=None,
            backend=backend,
            status="failed",
            message=message,
            inference_time_ms=elapsed_ms,
        )

    def _run_backend(self, backend: str, image_path_or_array: Any) -> OCSRResult:
        start = time.perf_counter()
        try:
            return self._adapter(backend).recognize(image_path_or_array)
        except Exception as exc:
            if not self.continue_on_error:
                raise
            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            return self._failure_result(backend, f"{backend} 后端异常：{exc}", elapsed_ms)

    def _run_serial(self, image_path_or_array: Any) -> list[OCSRResult]:
        started = time.perf_counter()
        results: list[OCSRResult] = []
        for backend in self.enabled_backends:
            if self.total_timeout_seconds > 0 and time.perf_counter() - started > self.total_timeout_seconds:
                results.append(self._failure_result(backend, "ensemble 总任务超时，未启动该后端。", 0.0))
                continue
            try:
                results.append(self._run_backend(backend, image_path_or_array))
            finally:
                if self.release_after_each_backend:
                    self.release_adapter(backend)
        return results

    def _run_parallel(self, image_path_or_array: Any) -> list[OCSRResult]:
        results: list[OCSRResult] = []
        future_to_backend: dict[Any, str] = {}
        executor = ThreadPoolExecutor(max_workers=max(1, len(self.enabled_backends)))
        try:
            for backend in self.enabled_backends:
                future_to_backend[executor.submit(self._run_backend, backend, image_path_or_array)] = backend
            timeout = self.total_timeout_seconds if self.total_timeout_seconds > 0 else None
            for future in as_completed(future_to_backend, timeout=timeout):
                results.append(future.result())
        except TimeoutError:
            completed = {result.backend for result in results}
            for backend in self.enabled_backends:
                if backend not in completed:
                    results.append(self._failure_result(backend, "ensemble 并行总任务超时。", self.total_timeout_seconds * 1000))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        ordered = {result.backend: result for result in results}
        return [ordered[backend] for backend in self.enabled_backends if backend in ordered]

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        """Run enabled backends and return the ranked consensus result."""
        started = time.perf_counter()
        if not self.enabled_backends:
            return OCSRResult(None, None, self.backend_name, "failed", "未配置可用的 ensemble 子后端。")
        raw_results = self._run_parallel(image_path_or_array) if self.parallel else self._run_serial(image_path_or_array)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        self.last_inference_time_ms = elapsed_ms
        return combine_ensemble_results(
            raw_results,
            enabled_backends=self.enabled_backends,
            backend_priority=self.backend_priority,
            reliability_weights=self.reliability_weights,
            elapsed_ms=elapsed_ms,
        )

    @property
    def is_available(self) -> bool:
        if not self.enabled_backends:
            return False
        return sum(1 for status in self._child_statuses() if status.get("available")) >= 2

    @property
    def availability_message(self) -> str:
        available = [status["backend"] for status in self._child_statuses() if status.get("available")]
        if len(available) == 1:
            return f"ensemble 至少需要两个真实 OCSR 后端；当前仅可用：{available[0]}。"
        if available:
            return f"ensemble 可运行；当前可用子后端：{', '.join(available)}。"
        return "ensemble 已配置，但当前没有可用的真实 OCSR 子后端。"

    def _child_statuses(self) -> list[dict[str, Any]]:
        statuses: list[dict[str, Any]] = []
        for backend in self.enabled_backends:
            try:
                statuses.append(self._adapter(backend).status())
            except Exception as exc:
                statuses.append({"backend": backend, "available": False, "message": str(exc)})
        return statuses

    def status(self) -> dict[str, Any]:
        statuses = self._child_statuses()
        return {
            "backend": self.backend_name,
            "available": sum(1 for status in statuses if status.get("available")) >= 2,
            "message": self.availability_message,
            "enabled_backends": self.enabled_backends,
            "backend_priority": self.backend_priority,
            "reliability_weights": self.reliability_weights,
            "reliability_weights_policy": "experimental_not_used_for_final_decision",
            "parallel": self.parallel,
            "release_after_each_backend": self.release_after_each_backend,
            "continue_on_error": self.continue_on_error,
            "total_timeout_seconds": self.total_timeout_seconds,
            "device": "mixed",
            "model_name": f"ensemble({'+'.join(self.enabled_backends)})",
            "model_version": None,
            "package_version": None,
            "last_inference_time_ms": self.last_inference_time_ms,
            "child_statuses": statuses,
            "warning": "默认串行运行，避免同时加载多个大型 GPU 模型导致显存压力。",
        }
