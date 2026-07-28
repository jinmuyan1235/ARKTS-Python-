"""Exact-identity evaluation for PubChem-grounded OCSR datasets."""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable

from rdkit import Chem, rdBase
from rdkit.Chem import rdMolDescriptors
from PIL import Image

from src.datasets.trusted_ocsr import sha256_file, validate_trusted_dataset
from src.ocsr.base import OCSRResult
from src.ocsr.decimer_adapter import DECIMERAdapter
from src.ocsr.ensemble import combine_ensemble_results
from src.ocsr.input_normalization import InputNormalizationConfig, get_profile, normalize_ocsr_input
from src.ocsr.molscribe_adapter import MolScribeAdapter
from src.ocsr.reliability import (
    DEFAULT_BACKEND_RELIABILITY_CONFIG, BackendReliabilityConfig,
    classify_backend_failure, run_with_single_retry, sanitize_exception_summary,
)
from src.ocsr.recognizer import MoleculeRecognizer
from src.runtime.metadata import dependency_versions, git_commit


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percent / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _identity(smiles: str | None) -> dict[str, Any]:
    if not str(smiles or "").strip():
        return {"valid": False}
    mol = Chem.MolFromSmiles(str(smiles).strip())
    if mol is None:
        return {"valid": False}
    return {
        "valid": True,
        "canonical": Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False),
        "isomeric": Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True),
        "inchikey": Chem.MolToInchiKey(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "fragments": len(Chem.GetMolFrags(mol)),
        "formal_charge": int(Chem.GetFormalCharge(mol)),
        "elements": sorted(atom.GetSymbol() for atom in mol.GetAtoms()),
        "atom_count": int(mol.GetNumAtoms()),
        "bond_types": sorted(str(bond.GetBondType()) for bond in mol.GetBonds()),
    }


def classify_structural_error(truth: dict[str, Any], predicted: dict[str, Any], result: OCSRResult) -> str:
    """Assign only automatically supportable failure classes."""
    message = str(result.message or "").lower()
    if result.status != "success":
        return result.failure_category or classify_backend_failure(result)
    if not predicted.get("valid"):
        return "invalid_smiles"
    if predicted["canonical"] == truth["canonical"] and predicted["isomeric"] != truth["isomeric"]:
        return "stereochemistry_error"
    if predicted["fragments"] < truth["fragments"]:
        return "missing_fragment"
    if predicted["fragments"] > truth["fragments"]:
        return "extra_fragment"
    if predicted["formal_charge"] != truth["formal_charge"]:
        return "charge_error"
    if predicted["elements"] != truth["elements"] or predicted["atom_count"] != truth["atom_count"]:
        return "wrong_atom"
    if predicted["bond_types"] != truth["bond_types"]:
        return "wrong_bond_order"
    truth_key = str(truth.get("inchikey") or "").split("-", 1)[0]
    predicted_key = str(predicted.get("inchikey") or "").split("-", 1)[0]
    if truth_key and predicted_key and truth_key != predicted_key:
        return "wrong_connectivity"
    return "structural_mismatch_unclassified"


class GPUMemoryMonitor:
    """Best-effort system GPU memory poller; unavailable platforms return None."""
    def __init__(self, interval: float = 0.5, enabled: bool = True) -> None:
        self.interval = interval
        self.enabled = enabled
        self.peak_mib: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _read() -> float | None:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                check=True, capture_output=True, text=True, timeout=3,
            )
            values = [float(line.strip()) for line in result.stdout.splitlines() if line.strip()]
            return sum(values) if values else None
        except Exception:
            return None

    def __enter__(self) -> "GPUMemoryMonitor":
        if not self.enabled:
            return self
        def poll() -> None:
            while not self._stop.is_set():
                value = self._read()
                if value is not None:
                    self.peak_mib = max(self.peak_mib or 0.0, value)
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def _ensemble_flags(result: OCSRResult, truth: dict[str, Any]) -> dict[str, Any]:
    candidates = {str(item.get("backend")): item for item in (result.candidates or [])}
    correct: dict[str, bool] = {}
    identities: set[str] = set()
    for backend in ("molscribe", "decimer"):
        candidate = candidates.get(backend, {})
        identity = _identity(candidate.get("raw_smiles"))
        correct[backend] = bool(identity.get("inchikey") == truth.get("inchikey"))
        if identity.get("valid"):
            identities.add(str(identity.get("inchikey") or identity.get("canonical")))
    accepted = bool(result.smiles and result.decision in {"accepted", "accepted_with_warning"})
    ensemble_correct = bool(_identity(result.smiles).get("inchikey") == truth.get("inchikey"))
    both_available = all(backend in candidates for backend in ("molscribe", "decimer"))
    disagreement = both_available and len(identities) > 1
    return {
        "molscribe_correct": correct["molscribe"], "decimer_correct": correct["decimer"],
        "both_models_correct": correct["molscribe"] and correct["decimer"],
        "only_molscribe_correct": correct["molscribe"] and not correct["decimer"],
        "only_decimer_correct": correct["decimer"] and not correct["molscribe"],
        "both_wrong_but_agree": both_available and not any(correct.values()) and len(identities) == 1,
        "model_disagreement": disagreement,
        "ensemble_correct_accept": accepted and ensemble_correct,
        "ensemble_wrong_accept": accepted and not ensemble_correct,
        "ensemble_correct_reject": not accepted and not any(correct.values()),
        "ensemble_unnecessary_reject": not accepted and any(correct.values()),
        "ensemble_abstention": not accepted,
        "ensemble_candidates_json": json.dumps(result.candidates or [], ensure_ascii=False),
    }


def evaluate_prediction(row: dict[str, str], result: OCSRResult, latency_ms: float) -> dict[str, Any]:
    truth = _identity(row["ground_truth_isomeric_smiles"])
    predicted = _identity(result.smiles)
    output: dict[str, Any] = dict(row)
    truth_key = str(truth.get("inchikey") or "")
    predicted_key = str(predicted.get("inchikey") or "")
    connectivity_exact = bool(truth_key and predicted_key and truth_key.split("-", 1)[0] == predicted_key.split("-", 1)[0])
    failure_category = result.failure_category or (
        classify_backend_failure(result) if result.status != "success" else ""
    )
    backend_execution_success = bool(result.status == "success" or failure_category == "output_parse_failure")
    output.update({
        "backend": result.backend, "predicted_smiles": result.smiles or "", "confidence": result.confidence,
        "backend_status": result.status,
        "backend_execution_success": backend_execution_success,
        "backend_success": result.status == "success" and bool(result.smiles), "valid_smiles": bool(predicted.get("valid")),
        "predicted_canonical_smiles": predicted.get("canonical", ""),
        "predicted_isomeric_smiles": predicted.get("isomeric", ""),
        "predicted_inchikey": predicted.get("inchikey", ""), "predicted_formula": predicted.get("formula", ""),
        "canonical_exact_match": bool(predicted.get("canonical") == truth.get("canonical")),
        "isomeric_exact_match": bool(predicted.get("isomeric") == truth.get("isomeric")),
        "inchikey_exact_match": bool(predicted.get("inchikey") == truth.get("inchikey")),
        "full_inchikey_exact": bool(predicted.get("inchikey") == truth.get("inchikey")),
        "connectivity_exact": connectivity_exact,
        "molecular_formula_match": bool(predicted.get("formula") == truth.get("formula")),
        "connectivity_match": connectivity_exact,
        "nonisomeric_canonical_exact": bool(predicted.get("canonical") == truth.get("canonical")),
        "stereochemistry_exact_match": bool(
            predicted.get("canonical") == truth.get("canonical") and predicted.get("isomeric") == truth.get("isomeric")
        ),
        "latency_ms": round(float(result.inference_time_ms if result.inference_time_ms is not None else latency_ms), 3),
        "decision": result.decision or ("accepted" if result.smiles else "rejected"),
        "message": result.message, "model_name": result.model_name, "model_version": result.model_version,
        "model_sha256": result.model_sha256, "device": result.device, "package_version": result.package_version,
        "raw_output": result.raw_output or "", "failure_category": failure_category,
        "exception_type": result.exception_type or "",
        "exception_summary": sanitize_exception_summary(result.exception_summary or ""),
        "attempt_count": result.attempt_count,
        "first_attempt_json": json.dumps(result.first_attempt or {}, ensure_ascii=False, sort_keys=True),
        "retry_attempt_json": json.dumps(result.retry_attempt or {}, ensure_ascii=False, sort_keys=True),
    })
    charge_or_protonation_different = bool(
        connectivity_exact and not output["full_inchikey_exact"] and (
            predicted.get("formal_charge") != truth.get("formal_charge")
            or predicted.get("formula") != truth.get("formula")
        )
    )
    output["connectivity_correct_charge_or_protonation_different"] = charge_or_protonation_different
    output["connectivity_correct_stereochemistry_wrong"] = bool(
        connectivity_exact
        and not charge_or_protonation_different
        and predicted.get("canonical") == truth.get("canonical")
        and not output["isomeric_exact_match"]
    )
    output["error_type"] = "" if output["inchikey_exact_match"] else classify_structural_error(truth, predicted, result)
    if result.backend == "ensemble":
        output.update(_ensemble_flags(result, truth))
        if output.get("model_disagreement") and not output["inchikey_exact_match"]:
            output["error_type"] = "ensemble_abstention" if output.get("ensemble_abstention") else "model_disagreement"
    return output


def _perturbation_group(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("image_variant") != "synthetic_perturbation":
        return "none", "none"
    kind = str(row.get("perturbation") or "unspecified")
    try:
        params = json.loads(str(row.get("perturbation_parameters") or "{}"))
    except json.JSONDecodeError:
        params = {}
    severity = str(params.get("severity") or "composite_unspecified")
    return kind, severity


def summarize_predictions(rows: list[dict[str, Any]], peak_gpu_memory_mib: float | None = None) -> dict[str, Any]:
    total = len(rows)
    latencies = [float(row["latency_ms"]) for row in rows]
    metric_fields = (
        "backend_execution_success", "backend_success", "valid_smiles", "canonical_exact_match", "isomeric_exact_match",
        "inchikey_exact_match", "full_inchikey_exact", "connectivity_exact", "molecular_formula_match",
        "connectivity_match", "nonisomeric_canonical_exact", "stereochemistry_exact_match",
        "connectivity_correct_stereochemistry_wrong", "connectivity_correct_charge_or_protonation_different",
    )
    result: dict[str, Any] = {"sample_count": total}
    for field in metric_fields:
        count = sum(bool(row.get(field)) for row in rows)
        result[f"{field}_count"] = count
        result[f"{field}_rate"] = _rate(count, total)
    result["false_failure_count"] = total - result["backend_execution_success_count"]
    result["false_failure_rate"] = _rate(result["false_failure_count"], total)
    result.update({
        "mean_latency_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "median_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 95), "peak_gpu_memory_mib": peak_gpu_memory_mib,
        "error_distribution": dict(Counter(row["error_type"] for row in rows if row.get("error_type"))),
    })
    accepted = [row for row in rows if row.get("decision") in {"accepted", "accepted_with_warning"}]
    false_acceptances = [row for row in accepted if not bool(row.get("full_inchikey_exact"))]
    result["accepted_prediction_count"] = len(accepted)
    result["accepted_prediction_rate"] = _rate(len(accepted), total)
    result["false_acceptance_count"] = len(false_acceptances)
    result["false_acceptance_rate"] = _rate(len(false_acceptances), total)
    result["abstention_count"] = total - len(accepted)
    result["abstention_rate"] = _rate(result["abstention_count"], total)
    successful = [row for row in rows if bool(row.get("backend_success"))]
    result["conditional_sample_count"] = len(successful)
    for field in (
        "valid_smiles", "canonical_exact_match", "isomeric_exact_match", "full_inchikey_exact",
        "connectivity_exact", "molecular_formula_match",
    ):
        count = sum(bool(row.get(field)) for row in successful)
        result[f"conditional_{field}_count"] = count
        result[f"conditional_{field}_rate"] = _rate(count, len(successful))
    execution_successful = [row for row in rows if bool(row.get("backend_execution_success"))]
    result["execution_conditional_sample_count"] = len(execution_successful)
    for field in ("valid_smiles", "full_inchikey_exact", "connectivity_exact"):
        count = sum(bool(row.get(field)) for row in execution_successful)
        result[f"execution_conditional_{field}_count"] = count
        result[f"execution_conditional_{field}_rate"] = _rate(count, len(execution_successful))
    if any(row.get("backend") == "ensemble" for row in rows):
        for field in (
            "both_models_correct", "only_molscribe_correct", "only_decimer_correct", "both_wrong_but_agree",
            "model_disagreement", "ensemble_correct_accept", "ensemble_wrong_accept", "ensemble_correct_reject",
            "ensemble_unnecessary_reject", "ensemble_abstention",
        ):
            count = sum(bool(row.get(field)) for row in rows)
            result[f"{field}_count"] = count
            result[f"{field}_rate"] = _rate(count, total)
    return result


def _group_metrics(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values = str(row.get(key) or "unspecified").split(";") if key == "structure_features" else [str(row.get(key) or "unspecified")]
        for value in values:
            grouped[value].append(row)
    output = []
    for value, members in sorted(grouped.items()):
        metrics = summarize_predictions(members)
        output.append({key: value, **{field: metrics[field] for field in (
            "sample_count", "backend_execution_success_rate", "backend_success_rate", "valid_smiles_rate", "canonical_exact_match_rate",
            "inchikey_exact_match_rate", "connectivity_match_rate", "mean_latency_ms",
        )}})
    return output


def _per_perturbation_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_perturbation_group(row)].append(row)
    output: list[dict[str, Any]] = []
    for (kind, severity), members in sorted(grouped.items()):
        metrics = summarize_predictions(members)
        output.append({
            "perturbation_type": kind,
            "severity": severity,
            "sample_count": metrics["sample_count"],
            "backend_execution_success_rate": metrics["backend_execution_success_rate"],
            "valid_smiles_rate": metrics["valid_smiles_rate"],
            "connectivity_exact_rate": metrics["connectivity_exact_rate"],
            "full_inchikey_exact_rate": metrics["full_inchikey_exact_rate"],
        })
    return output


def _cid_consistency(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("pubchem_cid"))].append(row)
    output: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for cid, members in sorted(grouped.items(), key=lambda item: int(item[0])):
        correct = {str(row.get("image_variant")): bool(row.get("full_inchikey_exact")) for row in members}
        official = correct.get("official_clean", False)
        rendered = correct.get("rendered_clean", False)
        perturbed = correct.get("synthetic_perturbation", False)
        if official and rendered and perturbed:
            category = "all_three_correct"
        elif rendered and not official and not perturbed:
            category = "rendered_only_correct"
        elif (official or rendered) and not perturbed:
            category = "clean_correct_perturbed_wrong"
        elif not any((official, rendered, perturbed)):
            category = "all_three_wrong"
        else:
            category = "mixed_other"
        counts[category] += 1
        output.append({"pubchem_cid": cid, **correct, "consistency_category": category})
    return output, dict(sorted(counts.items()))


def _complexity_group(row: dict[str, Any]) -> str:
    heavy = int(float(row.get("heavy_atom_count") or 0))
    rings = int(float(row.get("ring_count") or 0))
    if heavy <= 12 and rings <= 1: return "small_low_complexity"
    if heavy >= 35 or rings >= 5: return "large_high_complexity"
    return "medium_complexity"


def _gpu_runtime_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {"nvidia_smi": None, "torch_cuda": None}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        metadata["nvidia_smi"] = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    try:
        import torch
        metadata["torch_cuda"] = getattr(torch.version, "cuda", None)
    except Exception:
        pass
    return metadata


def _reset_framework_gpu_peak(backend: str) -> None:
    try:
        if backend == "molscribe":
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        # Do not import or touch TensorFlow here. DECIMER must configure the
        # physical device before the first TensorFlow GPU operation. Each
        # benchmark backend runs in a fresh process, so its allocator peak is
        # already scoped to this run.
    except Exception:
        pass


def _framework_gpu_peak_mib(backend: str) -> float | None:
    try:
        if backend == "molscribe":
            import torch
            if torch.cuda.is_available():
                return round(float(torch.cuda.max_memory_allocated()) / (1024 * 1024), 3)
        elif backend == "decimer":
            import tensorflow as tf
            return round(float(tf.config.experimental.get_memory_info("GPU:0")["peak"]) / (1024 * 1024), 3)
    except Exception:
        pass
    return None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) if rows else ["sample_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _benchmark_predictor(
    backend: str,
    reliability_config: BackendReliabilityConfig = DEFAULT_BACKEND_RELIABILITY_CONFIG,
) -> Callable[[Any], OCSRResult]:
    """Reuse one loaded model inside the dedicated benchmark process.

    Interactive production isolation remains unchanged. A classified
    retriable failure is still retried once through the isolated worker in
    ``run_with_single_retry``.
    """
    if backend == "molscribe":
        adapter = MolScribeAdapter(
            isolated_subprocess=False,
            timeout_seconds=reliability_config.molscribe_timeout_seconds,
        )
        return adapter.recognize
    if backend == "decimer":
        adapter = DECIMERAdapter(
            isolated_subprocess=False,
            timeout_seconds=reliability_config.decimer_timeout_seconds,
        )
        return adapter.recognize
    recognizer = MoleculeRecognizer(backend)
    return recognizer.recognize


def evaluate_trusted_manifest(
    manifest: Path,
    backend: str,
    output: Path,
    predictor: Callable[[Path], OCSRResult] | None = None,
    limit: int | None = None,
    measure_gpu: bool = True,
    peak_gpu_memory_mib: float | None = None,
    splits: tuple[str, ...] = ("test",),
    purpose: str = "formal_baseline",
    allow_frozen_test: bool = True,
    preprocessing_profile: InputNormalizationConfig | str = "raw",
    retry_failures: bool = False,
    reliability_config: BackendReliabilityConfig = DEFAULT_BACKEND_RELIABILITY_CONFIG,
) -> dict[str, Any]:
    root = manifest.resolve().parent
    formal_lock = output / "formal_run.lock"
    if purpose == "external_holdout" and formal_lock.exists():
        raise FileExistsError(f"External holdout formal output is already frozen: {formal_lock}")
    validation = validate_trusted_dataset(root)
    if not validation["valid"]:
        raise ValueError("Trusted dataset validation failed: " + "; ".join(validation["errors"][:20]))
    all_rows = list(csv.DictReader(manifest.open("r", encoding="utf-8-sig", newline="")))
    selected_splits = tuple(dict.fromkeys(str(split).strip() for split in splits if str(split).strip()))
    if not selected_splits:
        raise ValueError("At least one split is required.")
    if purpose in {"profile_selection", "tuning", "router_selection"} and "test" in selected_splits:
        raise ValueError("Frozen test split cannot participate in profile or router selection.")
    if "test" in selected_splits and not allow_frozen_test:
        raise ValueError("Reading frozen test requires explicit allow_frozen_test=True.")
    rows = [row for row in all_rows if row.get("split") in selected_splits]
    if limit is not None: rows = rows[:limit]
    if any(row.get("ground_truth_origin") != "pubchem" or row.get("review_status") != "source_verified" for row in rows):
        raise ValueError("Predictions or unverified labels cannot be used as trusted ground truth.")
    infer = predictor or _benchmark_predictor(backend, reliability_config)
    profile_config = get_profile(preprocessing_profile) if isinstance(preprocessing_profile, str) else preprocessing_profile
    if purpose == "external_holdout":
        protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
        if protocol.get("dataset_role") != "external_holdout" or protocol.get("model_results_viewed_before_freeze") is not False:
            raise ValueError("External holdout protocol is missing the pre-evaluation freeze declaration.")
        if profile_config.profile != "raw":
            frozen = (protocol.get("frozen_profiles") or {}).get(backend) or {}
            if frozen.get("profile") != profile_config.profile or frozen.get("profile_sha256") != profile_config.sha256():
                raise ValueError(
                    f"Profile {profile_config.profile} is not the train/dev-frozen profile for {backend}."
                )
    predictions: list[dict[str, Any]] = []
    _reset_framework_gpu_peak(backend)
    with tempfile.TemporaryDirectory(prefix=f"ocsr_{backend}_{profile_config.profile}_") as temp_directory, GPUMemoryMonitor(enabled=measure_gpu) as monitor:
        normalized_root = Path(temp_directory)
        for row in rows:
            path = root / row["image_path"]
            started = time.perf_counter()
            try:
                if profile_config.profile == "raw":
                    target: Any = path
                else:
                    normalized = normalize_ocsr_input(path, profile_config)
                    normalized_path = normalized_root / f"{row['sample_id']}.png"
                    Image.fromarray(normalized).save(normalized_path, "PNG")
                    target = normalized_path
                result = (
                    run_with_single_retry(backend, target, infer, config=reliability_config)
                    if retry_failures and predictor is None else infer(target)
                )
            except Exception as exc:
                category = classify_backend_failure(exception=exc)
                result = OCSRResult(
                    None, None, backend, "failed", f"evaluation_error:{sanitize_exception_summary(exc)}",
                    failure_category=category,
                    exception_type=type(exc).__name__,
                    exception_summary=sanitize_exception_summary(exc),
                )
            evaluated = evaluate_prediction(row, result, (time.perf_counter() - started) * 1000)
            evaluated["preprocessing_profile"] = profile_config.profile
            evaluated["preprocessing_version"] = profile_config.version
            evaluated["preprocessing_config_sha256"] = profile_config.sha256()
            predictions.append(evaluated)
    for row in predictions: row["complexity_group"] = _complexity_group(row)
    framework_peak = _framework_gpu_peak_mib(backend)
    measured_peaks = [
        value for value in (peak_gpu_memory_mib, monitor.peak_mib, framework_peak)
        if value is not None
    ]
    metrics = summarize_predictions(predictions, max(measured_peaks) if measured_peaks else None)
    consistency_rows, consistency_counts = _cid_consistency(predictions)
    metrics["cid_variant_consistency"] = consistency_counts
    metadata = {
        "backend": backend, "splits": list(selected_splits), "sample_count": len(predictions),
        "purpose": purpose, "test_is_evaluation_only": "test" in selected_splits,
        "test_used_for_tuning": False, "git_sha": git_commit(),
        "dataset_checksums_sha256": validation["checksums_sha256"], "rdkit_version": rdBase.rdkitVersion,
        "dependency_versions": dependency_versions(),
        "preprocessing_profile": asdict(profile_config),
        "preprocessing_config_sha256": profile_config.sha256(),
        "retry_failures": retry_failures,
        "reliability_config": asdict(reliability_config),
        "gpu_runtime": _gpu_runtime_metadata(),
        "framework_peak_gpu_memory_mib": framework_peak,
        "model_artifacts": [json.loads(item) for item in sorted({
            json.dumps({
                "model_name": row.get("model_name"), "model_version": row.get("model_version"),
                "model_sha256": row.get("model_sha256"), "package_version": row.get("package_version"),
                "device": row.get("device"),
            }, sort_keys=True)
            for row in predictions
        })],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "metrics.json").write_text(json.dumps({"metrics": metrics, "run_metadata": metadata}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output / "predictions.csv", predictions)
    _write_csv(output / "errors.csv", [row for row in predictions if row.get("error_type")])
    _write_csv(output / "per_variant_metrics.csv", _group_metrics(predictions, "image_variant"))
    _write_csv(output / "per_feature_metrics.csv", _group_metrics(predictions, "structure_features"))
    _write_csv(output / "per_complexity_metrics.csv", _group_metrics(predictions, "complexity_group"))
    _write_csv(output / "per_perturbation_metrics.csv", _per_perturbation_metrics(predictions))
    _write_csv(output / "per_cid_consistency.csv", consistency_rows)
    _write_csv(output / "latency.csv", [{"sample_id": row["sample_id"], "latency_ms": row["latency_ms"]} for row in predictions])
    report = [
        f"# Trusted OCSR evaluation: {backend}", "", f"Evaluated images: {len(predictions)}",
        f"Splits: {', '.join(selected_splits)}", "",
        f"- Backend execution success rate: {metrics['backend_execution_success_rate']:.4f}",
        f"- Parseable backend output rate: {metrics['backend_success_rate']:.4f}",
        f"- Valid SMILES rate: {metrics['valid_smiles_rate']:.4f}",
        f"- Canonical exact match: {metrics['canonical_exact_match_rate']:.4f}",
        f"- InChIKey exact match: {metrics['inchikey_exact_match_rate']:.4f}",
        f"- Connectivity exact (InChIKey first block): {metrics['connectivity_exact_rate']:.4f}",
        f"- Conditional full InChIKey exact among backend successes: {metrics['conditional_full_inchikey_exact_rate']:.4f}",
        f"- Formula match: {metrics['molecular_formula_match_rate']:.4f}",
        f"- Mean / median / p95 latency (ms): {metrics['mean_latency_ms']} / {metrics['median_latency_ms']} / {metrics['p95_latency_ms']}",
        f"- Peak GPU memory (MiB, system poll): {metrics['peak_gpu_memory_mib']}", "",
        "## Interpretation limits", "",
        "Ground truth comes from the matching PubChem CID; predictions never supply labels.",
        "PubChem official and RDKit-rendered images are trustworthy structure depictions, but are not real paper crops.",
        "Synthetic perturbations are deterministic stress tests, not a substitute for real scan noise.",
        "These results do not directly estimate accuracy on PMC paper figures; a real-image set with trusted mappings is still required.",
        "Any test/external-holdout split is evaluation-only and was not used to tune model, threshold, preprocessing, or ensemble rules.",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if purpose == "external_holdout":
        formal_lock.write_text(json.dumps({
            "completed": True, "git_sha": metadata["git_sha"], "dataset_checksums_sha256": metadata["dataset_checksums_sha256"],
            "backend": backend, "preprocessing_config_sha256": metadata["preprocessing_config_sha256"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"metrics": metrics, "metadata": metadata, "predictions": predictions}


def ensemble_predictor_from_files(
    dataset_root: Path,
    molscribe_predictions: Path,
    decimer_predictions: Path,
) -> Callable[[Path], OCSRResult]:
    """Replay the frozen ensemble rule over already-generated backend outputs."""
    def load(path: Path) -> dict[str, dict[str, str]]:
        rows = list(csv.DictReader(path.open("r", encoding="utf-8-sig", newline="")))
        return {row["image_path"].replace("\\", "/"): row for row in rows}

    tables = {"molscribe": load(molscribe_predictions), "decimer": load(decimer_predictions)}

    def predictor(image_path: Path) -> OCSRResult:
        relative = image_path.resolve().relative_to(dataset_root.resolve()).as_posix()
        raw_results: list[OCSRResult] = []
        latencies: list[float] = []
        for backend in ("molscribe", "decimer"):
            row = tables[backend].get(relative)
            if row is None:
                raise KeyError(f"Missing {backend} prediction for {relative}")
            latency = float(row.get("latency_ms") or 0.0)
            latencies.append(latency)
            raw_results.append(OCSRResult(
                smiles=row.get("predicted_smiles") or None,
                confidence=float(row["confidence"]) if row.get("confidence") else None,
                backend=backend,
                status="success" if row.get("backend_status") == "success" else "failed",
                message=row.get("message") or "replayed trusted benchmark prediction",
                inference_time_ms=latency,
                model_name=row.get("model_name") or None,
                model_version=row.get("model_version") or None,
                model_sha256=row.get("model_sha256") or None,
                device=row.get("device") or None,
                package_version=row.get("package_version") or None,
                result_origin="replayed_real_model_prediction",
                raw_output=row.get("raw_output") or None,
                failure_category=row.get("failure_category") or None,
                exception_type=row.get("exception_type") or None,
                exception_summary=row.get("exception_summary") or None,
            ))
        return combine_ensemble_results(
            raw_results,
            enabled_backends=["molscribe", "decimer"],
            elapsed_ms=max(latencies),
        )
    return predictor


def backend_predictor_from_file(
    dataset_root: Path,
    predictions: Path,
    backend: str,
) -> Callable[[Path], OCSRResult]:
    """Replay raw backend outputs to recompute metrics without re-running a model."""
    rows = list(csv.DictReader(predictions.open("r", encoding="utf-8-sig", newline="")))
    table = {row["image_path"].replace("\\", "/"): row for row in rows}

    def predictor(image_path: Path) -> OCSRResult:
        relative = image_path.resolve().relative_to(dataset_root.resolve()).as_posix()
        row = table.get(relative)
        if row is None:
            raise KeyError(f"Missing {backend} prediction for {relative}")
        return OCSRResult(
            smiles=row.get("predicted_smiles") or None,
            confidence=float(row["confidence"]) if row.get("confidence") else None,
            backend=backend,
            status="success" if row.get("backend_status") == "success" else "failed",
            message=row.get("message") or "replayed trusted benchmark prediction",
            inference_time_ms=float(row.get("latency_ms") or 0.0),
            model_name=row.get("model_name") or None, model_version=row.get("model_version") or None,
            model_sha256=row.get("model_sha256") or None, device=row.get("device") or None,
            package_version=row.get("package_version") or None, result_origin="replayed_real_model_prediction",
            raw_output=row.get("raw_output") or None,
            failure_category=row.get("failure_category") or None,
            exception_type=row.get("exception_type") or None,
            exception_summary=row.get("exception_summary") or None,
        )
    return predictor


def compare_trusted_runs(evaluation_root: Path, output: Path) -> dict[str, Any]:
    backends = ("molscribe", "decimer", "ensemble")
    metrics = {}
    rows_by_backend: dict[str, dict[str, dict[str, str]]] = {}
    for backend in backends:
        payload = json.loads((evaluation_root / backend / "metrics.json").read_text(encoding="utf-8"))
        metrics[backend] = payload["metrics"]
        rows = list(csv.DictReader((evaluation_root / backend / "predictions.csv").open("r", encoding="utf-8-sig", newline="")))
        rows_by_backend[backend] = {row["sample_id"]: row for row in rows}
    overlap_rows: list[dict[str, Any]] = []
    for sample_id in sorted(set.intersection(*(set(rows) for rows in rows_by_backend.values()))):
        mol = rows_by_backend["molscribe"][sample_id]
        dec = rows_by_backend["decimer"][sample_id]
        ens = rows_by_backend["ensemble"][sample_id]
        overlap_rows.append({
            "sample_id": sample_id, "image_variant": mol.get("image_variant"),
            "molscribe_correct": mol.get("inchikey_exact_match") == "True",
            "decimer_correct": dec.get("inchikey_exact_match") == "True",
            "ensemble_correct": ens.get("inchikey_exact_match") == "True",
            "model_disagreement": ens.get("model_disagreement") == "True",
            "ensemble_abstention": ens.get("ensemble_abstention") == "True",
        })
    errors = []
    for backend in backends:
        for error, count in metrics[backend].get("error_distribution", {}).items():
            errors.append({"backend": backend, "error_type": error, "count": count})
    comparison = {
        "backends": {backend: {key: metrics[backend].get(key) for key in (
            "sample_count", "backend_execution_success_rate", "backend_success_rate", "valid_smiles_rate", "canonical_exact_match_rate",
            "isomeric_exact_match_rate", "inchikey_exact_match_rate", "molecular_formula_match_rate",
            "connectivity_match_rate", "mean_latency_ms", "median_latency_ms", "p95_latency_ms", "peak_gpu_memory_mib",
        )} for backend in backends},
        "test_used_for_tuning": False,
        "ensemble_rule_change_recommended": False,
        "note": "No ensemble rule is changed from test results; changes require development data and a new untouched test set.",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output / "backend_overlap.csv", overlap_rows)
    _write_csv(output / "error_distribution.csv", errors)
    lines = ["# Trusted OCSR backend comparison", "", "Formal metrics use only the frozen test split.", ""]
    for backend in backends:
        item = comparison["backends"][backend]
        lines.append(f"- {backend}: InChIKey={item['inchikey_exact_match_rate']}, canonical={item['canonical_exact_match_rate']}, valid={item['valid_smiles_rate']}, mean latency={item['mean_latency_ms']} ms")
    lines += ["", "The test split was not used to tune ensemble weights or acceptance rules.", "Clean/synthetic images do not establish real-PMC accuracy."]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return comparison
