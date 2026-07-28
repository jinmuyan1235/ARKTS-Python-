"""Independent PubChem external holdout builder with typed perturbations."""

from __future__ import annotations

import csv
import json
import random
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rdkit import rdBase

from src.datasets.http import CachedHttpClient
from src.datasets.trusted_ocsr import (
    MANIFEST_FIELDS, PUBCHEM_BASE, PUBCHEM_LICENSE, PUBCHEM_POLICY_URL, SOURCE_FIELDS,
    TrustedDatasetBuildConfig, TrustedOCSRDatasetBuilder, _feature_balanced_order,
    _render_clean, _write_csv, deterministic_candidate_cids, sha256_file, validate_trusted_dataset,
)
from src.runtime.metadata import dependency_versions, git_commit
from src.ocsr.input_normalization import InputNormalizationConfig


V2_VERSION = "ocsr-trusted-v0.2"
PERTURBATION_TYPES = ("jpeg", "blur", "scale", "rotation", "contrast", "grayscale", "noise", "white_border")


def _typed_perturbation(image: Image.Image, kind: str, seed: int) -> tuple[Image.Image, dict[str, Any], str]:
    rng = random.Random(seed)
    source = image.convert("RGB")
    params: dict[str, Any] = {"seed": seed, "type": kind, "source_layer": "official_clean"}
    severity = "medium"
    if kind == "jpeg":
        quality = rng.randint(48, 72); params["jpeg_quality"] = quality
        buffer = BytesIO(); source.save(buffer, "JPEG", quality=quality); buffer.seek(0); result = Image.open(buffer).convert("RGB")
    elif kind == "blur":
        radius = round(rng.uniform(0.6, 1.2), 3); params["blur_radius"] = radius; result = source.filter(ImageFilter.GaussianBlur(radius))
    elif kind == "scale":
        factor = round(rng.uniform(0.65, 0.85), 3); params["scale"] = factor
        small = source.resize((max(32, int(source.width * factor)), max(32, int(source.height * factor))), Image.Resampling.LANCZOS)
        result = Image.new("RGB", source.size, "white"); result.paste(small, ((source.width-small.width)//2, (source.height-small.height)//2))
    elif kind == "rotation":
        angle = round(rng.uniform(-4.0, 4.0), 3); params["rotation_degrees"] = angle
        result = source.rotate(angle, expand=True, fillcolor="white", resample=Image.Resampling.BICUBIC)
    elif kind == "contrast":
        factor = round(rng.uniform(0.72, 0.88), 3); params["contrast"] = factor; result = ImageEnhance.Contrast(source).enhance(factor)
    elif kind == "grayscale":
        params["grayscale"] = True; result = ImageOps.grayscale(source).convert("RGB"); severity = "low"
    elif kind == "noise":
        sigma = round(rng.uniform(3.0, 7.0), 3); params["noise_sigma"] = sigma
        array = np.asarray(source).astype(np.float32)
        result = Image.fromarray(np.clip(array + np.random.default_rng(seed).normal(0, sigma, array.shape), 0, 255).astype(np.uint8))
    elif kind == "white_border":
        border = rng.randint(40, 100); params["white_border_px"] = border; result = ImageOps.expand(source, border, "white")
    else:
        raise ValueError(f"Unsupported perturbation type: {kind}")
    params["severity"] = severity
    return result, params, severity


@dataclass(frozen=True)
class ExternalHoldoutBuildConfig:
    output: Path
    cache_dir: Path
    reference_manifest: Path
    frozen_profiles: Path
    target_cids: int = 300
    minimum_success: int = 300
    candidate_pool_size: int = 1800
    seed: int = 20260719
    request_interval: float = 0.34


class TrustedOCSRExternalHoldoutBuilder:
    def __init__(self, config: ExternalHoldoutBuildConfig, client: CachedHttpClient | None = None) -> None:
        self.config = config
        base_config = TrustedDatasetBuildConfig(
            config.output, config.cache_dir, config.target_cids, config.minimum_success,
            config.candidate_pool_size, config.seed, None, config.request_interval,
        )
        self.base = TrustedOCSRDatasetBuilder(base_config, client)

    def build(self) -> dict[str, Any]:
        output = self.config.output.resolve()
        if output.exists(): raise FileExistsError(f"Refusing to overwrite external holdout: {output}")
        if not self.config.frozen_profiles.is_file():
            raise FileNotFoundError("Frozen train/dev best_profiles.json is required before v0.2 construction.")
        frozen_profiles_payload = json.loads(self.config.frozen_profiles.read_text(encoding="utf-8"))
        reference_rows = list(csv.DictReader(self.config.reference_manifest.open("r", encoding="utf-8-sig", newline="")))
        excluded_cids = {int(row["pubchem_cid"]) for row in reference_rows}
        excluded_keys = {row["ground_truth_inchikey"] for row in reference_rows}
        excluded_smiles = {row["ground_truth_canonical_smiles"] for row in reference_rows}
        candidates = [cid for cid in deterministic_candidate_cids(self.config.seed, self.config.candidate_pool_size) if cid not in excluded_cids]
        records, excluded = self.base._properties(candidates)
        unique: list[dict[str, Any]] = []
        seen_keys = set(excluded_keys); seen_smiles = set(excluded_smiles)
        for row in _feature_balanced_order(records, self.config.seed):
            if row["ground_truth_inchikey"] in seen_keys:
                excluded.append({"pubchem_cid": row["cid"], "stage": "reference_leakage", "reason": "v0.1_inchikey_overlap"})
            elif row["ground_truth_canonical_smiles"] in seen_smiles:
                excluded.append({"pubchem_cid": row["cid"], "stage": "reference_leakage", "reason": "v0.1_canonical_overlap"})
            else:
                seen_keys.add(row["ground_truth_inchikey"]); seen_smiles.add(row["ground_truth_canonical_smiles"]); unique.append(row)
        staging = output.with_name(f".{output.name}.building-{self.config.seed}")
        if staging.exists(): shutil.rmtree(staging)
        for path in (staging/"images/official_clean", staging/"images/rendered_clean", staging/"images/perturbations", staging/"metadata"):
            path.mkdir(parents=True, exist_ok=True)
        selected: list[dict[str, Any]] = []; downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in unique:
            if len(selected) >= self.config.target_cids: break
            cid = row["cid"]; image_url = f"{PUBCHEM_BASE}/{cid}/PNG?record_type=2d&image_size=1000x1000"
            try:
                payload, image_meta = self.base.client.get_bytes(image_url)
                with Image.open(BytesIO(payload)) as downloaded:
                    downloaded.load()
                    official = downloaded.copy()
                rendered = _render_clean(row["ground_truth_isomeric_smiles"])
                kind = PERTURBATION_TYPES[len(selected) % len(PERTURBATION_TYPES)]
                perturbed, params, severity = _typed_perturbation(official, kind, self.config.seed + cid)
            except Exception as exc:
                excluded.append({"pubchem_cid": cid, "stage": "image", "reason": str(exc)}); continue
            paths = {
                "official_clean": staging/f"images/official_clean/CID_{cid}.png",
                "rendered_clean": staging/f"images/rendered_clean/CID_{cid}.png",
                "synthetic_perturbation": staging/f"images/perturbations/CID_{cid}_{kind}.png",
            }
            # Preserve the official response byte-for-byte. Decoding above is
            # validation only; normalization happens in a separate layer.
            paths["official_clean"].write_bytes(payload)
            rendered.save(paths["rendered_clean"])
            perturbed.save(paths["synthetic_perturbation"])
            metadata = {**row, "cid": cid, "image_url": image_url, "image_response_sha256": image_meta["sha256"],
                        "downloaded_at": downloaded_at, "perturbation_parameters": params}
            metadata_path = staging/f"metadata/CID_{cid}.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            row.update({"paths": paths, "image_url": image_url, "image_response_sha256": image_meta["sha256"],
                        "metadata_path": metadata_path, "perturbation_parameters": params,
                        "perturbation_type": kind, "perturbation_severity": severity})
            selected.append(row)
        if len(selected) < self.config.minimum_success:
            _write_csv(staging/"excluded_samples.csv", ("pubchem_cid", "stage", "reason"), excluded)
            raise RuntimeError(f"Only {len(selected)} non-leaking external CIDs succeeded; required {self.config.minimum_success}.")
        manifest_rows: list[dict[str, Any]] = []; source_rows: list[dict[str, Any]] = []
        for row in sorted(selected, key=lambda item: item["cid"]):
            cid = row["cid"]
            for variant, path in row["paths"].items():
                params = row["perturbation_parameters"] if variant == "synthetic_perturbation" else {}
                manifest_rows.append({
                    "sample_id": f"pubchem_{cid}_{variant}", "pubchem_cid": cid, "image_path": path.relative_to(staging).as_posix(),
                    "image_variant": variant, "image_sha256": sha256_file(path), "ground_truth_smiles": row["ground_truth_smiles"],
                    "ground_truth_canonical_smiles": row["ground_truth_canonical_smiles"], "ground_truth_isomeric_smiles": row["ground_truth_isomeric_smiles"],
                    "ground_truth_inchikey": row["ground_truth_inchikey"], "ground_truth_formula": row["ground_truth_formula"],
                    "expected_action": "recognize", "source": "PubChem", "source_url": row["image_url"], "source_license": PUBCHEM_LICENSE,
                    "downloaded_at": downloaded_at, "dataset_version": V2_VERSION, "split": "external_holdout", "scaffold_key": row["scaffold_key"],
                    "structure_features": ";".join(row["structure_features"]), "perturbation": row["perturbation_type"] if params else "none",
                    "perturbation_parameters": json.dumps(params, sort_keys=True), "ground_truth_origin": "pubchem", "review_status": "source_verified",
                    "atom_count": row["atom_count"], "heavy_atom_count": row["heavy_atom_count"], "molecular_weight": row["molecular_weight"], "ring_count": row["ring_count"],
                })
            source_rows.append({"pubchem_cid": cid, "property_url": row["source_property_url"], "image_url": row["image_url"],
                "property_response_sha256": row["property_response_sha256"], "image_response_sha256": row["image_response_sha256"],
                "metadata_path": row["metadata_path"].relative_to(staging).as_posix(), "downloaded_at": downloaded_at,
                "source_license": PUBCHEM_LICENSE, "source_policy_url": PUBCHEM_POLICY_URL, "ground_truth_origin": "pubchem"})
        _write_csv(staging/"manifest.csv", MANIFEST_FIELDS, manifest_rows); _write_csv(staging/"source_manifest.csv", SOURCE_FIELDS, source_rows)
        _write_csv(staging/"excluded_samples.csv", ("pubchem_cid", "stage", "reason"), excluded)
        protocol = {"dataset_version": V2_VERSION, "dataset_role": "external_holdout", "random_seed": self.config.seed,
            "reference_dataset": "ocsr-trusted-v0.1", "reference_checksums_sha256": sha256_file(self.config.reference_manifest.parent/"checksums.sha256"),
            "frozen_profiles_sha256": sha256_file(self.config.frozen_profiles), "frozen_profiles": frozen_profiles_payload,
            "model_results_viewed_before_freeze": False, "formal_evaluation_policy": "one run per output directory after freeze",
            "perturbation_types": list(PERTURBATION_TYPES), "ground_truth_policy": "PubChem only; predictions prohibited"}
        feature_counts = Counter(feature for row in selected for feature in row["structure_features"])
        summary = {"dataset_version": V2_VERSION, "dataset_role": "external_holdout", "successful_cids": len(selected),
            "manifest_rows": len(manifest_rows), "excluded_records": len(excluded), "feature_counts": dict(sorted(feature_counts.items())),
            "perturbation_counts": dict(Counter(row["perturbation_type"] for row in selected)), "git_sha": git_commit(),
            "perturbation_severity_counts": dict(Counter(row["perturbation_severity"] for row in selected)),
            "split_cid_counts": {"external_holdout": len(selected)},
            "rdkit_version": rdBase.rdkitVersion, "dependency_versions": dependency_versions(), "created_at": downloaded_at}
        (staging/"protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging/"dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"{sha256_file(path)}  {path.relative_to(staging).as_posix()}" for path in sorted(p for p in staging.rglob("*") if p.is_file() and p.name != "checksums.sha256")]
        (staging/"checksums.sha256").write_text("\n".join(lines)+"\n", encoding="utf-8")
        validation = validate_external_holdout(staging, self.config.reference_manifest.parent)
        if not validation["valid"]:
            raise RuntimeError("External holdout staging validation failed: " + "; ".join(validation["errors"]))
        staging.replace(output)
        return summary


def validate_external_holdout(v2_root: Path, v1_root: Path) -> dict[str, Any]:
    base = validate_trusted_dataset(v2_root)
    errors = list(base["errors"])
    v2_rows = list(csv.DictReader((v2_root/"manifest.csv").open("r", encoding="utf-8-sig", newline=""))) if (v2_root/"manifest.csv").is_file() else []
    v1_rows = list(csv.DictReader((v1_root/"manifest.csv").open("r", encoding="utf-8-sig", newline=""))) if (v1_root/"manifest.csv").is_file() else []
    v2_sources = {
        row["pubchem_cid"]: row
        for row in csv.DictReader((v2_root/"source_manifest.csv").open("r", encoding="utf-8-sig", newline=""))
    } if (v2_root/"source_manifest.csv").is_file() else {}
    for field in ("pubchem_cid", "ground_truth_inchikey", "ground_truth_canonical_smiles"):
        overlap = {row[field] for row in v2_rows} & {row[field] for row in v1_rows}
        if overlap: errors.append(f"v0.1_{field}_leakage:{len(overlap)}")
    if any(row.get("split") != "external_holdout" for row in v2_rows): errors.append("non_external_holdout_split")
    perturbations = [row for row in v2_rows if row.get("image_variant") == "synthetic_perturbation"]
    if any(row.get("perturbation") not in PERTURBATION_TYPES for row in perturbations): errors.append("untyped_perturbation")
    for row in (item for item in v2_rows if item.get("image_variant") == "official_clean"):
        source = v2_sources.get(row.get("pubchem_cid", ""), {})
        if row.get("image_sha256") != source.get("image_response_sha256"):
            errors.append(f"official_response_not_byte_exact:{row.get('pubchem_cid')}")
    try:
        protocol = json.loads((v2_root/"protocol.json").read_text(encoding="utf-8"))
        summary = json.loads((v2_root/"dataset_summary.json").read_text(encoding="utf-8"))
        if protocol.get("dataset_role") != "external_holdout" or summary.get("dataset_role") != "external_holdout":
            errors.append("external_holdout_role_missing")
        if protocol.get("model_results_viewed_before_freeze") is not False:
            errors.append("pre_evaluation_freeze_declaration_missing")
        if protocol.get("reference_checksums_sha256") != sha256_file(v1_root/"checksums.sha256"):
            errors.append("reference_checksum_mismatch")
        for backend in ("molscribe", "decimer"):
            frozen = (protocol.get("frozen_profiles") or {}).get(backend) or {}
            config_payload = frozen.get("profile_config") or {}
            if not config_payload or InputNormalizationConfig(**config_payload).sha256() != frozen.get("profile_sha256"):
                errors.append(f"invalid_frozen_profile:{backend}")
    except Exception as exc:
        errors.append(f"external_protocol_invalid:{type(exc).__name__}")
    if len({row.get("pubchem_cid") for row in v2_rows}) < 300:
        errors.append("external_holdout_has_fewer_than_300_cids")
    represented_types = {row.get("perturbation") for row in perturbations}
    if represented_types != set(PERTURBATION_TYPES):
        errors.append("perturbation_type_coverage_incomplete")
    return {**base, "valid": not errors, "errors": sorted(set(errors)), "reference_overlap_checked": True}
