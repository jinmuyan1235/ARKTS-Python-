"""Chemistry-native structure export helpers."""

from __future__ import annotations

import json
import shutil
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem, Draw

from src.chem.mol_drawer import draw_molecule
from src.chem.smiles_validator import smiles_to_mol, suppress_rdkit_parse_errors
from src.analysis.correction import is_structure_confirmed
from src.export.csv_exporter import save_csv
from src.utils.file_utils import ensure_directory, safe_stem


SDF_PROPERTY_FIELDS = (
    "ANALYSIS_ID",
    "SOURCE_FILENAME",
    "OCSR_BACKEND",
    "DECISION",
    "MODEL_CONFIDENCE",
    "FINAL_SOURCE",
    "IMAGE_SHA256",
)

DEFAULT_LIST_COLUMNS = ("analysis_id", "filename", "status", "message", "decision", "final_smiles")


def copyable_structure_fields(report: Mapping[str, Any]) -> dict[str, str]:
    """Return user-facing structure identifiers that can be copied from the UI."""
    mol = _molecule_from_report(report)
    identity = _identity_block(report)
    return {
        "original_smiles": report_raw_smiles(report) or "",
        "canonical_smiles": report_canonical_smiles(report, mol) or "",
        "inchi": str(identity.get("inchi") or _safe_inchi(mol) or ""),
        "inchikey": str(identity.get("inchikey") or _safe_inchikey(mol) or ""),
    }


def report_raw_smiles(report: Mapping[str, Any]) -> str | None:
    """Extract the closest-to-source SMILES from a report."""
    final = _block(report, "final")
    ocsr = _block(report, "ocsr")
    input_data = _block(report, "input")
    for value in (
        final.get("raw_smiles"),
        ocsr.get("predicted_smiles"),
        ocsr.get("smiles"),
        input_data.get("smiles"),
    ):
        if value:
            return str(value)
    return None


def report_structure_smiles(report: Mapping[str, Any]) -> str | None:
    """Extract the best SMILES to serialize as a molecule structure."""
    final = _block(report, "final")
    validation = _block(report, "validation")
    identity = _identity_block(report)
    ocsr = _block(report, "ocsr")
    for value in (
        final.get("standardized_smiles"),
        final.get("smiles"),
        validation.get("standardized_smiles"),
        identity.get("standardized_smiles"),
        final.get("canonical_smiles"),
        validation.get("canonical_smiles"),
        identity.get("canonical_smiles"),
        ocsr.get("smiles"),
    ):
        if value:
            return str(value)
    return report_raw_smiles(report)


def report_canonical_smiles(report: Mapping[str, Any], mol: Chem.Mol | None = None) -> str | None:
    """Extract or compute canonical SMILES for a report."""
    final = _block(report, "final")
    validation = _block(report, "validation")
    identity = _identity_block(report)
    for value in (
        final.get("canonical_smiles"),
        validation.get("canonical_smiles"),
        identity.get("canonical_smiles"),
    ):
        if value:
            return str(value)
    molecule = mol or _molecule_from_report(report)
    if molecule is None:
        return None
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)


def can_export_structure(report: Mapping[str, Any]) -> bool:
    """Return True when a report has a valid structure that RDKit can serialize."""
    return _molecule_from_report(report) is not None


def mol_text(report: Mapping[str, Any]) -> str:
    """Return an RDKit MOL block for the report's final structure."""
    mol = _require_molecule(report)
    return Chem.MolToMolBlock(mol)


def sdf_properties(report: Mapping[str, Any]) -> OrderedDict[str, str]:
    """Return SDF properties with stable audit fields."""
    input_data = _block(report, "input")
    ocsr = _block(report, "ocsr")
    final = _block(report, "final")
    decision = _block(report, "recognition_decision")
    fields = copyable_structure_fields(report)
    properties: OrderedDict[str, str] = OrderedDict()
    properties["ANALYSIS_ID"] = _property_value(report.get("analysis_id"))
    properties["SOURCE_FILENAME"] = _property_value(input_data.get("filename") or input_data.get("path"))
    properties["OCSR_BACKEND"] = _property_value(ocsr.get("backend"))
    properties["DECISION"] = _property_value(decision.get("decision") or ocsr.get("decision"))
    properties["MODEL_CONFIDENCE"] = _property_value(ocsr.get("confidence"))
    properties["FINAL_SOURCE"] = _property_value(final.get("source"))
    properties["IMAGE_SHA256"] = _property_value(input_data.get("image_sha256"))
    properties["FINAL_SMILES"] = _property_value(report_structure_smiles(report))
    properties["ORIGINAL_SMILES"] = _property_value(fields.get("original_smiles"))
    properties["CANONICAL_SMILES"] = _property_value(fields.get("canonical_smiles"))
    properties["INCHI"] = _property_value(fields.get("inchi"))
    properties["INCHIKEY"] = _property_value(fields.get("inchikey"))
    return properties


def sdf_text(report: Mapping[str, Any]) -> str:
    """Return an SDF record for the report's final structure."""
    lines = [mol_text(report).rstrip()]
    for key, value in sdf_properties(report).items():
        lines.extend((f">  <{key}>", value, ""))
    lines.append("$$$$")
    return "\n".join(lines) + "\n"


def svg_text(report: Mapping[str, Any], size: tuple[int, int] = (600, 450)) -> str:
    """Return an SVG rendering for the report's final structure."""
    mol = _require_molecule(report)
    image = Draw.MolsToGridImage([mol], molsPerRow=1, subImgSize=size, useSVG=True)
    return str(image)


def png_bytes(report: Mapping[str, Any], output_dir: str | Path | None = None) -> bytes:
    """Return PNG bytes, using an existing redrawn molecule when available."""
    existing = (_block(report, "images")).get("redrawn_molecule")
    if existing and Path(str(existing)).is_file():
        return Path(str(existing)).read_bytes()
    if output_dir is None:
        raise ValueError("需要 output_dir 生成 PNG 导出。")
    destination = ensure_directory(output_dir) / f"{structure_export_prefix(report)}.png"
    draw_molecule(str(report_structure_smiles(report)), destination)
    return destination.read_bytes()


def export_structure_files(report: Mapping[str, Any], output_dir: str | Path, prefix: str | None = None) -> dict[str, str]:
    """Write MOL, SDF, SVG, PNG, and ZIP exports for one report."""
    destination = ensure_directory(output_dir)
    stem = safe_stem(prefix or structure_export_prefix(report), "structure")
    paths = _write_single_structure_files(report, destination, stem)
    zip_path = destination / f"{stem}_structure_exports.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for key in ("mol", "sdf", "svg", "png"):
            path = Path(paths[key])
            archive.write(path, path.name)
        archive.writestr(f"{stem}_fields.json", json.dumps(copyable_structure_fields(report), ensure_ascii=False, indent=2))
    paths["zip"] = str(zip_path.resolve())
    return paths


def export_batch_structure_files(
    reports: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    rows: Iterable[Mapping[str, Any]] | None = None,
    file_prefix: str = "batch",
) -> dict[str, str]:
    """Write merged SDF, successful-structure ZIP, failed CSV, and review CSV for many reports."""
    report_list = list(reports)
    row_list = list(rows) if rows is not None else [_fallback_row(report) for report in report_list]
    destination = ensure_directory(output_dir)
    prefix = safe_stem(file_prefix, "batch")
    merged_sdf = destination / f"{prefix}_merged_structures.sdf"
    merged_smi = destination / f"{prefix}_merged_structures.smi"
    successful_zip = destination / f"{prefix}_confirmed_structures.zip"
    complete_zip = destination / f"{prefix}_complete_results.zip"
    failed_csv = destination / f"{prefix}_failed_results.csv"
    review_csv = destination / f"{prefix}_pending_review.csv"

    sdf_blocks: list[str] = []
    smiles_lines: list[str] = []
    failed_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []

    with zipfile.ZipFile(successful_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, report in enumerate(report_list, start=1):
            row = _row_for_index(row_list, index - 1, report)
            confirmed = is_structure_confirmed(dict(report))
            if requires_review(report) or not confirmed:
                review_rows.append(row)
            if report.get("status") != "success":
                failed_rows.append(row)
                continue
            if not confirmed:
                continue
            if not can_export_structure(report):
                failed_rows.append(row)
                continue

            stem = f"{index:04d}_{structure_export_prefix(report)}"
            sdf = sdf_text(report)
            sdf_blocks.append(sdf)
            smiles = report_structure_smiles(report)
            if smiles:
                smiles_lines.append(f"{smiles}\t{stem}")
            archive.writestr(f"{stem}.mol", mol_text(report))
            archive.writestr(f"{stem}.sdf", sdf)
            archive.writestr(f"{stem}.svg", svg_text(report))
            archive.writestr(f"{stem}_fields.json", json.dumps(copyable_structure_fields(report), ensure_ascii=False, indent=2))
            try:
                archive.writestr(f"{stem}.png", png_bytes(report, destination / "png_cache"))
            except Exception:
                pass

    merged_sdf.write_text("".join(sdf_blocks), encoding="utf-8")
    merged_smi.write_text("\n".join(smiles_lines) + ("\n" if smiles_lines else ""), encoding="utf-8")
    _save_list_csv(failed_rows, failed_csv, row_list)
    _save_list_csv(review_rows, review_csv, row_list)
    with zipfile.ZipFile(complete_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (merged_sdf, merged_smi, successful_zip, failed_csv, review_csv):
            archive.write(path, path.name)
        for index, report in enumerate(report_list, start=1):
            input_path = Path(str(_block(report, "input").get("path") or ""))
            if input_path.is_file():
                archive.write(input_path, f"originals/{index:04d}_{safe_stem(input_path.name, 'image')}")
            redraw_path = Path(str(_block(report, "images").get("redrawn_molecule") or ""))
            if redraw_path.is_file():
                archive.write(redraw_path, f"candidate_previews/{index:04d}_{safe_stem(redraw_path.name, 'structure.png')}")
    return {
        "merged_sdf": str(merged_sdf.resolve()),
        "merged_smi": str(merged_smi.resolve()),
        "successful_zip": str(successful_zip.resolve()),
        "complete_zip": str(complete_zip.resolve()),
        "failed_csv": str(failed_csv.resolve()),
        "review_csv": str(review_csv.resolve()),
    }


def requires_review(report: Mapping[str, Any]) -> bool:
    """Return True when a report should be listed for human review."""
    decision = _block(report, "recognition_decision")
    consensus = _block(_block(report, "ocsr"), "consensus")
    final_decision = decision.get("decision") or consensus.get("decision")
    return bool(
        not is_structure_confirmed(dict(report))
        or
        decision.get("manual_review_recommended")
        or final_decision in {"review_needed", "accepted_with_warning"}
        or consensus.get("status") == "disagreement"
    )


def structure_export_prefix(report: Mapping[str, Any]) -> str:
    """Build a stable, filesystem-safe export prefix for a report."""
    input_data = _block(report, "input")
    filename = input_data.get("filename") or Path(str(input_data.get("path") or "")).stem
    analysis_id = str(report.get("analysis_id") or "")
    parts = [safe_stem(str(filename), "structure")]
    if analysis_id:
        parts.append(safe_stem(analysis_id[:8], "analysis"))
    return "_".join(part for part in parts if part)


def _write_single_structure_files(report: Mapping[str, Any], destination: Path, stem: str) -> dict[str, str]:
    paths = {
        "mol": destination / f"{stem}.mol",
        "sdf": destination / f"{stem}.sdf",
        "svg": destination / f"{stem}.svg",
        "png": destination / f"{stem}.png",
    }
    paths["mol"].write_text(mol_text(report), encoding="utf-8")
    paths["sdf"].write_text(sdf_text(report), encoding="utf-8")
    paths["svg"].write_text(svg_text(report), encoding="utf-8")
    existing = (_block(report, "images")).get("redrawn_molecule")
    if existing and Path(str(existing)).is_file():
        shutil.copy2(str(existing), paths["png"])
    else:
        draw_molecule(str(report_structure_smiles(report)), paths["png"])
    return {key: str(path.resolve()) for key, path in paths.items()}


def _molecule_from_report(report: Mapping[str, Any]) -> Chem.Mol | None:
    smiles = report_structure_smiles(report)
    if not smiles:
        return None
    mol = smiles_to_mol(smiles)
    if mol is None:
        return None
    AllChem.Compute2DCoords(mol)
    return mol


def _require_molecule(report: Mapping[str, Any]) -> Chem.Mol:
    mol = _molecule_from_report(report)
    if mol is None:
        raise ValueError("报告中没有可导出的有效分子结构。")
    return mol


def _identity_block(report: Mapping[str, Any]) -> Mapping[str, Any]:
    return _block(report, "chemical_identity")


def _block(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


def _safe_inchi(mol: Chem.Mol | None) -> str | None:
    if mol is None:
        return None
    try:
        with suppress_rdkit_parse_errors():
            return Chem.MolToInchi(mol)
    except Exception:
        return None


def _safe_inchikey(mol: Chem.Mol | None) -> str | None:
    if mol is None:
        return None
    try:
        with suppress_rdkit_parse_errors():
            return Chem.MolToInchiKey(mol)
    except Exception:
        return None


def _property_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text.replace("\r", " ").replace("\n", " ").strip()


def _fallback_row(report: Mapping[str, Any]) -> dict[str, Any]:
    input_data = _block(report, "input")
    decision = _block(report, "recognition_decision")
    return {
        "analysis_id": report.get("analysis_id"),
        "filename": input_data.get("filename") or input_data.get("path"),
        "status": report.get("status"),
        "message": report.get("message"),
        "decision": decision.get("decision"),
        "final_smiles": report_structure_smiles(report),
    }


def _row_for_index(row_list: list[Mapping[str, Any]], index: int, report: Mapping[str, Any]) -> dict[str, Any]:
    if index < len(row_list):
        return dict(row_list[index])
    return _fallback_row(report)


def _save_list_csv(rows: list[dict[str, Any]], output_path: Path, templates: list[Mapping[str, Any]]) -> None:
    if rows:
        save_csv(rows, output_path)
        return
    columns = list(templates[0].keys()) if templates else list(DEFAULT_LIST_COLUMNS)
    save_csv(pd.DataFrame(columns=columns), output_path)
