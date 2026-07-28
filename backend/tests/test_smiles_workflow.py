"""SMILES page validation, batch parsing, caching, and export tests."""

from __future__ import annotations

from pathlib import Path

from src.analysis.smiles_workflow import (
    parse_smiles_text,
    parse_smiles_upload,
    single_smiles_export_row,
    smiles_batch_exports,
)
from src.chem.descriptors import calculate_descriptors
from src.chem.smiles_validator import diagnose_smiles


def _manual_report(smiles: str = "CCO") -> dict:
    return {
        "analysis_id": "manual-test",
        "status": "success",
        "message": "完成",
        "input": {"type": "smiles", "smiles": smiles},
        "final": {
            "smiles": smiles,
            "raw_smiles": smiles,
            "canonical_smiles": smiles,
            "standardized_smiles": smiles,
            "source": "manual",
        },
        "validation": {"valid": True, "canonical_smiles": smiles, "standardized_smiles": smiles},
        "chemical_identity": {
            "raw_smiles": smiles,
            "canonical_smiles": smiles,
            "standardized_smiles": smiles,
            "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            "formula": "C2H6O",
            "formal_charge": 0,
            "fragment_count": 1,
        },
        "descriptors": calculate_descriptors(smiles),
        "human_review": {"required": False, "status": "not_required", "confirmed": True},
        "images": {},
    }


def test_diagnose_smiles_returns_position_and_repair_hints(capfd) -> None:
    result = diagnose_smiles("CC(C")

    captured = capfd.readouterr()
    assert result["valid"] is False
    assert result["error_position"] == 3
    assert "括号" in result["error"]
    assert any("括号" in hint for hint in result["suggestions"])
    assert "SMILES Parse Error" not in captured.err


def test_diagnose_unclosed_ring_points_to_ring_number() -> None:
    result = diagnose_smiles("C1CC")

    assert result["valid"] is False
    assert result["error_position"] == 2
    assert any("环编号" in hint for hint in result["suggestions"])


def test_parse_batch_paste_and_smi_names() -> None:
    entries = parse_smiles_text("CCO ethanol\n\nCC(=O)O\tacetic acid\n# comment")

    assert [entry["smiles"] for entry in entries] == ["CCO", "CC(=O)O"]
    assert [entry["name"] for entry in entries] == ["ethanol", "acetic acid"]
    assert parse_smiles_upload("demo.smi", b"CCN amine\n")[0]["name"] == "amine"


def test_parse_csv_uses_named_smiles_column_and_gb18030() -> None:
    content = "编号,SMILES\n样品一,CCO\n".encode("gb18030")
    entries = parse_smiles_upload("demo.csv", content)

    assert entries == [{
        "source": "demo.csv",
        "line_number": 2,
        "smiles": "CCO",
        "name": "样品一",
        "raw_line": "样品一,CCO",
    }]


def test_descriptors_include_ring_charge_and_fragment_counts() -> None:
    descriptors = calculate_descriptors("[Na+].[O-]C(=O)c1ccccc1")

    assert descriptors["ring_count"] == 1
    assert descriptors["formal_charge"] == 0
    assert descriptors["fragment_count"] == 2


def test_batch_exports_include_smi_sdf_csv_and_failure_list() -> None:
    report = _manual_report()
    rows = [
        {"状态": "成功", "失败原因": "", "原始 SMILES": "CCO"},
        {"状态": "失败", "失败原因": "括号未闭合", "原始 SMILES": "CC("},
    ]

    exports = smiles_batch_exports(rows, [report])

    assert b"CCO" in exports["smi"]
    assert b"$$$$" in exports["sdf"]
    assert exports["csv"].startswith(b"\xef\xbb\xbf")
    assert "括号未闭合" in exports["failed_csv"].decode("utf-8-sig")


def test_single_export_row_accepts_legacy_manual_report() -> None:
    legacy = {
        "input": {"type": "smiles", "smiles": "OCC"},
        "final": {"smiles": "CCO"},
        "validation": {"canonical_smiles": "CCO"},
        "descriptors": {"molecular_weight": 46.07},
    }

    row = single_smiles_export_row(legacy)

    assert row["Original SMILES"] == "OCC"
    assert row["Canonical SMILES"] == "CCO"
    assert row["Molecular Weight"] == 46.07


def test_smiles_page_exposes_all_requested_workflows() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "smiles_page.py").read_text(encoding="utf-8")

    for phrase in (
        "单条分析",
        "批量分析",
        "最近分析历史",
        "CSV/SMI 文件",
        "恢复原始输入",
        "结构指纹相似度",
        "复制 Canonical SMILES",
        "复制 Standardized SMILES",
        "复制 InChIKey",
        "下载 SMI",
        "下载 MOL",
        "下载 SDF",
        "下载 CSV",
        "下载 PDF",
        "下载失败清单",
    ):
        assert phrase in source
