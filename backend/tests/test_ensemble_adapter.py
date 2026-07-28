"""Tests for multi-backend OCSR ensemble ranking."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from PIL import Image

from src.analysis.correction import apply_ensemble_candidate_result, apply_smiles_correction
from src.evaluation.dataset import load_manifest
from src.evaluation.evaluator import OCSREvaluator
from src.ocsr.base import BaseOCSRAdapter, OCSRResult
from src.ocsr.ensemble import EnsembleOCSRAdapter, candidate_from_result, rank_candidates
from src.ocsr.recognizer import MoleculeRecognizer


class StaticAdapter(BaseOCSRAdapter):
    preferred_image_stage = "original"

    def __init__(self, backend: str, smiles: str | None, status: str = "success") -> None:
        self.backend_name = backend
        self.smiles = smiles
        self._status = status

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        return OCSRResult(
            smiles=self.smiles,
            confidence=0.99,
            backend=self.backend_name,
            status=self._status,  # type: ignore[arg-type]
            message="ok" if self._status == "success" else "failed",
            inference_time_ms=3.0,
            model_name=f"{self.backend_name}-model",
            device="cuda",
        )


def _adapter_factory(backend: str, smiles: str | None, status: str = "success"):
    return lambda: StaticAdapter(backend, smiles, status)


def _ensemble(factories: dict[str, Any], **kwargs: Any) -> EnsembleOCSRAdapter:
    return EnsembleOCSRAdapter(
        backends=list(factories),
        backend_priority=list(factories),
        adapter_factories=factories,
        total_timeout_seconds=kwargs.pop("total_timeout_seconds", 10),
        **kwargs,
    )


def test_ensemble_agreement_for_same_canonical_smiles() -> None:
    adapter = _ensemble({
        "molscribe": _adapter_factory("molscribe", "CCO"),
        "decimer": _adapter_factory("decimer", "OCC"),
    })
    result = adapter.recognize("image.png")
    assert result.status == "success"
    assert result.smiles == "CCO"
    assert result.consensus["status"] == "agreement"
    assert result.consensus["decision"] == "accepted_with_warning"
    assert result.consensus["recommended_backend"] == "consensus"
    assert result.consensus["manual_review_recommended"] is True
    assert "agreement_not_ground_truth" in result.consensus["reason_codes"]
    assert {candidate["backend"] for candidate in result.candidates} == {"molscribe", "decimer"}
    assert result.similarity_analysis[0]["canonical_smiles_equal"] is True


def test_ensemble_prefers_only_valid_candidate() -> None:
    adapter = _ensemble({
        "molscribe": _adapter_factory("molscribe", "not-a-smiles"),
        "decimer": _adapter_factory("decimer", "CCO"),
    })
    result = adapter.recognize("image.png")
    assert result.status == "success"
    assert result.consensus["status"] == "single_valid"
    assert result.consensus["decision"] == "accepted_with_warning"
    assert result.consensus["manual_review_recommended"] is True
    assert result.consensus["decision_note"] == "accepted_with_single_backend"
    assert result.consensus["recommended_backend"] == "decimer"
    assert result.smiles == "CCO"


def test_ensemble_disagreement_uses_priority_not_cross_model_confidence() -> None:
    adapter = EnsembleOCSRAdapter(
        backends=["molscribe", "decimer"],
        backend_priority=["decimer", "molscribe"],
        adapter_factories={
            "molscribe": _adapter_factory("molscribe", "CCO"),
            "decimer": _adapter_factory("decimer", "c1ccccc1"),
        },
    )
    result = adapter.recognize("image.png")
    assert result.status == "failed"
    assert result.smiles is None
    assert result.consensus["status"] == "disagreement"
    assert result.consensus["decision"] == "review_needed"
    assert result.consensus["recommended_backend"] is None
    assert result.consensus["review_candidates"][0]["backend"] == "decimer"
    assert "未比较" in result.consensus["confidence_policy"]
    assert result.similarity_analysis[0]["canonical_smiles_equal"] is False


def test_ensemble_all_failed_and_serialization() -> None:
    adapter = _ensemble({
        "molscribe": _adapter_factory("molscribe", None, "failed"),
        "decimer": _adapter_factory("decimer", None, "failed"),
    })
    result = adapter.recognize("image.png")
    payload = result.to_dict()
    assert result.status == "failed"
    assert payload["candidates"][0]["raw_smiles"] is None
    assert payload["consensus"]["status"] == "all_failed"
    assert payload["consensus"]["decision"] == "rejected"


def test_serial_ensemble_releases_child_adapters_by_default() -> None:
    adapter = _ensemble({
        "molscribe": _adapter_factory("molscribe", "CCO"),
        "decimer": _adapter_factory("decimer", "OCC"),
    })

    result = adapter.recognize("image.png")

    assert result.status == "success"
    assert adapter.adapters == {}


def test_ensemble_parallel_timeout_records_backend_failure() -> None:
    class SlowAdapter(BaseOCSRAdapter):
        backend_name = "slow"

        def recognize(self, image_path_or_array: Any) -> OCSRResult:
            time.sleep(0.1)
            return OCSRResult("CCO", None, self.backend_name, "success", "late")

    adapter = EnsembleOCSRAdapter(
        backends=["slow"],
        adapter_factories={"slow": SlowAdapter},
        parallel=True,
        total_timeout_seconds=0.001,
    )
    result = adapter.recognize("image.png")
    assert result.status == "failed"
    assert result.candidates[0]["status"] == "failed"
    assert "超时" in result.candidates[0]["message"]


def test_rank_candidates_marks_disagreement_for_review() -> None:
    low = candidate_from_result(OCSRResult("CCO", 0.99, "low", "success", "ok"))
    high = candidate_from_result(OCSRResult("c1ccccc1", 0.1, "high", "success", "ok"))
    consensus = rank_candidates([low, high], backend_priority=["low", "high"], reliability_weights={"high": 2.0})
    assert consensus["status"] == "disagreement"
    assert consensus["decision"] == "review_needed"
    assert consensus["recommended_backend"] is None


def test_human_correction_overrides_ensemble_recommendation(tmp_path: Path) -> None:
    result = _ensemble({
        "molscribe": _adapter_factory("molscribe", "CCO"),
        "decimer": _adapter_factory("decimer", "OCC"),
    }).recognize("image.png")
    report = {
        "analysis_id": "abc123",
        "status": "success",
        "input": {"type": "image", "filename": "mol.png"},
        "ocsr": result.to_dict(),
        "correction": {"applied": False},
        "final": {"smiles": "CCO", "canonical_smiles": "CCO", "source": "ensemble_recommendation"},
        "images": {},
    }
    corrected = apply_smiles_correction(report, "c1ccccc1", tmp_path)
    assert corrected["final"]["source"] == "user_correction"
    assert corrected["final"]["canonical_smiles"] == "c1ccccc1"
    assert corrected["ocsr"]["candidates"][0]["backend"] == "molscribe"


def test_user_can_apply_ensemble_candidate_as_final_result(tmp_path: Path) -> None:
    result = EnsembleOCSRAdapter(
        backends=["molscribe", "decimer"],
        backend_priority=["molscribe", "decimer"],
        adapter_factories={
            "molscribe": _adapter_factory("molscribe", "CCO"),
            "decimer": _adapter_factory("decimer", "c1ccccc1"),
        },
    ).recognize("image.png")
    report = {
        "analysis_id": "abc123",
        "status": "failed",
        "input": {"type": "image", "filename": "mol.png"},
        "ocsr": result.to_dict(),
        "correction": {"applied": False},
        "final": {"smiles": None, "source": None},
        "images": {},
        "image_quality": {"quality_score": 0.9, "passed": True, "reason_codes": []},
    }

    selected = apply_ensemble_candidate_result(report, "decimer", tmp_path)

    assert selected["status"] == "success"
    assert selected["final"]["source"] == "user_selected_decimer_candidate"
    assert selected["final"]["canonical_smiles"] == "c1ccccc1"
    assert selected["candidate_selection"]["backend"] == "decimer"
    assert selected["recognition_decision"]["decision"] == "accepted"
    assert selected["ocsr"]["decision"] == "accepted"
    assert selected["ocsr"]["selected_candidate_backend"] == "decimer"
    assert selected["audit"][-1]["operation"] == "apply_ensemble_candidate"
    assert Path(selected["images"]["predicted_molecule"]).is_file()


def _image(path: Path) -> Path:
    Image.new("RGB", (24, 24), "white").save(path)
    return path


def _manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    fieldnames = ["sample_id", "image_path", "ground_truth_smiles", "category", "source", "notes"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class FakeBenchmarkEnsemble(BaseOCSRAdapter):
    backend_name = "ensemble"
    preferred_image_stage = "original"

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        candidates = [
            candidate_from_result(OCSRResult("CCO", None, "molscribe", "success", "ok", inference_time_ms=1.0)),
            candidate_from_result(OCSRResult("c1ccccc1", None, "decimer", "success", "ok", inference_time_ms=2.0)),
        ]
        return OCSRResult(
            smiles=None,
            confidence=None,
            backend="ensemble",
            status="failed",
            message="needs review",
            inference_time_ms=3.0,
            candidates=candidates,
            consensus={
                "status": "disagreement",
                "decision": "review_needed",
                "recommended_smiles": None,
                "recommended_backend": None,
                "reason": "needs review",
            },
            similarity_analysis=[],
        )


def test_benchmark_integrates_ensemble_candidate_metrics(monkeypatch, tmp_path: Path) -> None:
    _image(tmp_path / "ethanol.png")
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [{
            "sample_id": "ethanol",
            "image_path": "ethanol.png",
            "ground_truth_smiles": "CCO",
            "category": "clean",
            "source": "unit",
            "notes": "",
        }],
    )
    monkeypatch.setitem(MoleculeRecognizer.ADAPTERS, "ensemble", FakeBenchmarkEnsemble)
    sample = load_manifest(manifest, tmp_path)[0]
    result = OCSREvaluator("ensemble").run([sample])
    row = result["rows"][0]
    assert row["consensus_status"] == "disagreement"
    assert row["consensus_decision"] == "review_needed"
    assert row["ensemble_review_needed"] is True
    assert row["candidate_molscribe_canonical_exact_match"] is True
    assert row["candidate_decimer_canonical_exact_match"] is False
    assert result["metrics"]["ensemble"]["disagreement_count"] == 1
    assert result["metrics"]["ensemble"]["review_needed_count"] == 1
    assert result["metrics"]["ensemble"]["candidate_backend_metrics"]["molscribe"]["canonical_exact_match_count"] == 1
