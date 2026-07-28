"""Single-image candidate confirmation, correction, and export tests."""

from __future__ import annotations

import copy
from pathlib import Path

from src.analysis.correction import (
    apply_smiles_correction,
    confirm_structure,
    human_review_state,
    is_structure_confirmed,
    mark_structure_unable_to_confirm,
    revoke_structure_confirmation,
)
from src.analysis.molecule_report import MoleculeReportGenerator
from src.chem.lipinski import evaluate_lipinski
from src.export.pdf_exporter import _clean, save_pdf
from src.ui.report_view import _compact_technical, _format_local_time, _report_section_options


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _image_report(tmp_path: Path) -> dict:
    return MoleculeReportGenerator("demo", tmp_path).generate(
        image_path=PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    )


def test_image_result_starts_as_candidate_and_requires_explicit_confirmation(tmp_path: Path) -> None:
    report = _image_report(tmp_path)

    assert human_review_state(report)["status"] == "unconfirmed"
    assert is_structure_confirmed(report) is False

    confirmed = confirm_structure(report)
    assert is_structure_confirmed(confirmed) is True
    assert confirmed["human_review"]["reviewed_smiles"] == report["final"]["smiles"]
    assert confirmed["final"]["source"] == "human_confirmed_model_candidate"
    assert confirmed["correction"]["applied"] is False
    assert confirmed["review_events"][-1]["action"] == "confirm_structure"
    assert human_review_state(report)["status"] == "unconfirmed"

    revoked = revoke_structure_confirmation(confirmed)
    assert revoked["human_review"]["status"] == "unconfirmed"
    assert revoked["final"]["source"] == report["final"]["source"]
    assert revoked["correction"]["applied"] is False
    assert revoked["review_events"][-1]["action"] == "revoke_confirmation"


def test_legacy_image_report_without_review_block_remains_compatible(tmp_path: Path) -> None:
    legacy = copy.deepcopy(_image_report(tmp_path))
    legacy.pop("human_review", None)
    legacy.pop("review_events", None)

    assert human_review_state(legacy)["status"] == "unconfirmed"
    confirmed = confirm_structure(legacy)
    assert confirmed["human_review"]["status"] == "confirmed"
    assert confirmed["final"]["source"] == "human_confirmed_model_candidate"


def test_smiles_change_preserves_prediction_and_revokes_confirmation(tmp_path: Path) -> None:
    report = _image_report(tmp_path)
    predicted = report["ocsr"]["predicted_smiles"]
    confirmed = confirm_structure(report)

    corrected = apply_smiles_correction(confirmed, "OCC", tmp_path)

    assert corrected["validation"]["canonical_smiles"] == "CCO"
    assert corrected["images"]["corrected_molecule"]
    assert corrected["ocsr"]["predicted_smiles"] == predicted
    assert corrected["human_review"]["status"] == "unconfirmed"
    assert is_structure_confirmed(corrected) is False

    reconfirmed = confirm_structure(corrected)
    assert reconfirmed["final"]["source"] == "human_confirmed_model_candidate"
    assert reconfirmed["correction"]["applied"] is True


def test_unable_to_confirm_keeps_candidate_but_locks_formal_state(tmp_path: Path) -> None:
    report = _image_report(tmp_path)
    unable = mark_structure_unable_to_confirm(report)

    assert unable["human_review"]["status"] == "unable_to_confirm"
    assert unable["human_review"]["reviewed_smiles"] == report["final"]["smiles"]
    assert is_structure_confirmed(unable) is False
    assert unable["final"] == report["final"]


def test_pdf_switches_from_watermarked_candidate_to_formal_report(tmp_path: Path) -> None:
    report = _image_report(tmp_path)
    candidate = save_pdf(report, tmp_path / "candidate.pdf")
    assert candidate["success"] is True
    assert candidate["report_type"] == "candidate"
    assert candidate["watermarked"] is True

    confirmed_report = confirm_structure(report)
    formal = save_pdf(confirmed_report, tmp_path / "formal.pdf")
    assert formal["success"] is True
    assert formal["report_type"] == "formal"
    assert formal["watermarked"] is False

    for result in (candidate, formal):
        assert Path(result["path"]).stat().st_size > 1000
    assert _clean(None) == ""
    assert _clean("None") == ""


def test_lipinski_summary_lists_only_specific_exceeded_items() -> None:
    result = evaluate_lipinski({
        "molecular_weight": 650,
        "logp": 7,
        "hbd": 0,
        "hba": 1,
        "rotatable_bonds": 14,
    })
    assert "类药性风险较高" not in result["summary"]
    assert "MW > 500" in result["summary"]
    assert "LogP > 5" in result["summary"]
    assert "Rotatable Bonds > 10" in result["summary"]


def test_single_image_ui_contains_required_review_and_export_language() -> None:
    source = (PROJECT_ROOT / "src" / "ui" / "report_view.py").read_text(encoding="utf-8")
    for phrase in (
        "候选结构",
        "确认结构正确",
        "修改 SMILES",
        "无法确认",
        "确认与修正",
        "撤销确认",
        "重新修改结构",
        "候选性质预览",
        "未人工确认",
        "下载正式 PDF",
        "下载 SMI",
        "下载 MOL",
        "下载 SDF",
        "复制最终 SMILES",
        "复制 Canonical SMILES",
        "复制 InChIKey",
    ):
        assert phrase in source
    assert "纠错反馈与数据回流" not in source
    assert "保存为待审核" not in source


def test_candidate_tab_is_hidden_until_a_second_candidate_exists(tmp_path: Path) -> None:
    report = _image_report(tmp_path)
    assert "候选比较" not in _report_section_options(report)

    report["ocsr"]["strategy_attempts"] = [
        {"smiles": "CCO"},
        {"smiles": "OCC"},
    ]
    assert "候选比较" not in _report_section_options(report)

    report["ocsr"]["candidates"] = [
        {"backend": "first", "raw_smiles": "CCO"},
        {"backend": "second", "raw_smiles": "CCN"},
    ]
    assert "候选比较" in _report_section_options(report)


def test_technical_payload_uses_seconds_and_hides_empty_values() -> None:
    compact = _compact_technical({
        "inference_time_ms": 1250,
        "empty": None,
        "blank": "",
        "literal_none": "None",
        "nested": {"inference_time_ms": 25, "unused": []},
        "zero": 0,
    })
    assert compact == {
        "inference_time_seconds": 1.25,
        "nested": {"inference_time_seconds": 0.025},
        "zero": 0,
    }


def test_ui_timestamp_is_local_and_readable() -> None:
    formatted = _format_local_time("2026-07-20T01:23:45+00:00")
    assert formatted.startswith("2026-07-20")
    assert formatted[10] == " "


def test_pdf_contains_redraw_orientation_explanation() -> None:
    source = (PROJECT_ROOT / "src" / "export" / "pdf_exporter.py").read_text(encoding="utf-8")
    assert "二维结构重绘的方向和排版可能与原图不同" in source
    assert "原子、键型和连接关系为准" in source
