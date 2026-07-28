from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from src.analysis.molecule_report import MoleculeReportGenerator
from src.analysis.correction import confirm_structure, reset_human_review
from src.export.structure_exporter import (
    SDF_PROPERTY_FIELDS,
    copyable_structure_fields,
    export_batch_structure_files,
    export_structure_files,
    sdf_text,
)


def _report(smiles: str, tmp_path: Path, analysis_id: str, filename: str) -> dict:
    report = MoleculeReportGenerator("manual", tmp_path).generate(smiles=smiles, analysis_id=analysis_id)
    report["input"].update({"filename": filename, "image_sha256": f"sha-{analysis_id}"})
    report["ocsr"]["confidence"] = 0.91
    report["recognition_decision"] = {"decision": "accepted", "manual_review_recommended": False}
    return report


def test_single_structure_export_writes_chemistry_formats(tmp_path: Path) -> None:
    report = _report("CCO", tmp_path, "ethanol123", "ethanol.png")

    exports = export_structure_files(report, tmp_path / "exports", prefix="ethanol")

    assert set(exports) == {"mol", "sdf", "svg", "png", "zip"}
    assert "V2000" in Path(exports["mol"]).read_text(encoding="utf-8")
    sdf = Path(exports["sdf"]).read_text(encoding="utf-8")
    assert "$$$$" in sdf
    for field in SDF_PROPERTY_FIELDS:
        assert f">  <{field}>" in sdf
    assert ">  <SOURCE_FILENAME>\nethanol.png" in sdf
    assert ">  <IMAGE_SHA256>\nsha-ethanol123" in sdf
    assert Path(exports["svg"]).read_text(encoding="utf-8").lstrip().startswith("<?xml")
    assert Path(exports["png"]).read_bytes().startswith(b"\x89PNG")

    fields = copyable_structure_fields(report)
    assert fields["original_smiles"] == "CCO"
    assert fields["canonical_smiles"] == "CCO"
    assert fields["inchi"].startswith("InChI=")
    assert fields["inchikey"]

    with zipfile.ZipFile(exports["zip"]) as archive:
        names = set(archive.namelist())
    assert {"ethanol.mol", "ethanol.sdf", "ethanol.svg", "ethanol.png", "ethanol_fields.json"}.issubset(names)


def test_batch_structure_export_writes_sdf_zip_and_review_lists(tmp_path: Path) -> None:
    accepted = _report("CCO", tmp_path / "accepted", "accepted001", "accepted.png")
    review = _report("c1ccccc1", tmp_path / "review", "review001", "review.png")
    review["recognition_decision"] = {"decision": "review_needed", "manual_review_recommended": True}
    failed = {
        "analysis_id": "failed001",
        "status": "failed",
        "message": "no smiles",
        "input": {"filename": "failed.png"},
    }
    rows = [
        {"analysis_id": "accepted001", "filename": "accepted.png", "status": "success"},
        {"analysis_id": "review001", "filename": "review.png", "status": "success"},
        {"analysis_id": "failed001", "filename": "failed.png", "status": "failed"},
    ]

    exports = export_batch_structure_files([accepted, review, failed], tmp_path / "batch", rows)

    merged = Path(exports["merged_sdf"]).read_text(encoding="utf-8")
    assert merged.count("$$$$") == 2
    assert ">  <ANALYSIS_ID>\naccepted001" in merged
    assert ">  <ANALYSIS_ID>\nreview001" in merged
    merged_smi = Path(exports["merged_smi"]).read_text(encoding="utf-8")
    assert "CCO\t" in merged_smi
    assert "c1ccccc1\t" in merged_smi

    with zipfile.ZipFile(exports["successful_zip"]) as archive:
        names = archive.namelist()
    assert any(name.endswith(".mol") for name in names)
    assert any(name.endswith(".sdf") for name in names)
    assert any(name.endswith(".svg") for name in names)

    failed_frame = pd.read_csv(exports["failed_csv"])
    assert list(failed_frame["analysis_id"]) == ["failed001"]
    review_frame = pd.read_csv(exports["review_csv"])
    assert list(review_frame["analysis_id"]) == ["review001"]


def test_batch_formal_exports_exclude_unconfirmed_image_candidates(tmp_path: Path) -> None:
    candidate = _report("CCO", tmp_path / "candidate", "candidate001", "candidate.png")
    candidate["input"]["type"] = "image"
    candidate = reset_human_review(candidate)
    rows = [{"analysis_id": "candidate001", "filename": "candidate.png", "status": "success"}]

    unconfirmed = export_batch_structure_files([candidate], tmp_path / "unconfirmed", rows)
    assert Path(unconfirmed["merged_sdf"]).read_text(encoding="utf-8") == ""
    assert Path(unconfirmed["merged_smi"]).read_text(encoding="utf-8") == ""
    with zipfile.ZipFile(unconfirmed["successful_zip"]) as archive:
        assert archive.namelist() == []
    assert list(pd.read_csv(unconfirmed["review_csv"])["analysis_id"]) == ["candidate001"]

    confirmed = confirm_structure(candidate)
    formal = export_batch_structure_files([confirmed], tmp_path / "confirmed", rows)
    assert "$$$$" in Path(formal["merged_sdf"]).read_text(encoding="utf-8")
    assert "CCO" in Path(formal["merged_smi"]).read_text(encoding="utf-8")
    assert Path(formal["complete_zip"]).is_file()


def test_sdf_text_contains_required_audit_properties(tmp_path: Path) -> None:
    report = _report("CC(=O)O", tmp_path, "acetic001", "acetic.png")
    text = sdf_text(report)
    for field in SDF_PROPERTY_FIELDS:
        assert f">  <{field}>" in text
    assert ">  <OCSR_BACKEND>\nmanual" in text
    assert ">  <MODEL_CONFIDENCE>\n0.91" in text
