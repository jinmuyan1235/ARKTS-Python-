"""Input-domain and backend-failure diagnostics for trusted OCSR runs."""

from __future__ import annotations

import csv
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rdkit import Chem
from rdkit.Chem import Draw

from src.ocsr.base import OCSRResult
from src.ocsr.input_normalization import image_statistics
from src.ocsr.reliability import classify_backend_failure


def _bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], default_fields: tuple[str, ...]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) if rows else list(default_fields)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _group_counts(rows: list[dict[str, Any]], keys: tuple[str, ...], value_name: str) -> list[dict[str, Any]]:
    groups: Counter[tuple[str, ...]] = Counter(tuple(str(row.get(key) or "unspecified") for key in keys) for row in rows)
    return [{**dict(zip(keys, values)), value_name: count} for values, count in sorted(groups.items())]


def _draw_smiles(smiles: str, output: Path) -> bool:
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return False
    Draw.MolToImage(mol, size=(420, 420), kekulize=True).save(output)
    return True


def _prediction_files(evaluation_root: Path, backend: str) -> list[Path]:
    candidates = (
        evaluation_root / "development_baseline" / backend / "predictions.csv",
        evaluation_root / "dev_preprocessing" / "runs" / backend / "raw" / "predictions.csv",
        evaluation_root / backend / "predictions.csv",
    )
    return [path for path in candidates if path.is_file()]


def _failure_category(row: dict[str, str], backend: str) -> str:
    if row.get("failure_category"):
        return row["failure_category"]
    result = OCSRResult(
        smiles=row.get("predicted_smiles") or None,
        confidence=None,
        backend=backend,
        status="success" if row.get("backend_status") == "success" else "failed",
        message=row.get("message") or "",
        raw_output=row.get("raw_output") or None,
    )
    return classify_backend_failure(result)


def _build_gallery(
    dataset_root: Path,
    gallery: Path,
    manifest_by_cid: dict[str, dict[str, dict[str, str]]],
    predictions: dict[str, dict[str, dict[str, str]]],
    selected: list[dict[str, Any]],
    stats_by_sample: dict[str, dict[str, Any]],
) -> None:
    assets = gallery / "assets"; assets.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    seen: set[str] = set()
    for item in selected:
        cid = str(item["pubchem_cid"])
        if cid in seen or cid not in manifest_by_cid: continue
        seen.add(cid)
        variants = manifest_by_cid[cid]
        columns: list[str] = []
        for variant in ("official_clean", "rendered_clean", "synthetic_perturbation"):
            row = variants.get(variant)
            if not row: continue
            source = dataset_root / row["image_path"]
            destination = assets / f"CID_{cid}_{variant}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            columns.append(f'<div><h4>{html.escape(variant)}</h4><img src="assets/{destination.name}"></div>')
        truth = next(iter(variants.values()))
        truth_path = assets / f"CID_{cid}_ground_truth.png"
        _draw_smiles(truth.get("ground_truth_isomeric_smiles", ""), truth_path)
        columns.append(f'<div><h4>ground-truth RDKit redraw</h4><img src="assets/{truth_path.name}"></div>')
        prediction_text: list[str] = []
        for backend in ("molscribe", "decimer"):
            backend_rows = predictions.get(backend, {})
            for variant, variant_row in variants.items():
                prediction = backend_rows.get(variant_row["sample_id"])
                if not prediction:
                    continue
                predicted = prediction.get("predicted_smiles", "")
                redraw = assets / f"CID_{cid}_{backend}_{variant}_prediction.png"
                if _draw_smiles(predicted, redraw):
                    columns.append(f'<div><h4>{backend} / {variant} redraw</h4><img src="assets/{redraw.name}"></div>')
                prediction_text.append(
                    f"<b>{backend} / {variant}</b>: status={html.escape(prediction.get('backend_status',''))}; "
                    f"failure={html.escape(prediction.get('failure_category',''))}; "
                    f"SMILES={html.escape(predicted)}; message={html.escape(prediction.get('message',''))}"
                )
        stat_rows = [stats_by_sample.get(row["sample_id"], {}) for row in variants.values()]
        cards.append(
            f'<section><h2>CID {cid} — {html.escape(str(item.get("selection_reason", "diagnostic")))}</h2>'
            f'<div class="grid">{"".join(columns)}</div><p>{"<br>".join(prediction_text)}</p>'
            f'<pre>{html.escape(json.dumps(stat_rows, ensure_ascii=False, indent=2))}</pre></section>'
        )
    page = """<!doctype html><meta charset="utf-8"><title>Trusted OCSR diagnostics</title>
<style>body{font-family:sans-serif;margin:20px;background:#f5f7f7}section{background:white;padding:16px;margin:18px 0;border-radius:10px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.grid img{width:100%;max-height:320px;object-fit:contain;border:1px solid #ddd}pre{white-space:pre-wrap;font-size:11px}</style>
""" + "\n".join(cards)
    (gallery / "index.html").write_text(page, encoding="utf-8")


def analyze_trusted_failures(
    manifest: Path,
    evaluation_root: Path,
    output: Path,
    include_frozen_test: bool = False,
) -> dict[str, Any]:
    dataset_root = manifest.resolve().parent
    manifest_rows = _read_csv(manifest)
    allowed_splits = {"train", "dev"} | ({"test"} if include_frozen_test else set())
    selected_manifest = [row for row in manifest_rows if row.get("split") in allowed_splits]
    sample_ids = {row["sample_id"] for row in selected_manifest}
    prediction_rows: dict[str, list[dict[str, str]]] = {}
    for backend in ("molscribe", "decimer", "ensemble"):
        by_sample: dict[str, dict[str, str]] = {}
        for prediction_path in _prediction_files(evaluation_root, backend):
            for row in _read_csv(prediction_path):
                if row.get("sample_id") in sample_ids:
                    by_sample.setdefault(row["sample_id"], row)
        prediction_rows[backend] = list(by_sample.values())
    if not any(prediction_rows.values()):
        raise ValueError(
            "No train/dev predictions found. Run evaluate_trusted_ocsr.py with "
            "--splits train,dev --purpose diagnostic before diagnostics."
        )
    output.mkdir(parents=True, exist_ok=True)
    stats_rows: list[dict[str, Any]] = []
    manifest_by_sample = {row["sample_id"]: row for row in selected_manifest}
    for row in selected_manifest:
        stats_rows.append({
            "sample_id": row["sample_id"], "pubchem_cid": row["pubchem_cid"],
            "split": row["split"], "image_variant": row["image_variant"],
            **image_statistics(dataset_root / row["image_path"]),
        })
    stats_by_sample = {row["sample_id"]: row for row in stats_rows}
    failures: list[dict[str, Any]] = []
    enriched_predictions: list[dict[str, Any]] = []
    for backend, rows in prediction_rows.items():
        for row in rows:
            manifest_row = manifest_by_sample[row["sample_id"]]
            merged = {**manifest_row, **row, **stats_by_sample.get(row["sample_id"], {}), "backend": backend}
            enriched_predictions.append(merged)
            if row.get("backend_status") != "success":
                failures.append({
                    "backend": backend, "sample_id": row["sample_id"], "pubchem_cid": manifest_row["pubchem_cid"],
                    "image_variant": manifest_row["image_variant"], "split": manifest_row["split"],
                    "failure_category": _failure_category(row, backend),
                    "exception_type": row.get("exception_type", ""),
                    "exception_summary": row.get("exception_summary", ""), "message": row.get("message", ""),
                    "raw_output": row.get("raw_output", ""), "attempt_count": row.get("attempt_count", "1"),
                    "width": merged.get("width"), "height": merged.get("height"),
                    "complexity_group": row.get("complexity_group", ""),
                    "structure_features": manifest_row.get("structure_features", ""),
                })
    _write_csv(output / "backend_failure_reasons.csv", failures, ("backend", "sample_id", "failure_category", "message"))
    failure_matrix = _group_counts(failures, ("backend", "image_variant", "failure_category", "width", "height", "complexity_group", "structure_features"), "failure_count")
    _write_csv(output / "per_variant_failure_matrix.csv", failure_matrix, ("backend", "image_variant", "failure_category", "failure_count"))
    _write_csv(output / "image_statistics.csv", stats_rows, ("sample_id", "image_variant", "width", "height"))
    perturbations: list[dict[str, Any]] = []
    for row in selected_manifest:
        if row.get("image_variant") != "synthetic_perturbation": continue
        params = json.loads(row.get("perturbation_parameters") or "{}")
        severity = "high" if float(params.get("noise_sigma", 0)) >= 4 or int(params.get("jpeg_quality", 100)) <= 55 else "medium"
        for perturbation_type in ("grayscale", "rotation", "blur", "scale", "contrast", "jpeg", "noise", "white_border"):
            matches = [item for item in enriched_predictions if item["sample_id"] == row["sample_id"]]
            for prediction in matches:
                perturbations.append({
                    "sample_id": row["sample_id"], "pubchem_cid": row["pubchem_cid"], "backend": prediction["backend"],
                    "source_layer": "official_clean", "perturbation_type": perturbation_type,
                    "severity": severity, "seed": params.get("seed"),
                    "parameters": json.dumps(params, sort_keys=True),
                    "full_inchikey_exact": prediction.get("full_inchikey_exact", prediction.get("inchikey_exact_match", "")),
                    "note": "v0.1 applies all listed operations together; per-operation causal accuracy is not identifiable.",
                })
    _write_csv(output / "perturbation_statistics.csv", perturbations, ("sample_id", "backend", "perturbation_type", "severity"))
    structure_matrix = _group_counts(
        [row for row in enriched_predictions if row.get("error_type")],
        ("backend", "image_variant", "structure_features", "error_type"), "error_count",
    )
    _write_csv(output / "structure_error_matrix.csv", structure_matrix, ("backend", "image_variant", "error_type", "error_count"))

    by_cid: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in selected_manifest: by_cid[row["pubchem_cid"]][row["image_variant"]] = row
    predictions_by_backend = {backend: {row["sample_id"]: row for row in rows} for backend, rows in prediction_rows.items()}
    selected_examples: list[dict[str, Any]] = []
    primary = predictions_by_backend.get("decimer") or predictions_by_backend.get("molscribe") or {}
    for cid, variants in sorted(by_cid.items(), key=lambda item: int(item[0])):
        official = primary.get(variants.get("official_clean", {}).get("sample_id", ""), {})
        rendered = primary.get(variants.get("rendered_clean", {}).get("sample_id", ""), {})
        perturbed = primary.get(variants.get("synthetic_perturbation", {}).get("sample_id", ""), {})
        if not _bool(official.get("full_inchikey_exact", official.get("inchikey_exact_match"))) and _bool(rendered.get("full_inchikey_exact", rendered.get("inchikey_exact_match"))):
            selected_examples.append({"pubchem_cid": cid, "selection_reason": "official_wrong_rendered_correct"})
        if len([x for x in selected_examples if x["selection_reason"] == "official_wrong_rendered_correct"]) >= 50: break
    rendered_wrong = []
    for cid, variants in sorted(by_cid.items(), key=lambda item: int(item[0])):
        rendered = primary.get(variants.get("rendered_clean", {}).get("sample_id", ""), {})
        if rendered and not _bool(rendered.get("full_inchikey_exact", rendered.get("inchikey_exact_match"))):
            rendered_wrong.append({"pubchem_cid": cid, "selection_reason": "rendered_wrong"})
    selected_examples.extend(rendered_wrong[:30])
    for backend in ("molscribe", "decimer"):
        seen_failure_cids: set[str] = set()
        backend_examples = []
        for row in failures:
            cid = str(row["pubchem_cid"])
            if row["backend"] != backend or cid in seen_failure_cids:
                continue
            seen_failure_cids.add(cid)
            backend_examples.append({"pubchem_cid": cid, "selection_reason": f"{backend}_backend_failure"})
        selected_examples.extend(backend_examples[:30])
    flip_cids: set[str] = set()
    for backend in ("molscribe", "decimer"):
        backend_rows = predictions_by_backend.get(backend, {})
        for cid, variants in sorted(by_cid.items(), key=lambda item: int(item[0])):
            clean = backend_rows.get(variants.get("official_clean", {}).get("sample_id", ""), {})
            perturbed = backend_rows.get(variants.get("synthetic_perturbation", {}).get("sample_id", ""), {})
            clean_correct = _bool(clean.get("full_inchikey_exact", clean.get("inchikey_exact_match")))
            perturbation_correct = _bool(perturbed.get("full_inchikey_exact", perturbed.get("inchikey_exact_match")))
            if clean_correct and not perturbation_correct:
                flip_cids.add(cid)
    flip_examples = [{"pubchem_cid": cid} for cid in sorted(flip_cids, key=int)]
    # Every v0.1 perturbation is a composite of all eight operations. The same
    # fixed flip examples therefore document each included operation, without
    # claiming that any one operation caused the failure.
    for perturbation_type in ("grayscale", "rotation", "blur", "scale", "contrast", "jpeg", "noise", "white_border"):
        selected_examples.extend({
            "pubchem_cid": row["pubchem_cid"],
            "selection_reason": f"{perturbation_type}_included_in_composite_flip",
        } for row in flip_examples[:10])
    _write_csv(output / "example_manifest.csv", selected_examples, ("pubchem_cid", "selection_reason"))
    _build_gallery(dataset_root, output / "gallery", by_cid, predictions_by_backend, selected_examples, stats_by_sample)
    failure_counts = Counter((row["backend"], row["failure_category"]) for row in failures)
    parse_outputs_missing = sum(
        row["failure_category"] == "output_parse_failure" and not str(row.get("raw_output") or "").strip()
        for row in failures
    )
    visual_fields = (
        "foreground_occupancy", "ink_ratio", "contrast_std", "connected_components",
        "border_whitespace_ratio", "line_thickness_approx",
    )
    variant_statistics: dict[str, dict[str, float]] = {}
    for variant in ("official_clean", "rendered_clean", "synthetic_perturbation"):
        members = [row for row in stats_rows if row["image_variant"] == variant]
        variant_statistics[variant] = {
            field: round(sum(float(row[field]) for row in members) / len(members), 6) if members else 0.0
            for field in visual_fields
        }
    report = [
        "# Trusted OCSR train/dev diagnostics", "",
        f"Splits analyzed: {', '.join(sorted(allowed_splits))}",
        f"Images analyzed: {len(selected_manifest)}", f"Backend failures: {len(failures)}", "",
        f"Prediction rows analyzed: {len(enriched_predictions)}", "",
        "## Failure taxonomy", "",
        *[f"- {backend} / {category}: {count}" for (backend, category), count in sorted(failure_counts.items())],
        f"- Legacy output-parse rows whose raw model string was not retained: {parse_outputs_missing}",
        "", "## Input-domain statistics (means)", "",
        "```json", json.dumps(variant_statistics, ensure_ascii=False, indent=2), "```",
        "", "## Perturbation provenance", "",
        "v0.1 synthetic perturbations are generated from official_clean. Rotation, blur, scaling, contrast, JPEG, grayscale, noise, and white-border changes are combined in every perturbed image; individual causal effects cannot be recovered from v0.1.",
        "", "Frozen test is excluded unless --include-frozen-test is explicitly supplied. Gallery contents are diagnostic only and cannot edit ground truth.",
    ]
    (output / "diagnostics_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {"manifest_rows": len(selected_manifest), "prediction_rows": len(enriched_predictions), "failures": len(failures), "output": str(output)}
