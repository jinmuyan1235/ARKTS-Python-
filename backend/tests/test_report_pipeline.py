"""End-to-end tests for image, SMILES, batch, and report export workflows."""

import json
from pathlib import Path

import pandas as pd

from src.analysis.batch_analyzer import BatchAnalyzer
from src.analysis.molecule_report import MoleculeReportGenerator
from src.export.pdf_exporter import save_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manual_smiles_pipeline_creates_unique_outputs(tmp_path: Path) -> None:
    generator = MoleculeReportGenerator("demo", tmp_path)
    first = generator.generate(smiles="CCO")
    second = generator.generate(smiles="CCO")
    assert first["status"] == "success"
    assert first["validation"]["canonical_smiles"] == "CCO"
    assert first["admet"]["status"] == "disabled"
    assert first["analysis_id"] != second["analysis_id"]
    assert first["images"]["redrawn_molecule"] != second["images"]["redrawn_molecule"]
    assert Path(first["images"]["redrawn_molecule"]).is_file()


def test_manual_smiles_pipeline_works_in_production_mode(monkeypatch, tmp_path: Path) -> None:
    import config

    monkeypatch.setattr(config, "APP_MODE", "production")
    report = MoleculeReportGenerator("manual", tmp_path).generate(smiles="CCO")
    assert report["status"] == "success"
    assert report["ocsr"]["backend"] == "manual"


def test_demo_image_pipeline_and_pdf_export(tmp_path: Path) -> None:
    sample = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=sample)
    assert report["status"] == "success"
    assert report["ocsr"]["backend"] == "demo"
    assert report["ocsr"]["selected_strategy"] in {"original", "enhanced"}
    assert report["ocsr"]["strategy_attempt_count"] >= 1
    assert report["ocsr"]["strategy_attempts"][0]["strategy"] == "original"
    assert set(report["images"]["preprocessing"]) >= {
        "original", "gray", "denoised", "binary", "cropped", "deskewed", "normalized"
    }
    pdf_result = save_pdf(report, tmp_path / "aspirin_report.pdf")
    assert pdf_result["success"] is True
    assert Path(pdf_result["path"]).stat().st_size > 1000


def test_batch_pipeline_exports_success_and_failure(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    source = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    (input_dir / "aspirin.png").write_bytes(source.read_bytes())
    (input_dir / "unknown.png").write_bytes(source.read_bytes())
    result = BatchAnalyzer("demo", output_dir).analyze_folder(input_dir)
    assert result["summary"]["total"] == 2
    assert result["summary"]["successful"] == 1
    assert result["summary"]["failed"] == 1
    assert Path(result["exports"]["csv"]).is_file()
    assert Path(result["exports"]["json"]).is_file()
    assert Path(result["exports"]["summary_chart"]).is_file()
    assert Path(result["exports"]["merged_sdf"]).is_file()
    assert Path(result["exports"]["successful_zip"]).is_file()
    assert Path(result["exports"]["failed_csv"]).is_file()
    assert Path(result["exports"]["review_csv"]).is_file()
    frame = pd.read_csv(result["exports"]["csv"])
    assert set(frame["status"]) == {"success", "failed"}
    exported = json.loads(Path(result["exports"]["json"]).read_text(encoding="utf-8"))
    assert exported["summary"]["total"] == 2
