"""Build and validate an auditable PubChem-grounded OCSR benchmark."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, Draw, Lipinski, rdMolDescriptors
from rdkit.Chem.Scaffolds import MurckoScaffold

from src.datasets.http import CachedHttpClient
from src.runtime.metadata import dependency_versions, git_commit


DATASET_VERSION = "ocsr-trusted-v0.1"
PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid"
PUBCHEM_POLICY_URL = "https://www.ncbi.nlm.nih.gov/home/about/policies/#molecular-data-usage"
PUBCHEM_LICENSE = "NCBI molecular-data usage policy; preserve PubChem contributor provenance"
PROPERTY_NAMES = "MolecularFormula,SMILES,ConnectivitySMILES,InChIKey,MolecularWeight"

MANIFEST_FIELDS = (
    "sample_id", "pubchem_cid", "image_path", "image_variant", "image_sha256",
    "ground_truth_smiles", "ground_truth_canonical_smiles", "ground_truth_isomeric_smiles",
    "ground_truth_inchikey", "ground_truth_formula", "expected_action", "source", "source_url",
    "source_license", "downloaded_at", "dataset_version", "split", "scaffold_key",
    "structure_features", "perturbation", "perturbation_parameters", "ground_truth_origin",
    "review_status", "atom_count", "heavy_atom_count", "molecular_weight", "ring_count",
)

SOURCE_FIELDS = (
    "pubchem_cid", "property_url", "image_url", "property_response_sha256", "image_response_sha256",
    "metadata_path", "downloaded_at", "source_license", "source_policy_url", "ground_truth_origin",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _canonical(mol: Chem.Mol, *, isomeric: bool) -> str:
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=isomeric)


def _formula(mol: Chem.Mol) -> str:
    return str(rdMolDescriptors.CalcMolFormula(mol))


def _scaffold_key(mol: Chem.Mol, inchikey: str) -> str:
    try:
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False)
    except Exception:
        scaffold = ""
    return scaffold or f"inchikey:{inchikey}"


def structure_features(mol: Chem.Mol) -> list[str]:
    """Return deterministic, overlapping structural strata."""
    features: set[str] = set()
    weight = float(Descriptors.MolWt(mol))
    rings = int(Lipinski.RingCount(mol))
    aromatic_rings = int(Lipinski.NumAromaticRings(mol))
    fragments = len(Chem.GetMolFrags(mol))
    elements = {atom.GetSymbol() for atom in mol.GetAtoms()}
    if mol.GetNumHeavyAtoms() <= 12 and weight < 200:
        features.add("small_molecule")
    if weight >= 500:
        features.add("large_molecular_weight")
    if rings <= 1 and weight < 350 and fragments == 1:
        features.add("simple_organic")
    if rings >= 3:
        features.add("high_ring_count")
    if aromatic_rings >= 2:
        features.add("polycyclic_or_fused")
    if Chem.FindMolChiralCenters(mol, includeUnassigned=False):
        features.add("stereochemical")
    if Chem.GetFormalCharge(mol) != 0:
        features.add("formal_charge")
    if fragments > 1:
        features.add("salt_or_multifragment")
    if elements.intersection({"F", "Cl", "Br", "I"}):
        features.add("halogen")
    for element in ("N", "O", "S", "P"):
        if element in elements:
            features.add(f"contains_{element}")
    if sum(atom.GetAtomicNum() == 6 and not atom.GetIsAromatic() for atom in mol.GetAtoms()) >= 10 and rings <= 1:
        features.add("long_chain")
    return sorted(features or {"other"})


def _parse_property_row(row: dict[str, Any], response_sha: str, property_url: str) -> dict[str, Any]:
    cid = int(row["CID"])
    isomeric = str(row.get("SMILES") or row.get("IsomericSMILES") or "").strip()
    connectivity = str(row.get("ConnectivitySMILES") or row.get("CanonicalSMILES") or "").strip()
    mol = Chem.MolFromSmiles(isomeric or connectivity)
    if mol is None:
        raise ValueError("invalid_pubchem_smiles")
    computed_inchikey = Chem.MolToInchiKey(mol)
    source_inchikey = str(row.get("InChIKey") or "").strip()
    if not source_inchikey or source_inchikey != computed_inchikey:
        raise ValueError("inchikey_mismatch")
    source_formula = str(row.get("MolecularFormula") or "").strip()
    computed_formula = _formula(mol)
    if source_formula != computed_formula:
        raise ValueError(f"formula_mismatch:{source_formula}!={computed_formula}")
    canonical = _canonical(mol, isomeric=False)
    canonical_isomeric = _canonical(mol, isomeric=True)
    return {
        "cid": cid,
        "source_row": row,
        "source_property_url": property_url,
        "property_response_sha256": response_sha,
        "ground_truth_smiles": isomeric or connectivity,
        "ground_truth_canonical_smiles": canonical,
        "ground_truth_isomeric_smiles": canonical_isomeric,
        "ground_truth_inchikey": source_inchikey,
        "ground_truth_formula": source_formula,
        "scaffold_key": _scaffold_key(mol, source_inchikey),
        "structure_features": structure_features(mol),
        "atom_count": int(mol.GetNumAtoms()),
        "heavy_atom_count": int(mol.GetNumHeavyAtoms()),
        "molecular_weight": round(float(Descriptors.MolWt(mol)), 6),
        "ring_count": int(Lipinski.RingCount(mol)),
    }


def deterministic_candidate_cids(seed: int, count: int) -> list[int]:
    """Generate a reproducible, range-diverse candidate pool without predictions."""
    rng = random.Random(seed)
    anchors = list(range(1, min(2501, count // 3 + 1)))
    ranges = ((2_501, 1_000_000), (1_000_001, 20_000_000), (20_000_001, 170_000_000))
    result = list(anchors)
    seen = set(result)
    while len(result) < count:
        low, high = ranges[(len(result) - len(anchors)) % len(ranges)]
        cid = rng.randint(low, high)
        if cid not in seen:
            seen.add(cid)
            result.append(cid)
    return result


def _feature_balanced_order(records: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    shuffled = list(records)
    rng.shuffle(shuffled)
    frequency = Counter(feature for row in shuffled for feature in row["structure_features"])
    return sorted(
        shuffled,
        key=lambda row: (
            sum(1.0 / max(1, frequency[f]) for f in row["structure_features"]),
            len(row["structure_features"]),
            -row["cid"],
        ),
        reverse=True,
    )


def assign_grouped_splits(records: list[dict[str, Any]], seed: int) -> dict[int, str]:
    """Assign whole scaffold groups to 70/15/15 splits."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[row["scaffold_key"]].append(row)
    items = list(groups.items())
    random.Random(seed).shuffle(items)
    items.sort(key=lambda item: len(item[1]), reverse=True)
    targets = {"train": 0.70 * len(records), "dev": 0.15 * len(records), "test": 0.15 * len(records)}
    counts = Counter()
    assignments: dict[int, str] = {}
    for _key, members in items:
        split = min(targets, key=lambda name: counts[name] / max(targets[name], 1.0))
        for member in members:
            assignments[int(member["cid"])] = split
        counts[split] += len(members)
    return assignments


def _render_clean(smiles: str, size: int = 1000) -> Image.Image:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("render_invalid_smiles")
    return Draw.MolToImage(mol, size=(size, size), kekulize=True).convert("RGB")


def _perturb(image: Image.Image, seed: int) -> tuple[Image.Image, dict[str, Any]]:
    rng = random.Random(seed)
    angle = round(rng.uniform(-3.0, 3.0), 3)
    blur = round(rng.uniform(0.25, 1.0), 3)
    contrast = round(rng.uniform(0.85, 1.15), 3)
    scale = round(rng.uniform(0.82, 1.0), 3)
    jpeg_quality = rng.randint(45, 82)
    noise_sigma = round(rng.uniform(1.0, 5.0), 3)
    margin = rng.randint(20, 90)
    work = ImageOps.grayscale(image).convert("RGB")
    work = ImageEnhance.Contrast(work).enhance(contrast)
    work = work.filter(ImageFilter.GaussianBlur(blur))
    side = max(64, int(min(work.size) * scale))
    work = ImageOps.contain(work, (side, side), Image.Resampling.LANCZOS)
    work = work.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")
    canvas_side = max(work.width, work.height) + 2 * margin
    canvas = Image.new("RGB", (canvas_side, canvas_side), "white")
    canvas.paste(work, ((canvas_side - work.width) // 2, (canvas_side - work.height) // 2))
    array = np.asarray(canvas).astype(np.float32)
    noise_rng = np.random.default_rng(seed)
    array = np.clip(array + noise_rng.normal(0, noise_sigma, array.shape), 0, 255).astype(np.uint8)
    buffer = BytesIO()
    Image.fromarray(array).save(buffer, "JPEG", quality=jpeg_quality, optimize=True)
    buffer.seek(0)
    params = {
        "seed": seed, "rotation_degrees": angle, "blur_radius": blur, "contrast": contrast,
        "scale": scale, "jpeg_quality": jpeg_quality, "noise_sigma": noise_sigma,
        "white_margin_px": margin, "grayscale": True,
    }
    return Image.open(buffer).convert("RGB"), params


@dataclass(frozen=True)
class TrustedDatasetBuildConfig:
    output: Path
    cache_dir: Path
    target_cids: int = 1000
    minimum_success: int = 800
    candidate_pool_size: int = 3000
    seed: int = 20260718
    cid_file: Path | None = None
    request_interval: float = 0.34


class TrustedOCSRDatasetBuilder:
    def __init__(self, config: TrustedDatasetBuildConfig, client: CachedHttpClient | None = None) -> None:
        self.config = config
        self.client = client or CachedHttpClient(config.cache_dir, request_interval=config.request_interval, retries=4, timeout=60)

    def _candidate_cids(self) -> list[int]:
        if self.config.cid_file:
            values = []
            for token in self.config.cid_file.read_text(encoding="utf-8-sig").replace(",", " ").split():
                values.append(int(token))
            return list(dict.fromkeys(values))
        return deterministic_candidate_cids(self.config.seed, self.config.candidate_pool_size)

    def _properties(self, cids: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        accepted: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for offset in range(0, len(cids), 75):
            batch = cids[offset:offset + 75]
            url = f"{PUBCHEM_BASE}/{','.join(map(str, batch))}/property/{PROPERTY_NAMES}/JSON"
            try:
                payload, metadata = self.client.get_bytes(url)
                data = json.loads(payload.decode("utf-8"))
                returned = {int(row["CID"]): row for row in (data.get("PropertyTable") or {}).get("Properties", [])}
                for cid in batch:
                    if cid not in returned:
                        excluded.append({"pubchem_cid": cid, "stage": "properties", "reason": "cid_not_returned"})
                        continue
                    try:
                        accepted.append(_parse_property_row(returned[cid], metadata["sha256"], url))
                    except Exception as exc:
                        excluded.append({"pubchem_cid": cid, "stage": "properties", "reason": str(exc)})
            except Exception as exc:
                for cid in batch:
                    excluded.append({"pubchem_cid": cid, "stage": "properties", "reason": f"batch_request_failed:{exc}"})
        return accepted, excluded

    def build(self) -> dict[str, Any]:
        output = self.config.output.resolve()
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite frozen dataset directory: {output}")
        staging = output.with_name(f".{output.name}.building-{self.config.seed}")
        if staging.exists():
            shutil.rmtree(staging)
        for path in (staging / "images/official_clean", staging / "images/rendered_clean", staging / "images/perturbations", staging / "metadata"):
            path.mkdir(parents=True, exist_ok=True)
        records, excluded = self._properties(self._candidate_cids())
        unique: list[dict[str, Any]] = []
        seen_inchikey: set[str] = set()
        seen_canonical: set[str] = set()
        for row in _feature_balanced_order(records, self.config.seed):
            if row["ground_truth_inchikey"] in seen_inchikey:
                excluded.append({"pubchem_cid": row["cid"], "stage": "deduplication", "reason": "duplicate_inchikey"})
            elif row["ground_truth_canonical_smiles"] in seen_canonical:
                excluded.append({"pubchem_cid": row["cid"], "stage": "deduplication", "reason": "duplicate_canonical_smiles"})
            else:
                seen_inchikey.add(row["ground_truth_inchikey"])
                seen_canonical.add(row["ground_truth_canonical_smiles"])
                unique.append(row)
        selected: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        downloaded_at = _utc_now()
        for row in unique:
            if len(selected) >= self.config.target_cids:
                break
            cid = row["cid"]
            image_url = f"{PUBCHEM_BASE}/{cid}/PNG?record_type=2d&image_size=1000x1000"
            try:
                payload, image_metadata = self.client.get_bytes(image_url)
                official = Image.open(BytesIO(payload)).convert("RGB")
                official.load()
                rendered = _render_clean(row["ground_truth_isomeric_smiles"])
                perturbed, perturbation_params = _perturb(official, self.config.seed + cid)
            except Exception as exc:
                excluded.append({"pubchem_cid": cid, "stage": "image", "reason": str(exc)})
                continue
            paths = {
                "official_clean": staging / f"images/official_clean/CID_{cid}.png",
                "rendered_clean": staging / f"images/rendered_clean/CID_{cid}.png",
                "synthetic_perturbation": staging / f"images/perturbations/CID_{cid}.jpg",
            }
            official.save(paths["official_clean"], "PNG")
            rendered.save(paths["rendered_clean"], "PNG")
            perturbed.save(paths["synthetic_perturbation"], "JPEG", quality=95)
            metadata_payload = {
                **row,
                "source_row": row["source_row"],
                "image_url": image_url,
                "image_response_sha256": image_metadata["sha256"],
                "downloaded_at": downloaded_at,
                "source_license": PUBCHEM_LICENSE,
                "source_policy_url": PUBCHEM_POLICY_URL,
                "perturbation_parameters": perturbation_params,
            }
            metadata_path = staging / f"metadata/CID_{cid}.json"
            metadata_path.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            row["paths"] = paths
            row["image_url"] = image_url
            row["image_response_sha256"] = image_metadata["sha256"]
            row["metadata_path"] = metadata_path
            row["perturbation_parameters"] = perturbation_params
            selected.append(row)
        if len(selected) < self.config.minimum_success:
            _write_csv(staging / "excluded_samples.csv", ("pubchem_cid", "stage", "reason"), excluded)
            raise RuntimeError(
                f"Only {len(selected)} trusted CIDs succeeded; minimum is {self.config.minimum_success}. "
                f"Staging diagnostics retained at {staging}."
            )
        assignments = assign_grouped_splits(selected, self.config.seed)
        manifest_rows: list[dict[str, Any]] = []
        for row in sorted(selected, key=lambda item: item["cid"]):
            cid = row["cid"]
            for variant, path in row["paths"].items():
                params = row["perturbation_parameters"] if variant == "synthetic_perturbation" else {}
                manifest_rows.append({
                    "sample_id": f"pubchem_{cid}_{variant}", "pubchem_cid": cid,
                    "image_path": path.relative_to(staging).as_posix(), "image_variant": variant,
                    "image_sha256": sha256_file(path), "ground_truth_smiles": row["ground_truth_smiles"],
                    "ground_truth_canonical_smiles": row["ground_truth_canonical_smiles"],
                    "ground_truth_isomeric_smiles": row["ground_truth_isomeric_smiles"],
                    "ground_truth_inchikey": row["ground_truth_inchikey"],
                    "ground_truth_formula": row["ground_truth_formula"], "expected_action": "recognize",
                    "source": "PubChem", "source_url": row["image_url"], "source_license": PUBCHEM_LICENSE,
                    "downloaded_at": downloaded_at, "dataset_version": DATASET_VERSION,
                    "split": assignments[cid], "scaffold_key": row["scaffold_key"],
                    "structure_features": ";".join(row["structure_features"]),
                    "perturbation": "deterministic_composite" if params else "none",
                    "perturbation_parameters": json.dumps(params, sort_keys=True),
                    "ground_truth_origin": "pubchem", "review_status": "source_verified",
                    "atom_count": row["atom_count"], "heavy_atom_count": row["heavy_atom_count"],
                    "molecular_weight": row["molecular_weight"], "ring_count": row["ring_count"],
                })
            source_rows.append({
                "pubchem_cid": cid, "property_url": row["source_property_url"], "image_url": row["image_url"],
                "property_response_sha256": row["property_response_sha256"],
                "image_response_sha256": row["image_response_sha256"],
                "metadata_path": row["metadata_path"].relative_to(staging).as_posix(),
                "downloaded_at": downloaded_at, "source_license": PUBCHEM_LICENSE,
                "source_policy_url": PUBCHEM_POLICY_URL, "ground_truth_origin": "pubchem",
            })
        _write_csv(staging / "manifest.csv", MANIFEST_FIELDS, manifest_rows)
        _write_csv(staging / "source_manifest.csv", SOURCE_FIELDS, source_rows)
        _write_csv(staging / "excluded_samples.csv", ("pubchem_cid", "stage", "reason"), excluded)
        feature_counts = Counter(feature for row in selected for feature in row["structure_features"])
        split_cids = Counter(assignments.values())
        protocol = {
            "dataset_version": DATASET_VERSION, "random_seed": self.config.seed,
            "target_cids": self.config.target_cids, "minimum_success": self.config.minimum_success,
            "candidate_pool_size": len(self._candidate_cids()), "split_policy": "scaffold grouped 70/15/15",
            "test_usage": "evaluation only; forbidden for threshold or ensemble tuning",
            "ground_truth_policy": "PubChem source records only; model predictions are never ground truth",
            "image_variants": ["official_clean", "rendered_clean", "synthetic_perturbation"],
            "pubchem_policy_url": PUBCHEM_POLICY_URL,
        }
        summary = {
            "dataset_version": DATASET_VERSION, "dataset_role": "trusted_ocsr_benchmark",
            "successful_cids": len(selected), "manifest_rows": len(manifest_rows),
            "excluded_records": len(excluded), "split_cid_counts": dict(split_cids),
            "feature_counts": dict(sorted(feature_counts.items())), "git_sha": git_commit(),
            "rdkit_version": rdBase.rdkitVersion, "dependency_versions": dependency_versions(),
            "random_seed": self.config.seed, "created_at": downloaded_at,
            "limitations": [
                "PubChem and RDKit clean depictions are not real paper crops.",
                "Synthetic perturbations are not equivalent to real scan noise.",
                "This benchmark cannot directly estimate accuracy on PMC paper figures.",
            ],
        }
        (staging / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        checksum_lines = []
        for path in sorted(p for p in staging.rglob("*") if p.is_file() and p.name != "checksums.sha256"):
            checksum_lines.append(f"{sha256_file(path)}  {path.relative_to(staging).as_posix()}")
        (staging / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
        staging.replace(output)
        return summary


def validate_trusted_dataset(dataset_root: Path) -> dict[str, Any]:
    """Validate source identity, chemistry, variants, leakage and frozen hashes."""
    root = dataset_root.resolve()
    errors: list[str] = []
    manifest_path = root / "manifest.csv"
    source_path = root / "source_manifest.csv"
    checksum_path = root / "checksums.sha256"
    for required in (manifest_path, source_path, checksum_path, root / "dataset_summary.json", root / "protocol.json"):
        if not required.is_file():
            errors.append(f"missing_required_file:{required.name}")
    if errors:
        return {"valid": False, "errors": errors, "checked_rows": 0}
    rows = list(csv.DictReader(manifest_path.open("r", encoding="utf-8-sig", newline="")))
    sources = {row["pubchem_cid"]: row for row in csv.DictReader(source_path.open("r", encoding="utf-8-sig", newline=""))}
    expected_checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split(None, 1)
            expected_checksums[rel.strip()] = digest
    for rel, expected in expected_checksums.items():
        path = root / rel
        if not path.is_file():
            errors.append(f"checksum_missing:{rel}")
        elif sha256_file(path) != expected:
            errors.append(f"checksum_mismatch:{rel}")
    by_cid: dict[str, list[dict[str, str]]] = defaultdict(list)
    inchikey_splits: dict[str, set[str]] = defaultdict(set)
    canonical_splits: dict[str, set[str]] = defaultdict(set)
    scaffold_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        cid = row.get("pubchem_cid", "")
        by_cid[cid].append(row)
        path = (root / row.get("image_path", "")).resolve()
        if root not in path.parents or not path.is_file():
            errors.append(f"image_missing_or_escaped:{row.get('sample_id')}")
        else:
            try:
                with Image.open(path) as image:
                    image.verify()
            except Exception:
                errors.append(f"image_decode_failed:{row.get('sample_id')}")
            if sha256_file(path) != row.get("image_sha256"):
                errors.append(f"image_hash_mismatch:{row.get('sample_id')}")
        if row.get("ground_truth_origin") != "pubchem" or row.get("review_status") != "source_verified":
            errors.append(f"untrusted_ground_truth:{row.get('sample_id')}")
        mol = Chem.MolFromSmiles(row.get("ground_truth_isomeric_smiles", ""))
        if mol is None:
            errors.append(f"invalid_smiles:{row.get('sample_id')}")
        else:
            if Chem.MolToInchiKey(mol) != row.get("ground_truth_inchikey"):
                errors.append(f"inchikey_mismatch:{row.get('sample_id')}")
            if _canonical(mol, isomeric=False) != row.get("ground_truth_canonical_smiles"):
                errors.append(f"canonicalization_mismatch:{row.get('sample_id')}")
            if _formula(mol) != row.get("ground_truth_formula"):
                errors.append(f"formula_mismatch:{row.get('sample_id')}")
        split = row.get("split", "")
        inchikey_splits[row.get("ground_truth_inchikey", "")].add(split)
        canonical_splits[row.get("ground_truth_canonical_smiles", "")].add(split)
        scaffold_splits[row.get("scaffold_key", "")].add(split)
        if cid not in sources:
            errors.append(f"missing_source_record:{cid}")
        else:
            source = sources[cid]
            property_url = source.get("property_url", "")
            try:
                property_cids = property_url.split("/cid/", 1)[1].split("/", 1)[0].split(",")
            except Exception:
                property_cids = []
            if cid not in property_cids or f"/cid/{cid}/" not in source.get("image_url", ""):
                errors.append(f"source_cid_url_mismatch:{cid}")
            if source.get("ground_truth_origin") != "pubchem" or not source.get("source_license"):
                errors.append(f"source_provenance_missing:{cid}")
            metadata_path = (root / source.get("metadata_path", "")).resolve()
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if int(metadata.get("cid", -1)) != int(cid):
                    errors.append(f"metadata_cid_mismatch:{cid}")
                if metadata.get("property_response_sha256") != source.get("property_response_sha256"):
                    errors.append(f"property_response_hash_mismatch:{cid}")
                if metadata.get("image_response_sha256") != source.get("image_response_sha256"):
                    errors.append(f"image_response_hash_mismatch:{cid}")
            except Exception:
                errors.append(f"source_metadata_invalid:{cid}")
        if row.get("image_variant") == "synthetic_perturbation":
            try:
                params = json.loads(row.get("perturbation_parameters") or "{}")
                if int(params["seed"]) <= 0:
                    raise ValueError
            except Exception:
                errors.append(f"untraceable_perturbation:{row.get('sample_id')}")
    for cid, variants in by_cid.items():
        if {row["image_variant"] for row in variants} != {"official_clean", "rendered_clean", "synthetic_perturbation"}:
            errors.append(f"variant_set_mismatch:{cid}")
        if len({row["split"] for row in variants}) != 1:
            errors.append(f"cid_split_leakage:{cid}")
        identities = {(row["ground_truth_inchikey"], row["ground_truth_canonical_smiles"]) for row in variants}
        if len(identities) != 1:
            errors.append(f"cid_identity_inconsistent:{cid}")
    for name, mapping in (("inchikey", inchikey_splits), ("canonical", canonical_splits), ("scaffold", scaffold_splits)):
        for key, splits in mapping.items():
            if key and len(splits) > 1:
                errors.append(f"{name}_split_leakage:{key}")
    unique_inchikey = {next(iter(rows_for_cid))["ground_truth_inchikey"] for rows_for_cid in by_cid.values()}
    unique_canonical = {next(iter(rows_for_cid))["ground_truth_canonical_smiles"] for rows_for_cid in by_cid.values()}
    if len(unique_inchikey) != len(by_cid):
        errors.append("duplicate_inchikey_across_cids")
    if len(unique_canonical) != len(by_cid):
        errors.append("duplicate_canonical_across_cids")
    return {
        "valid": not errors, "errors": sorted(set(errors)), "checked_rows": len(rows),
        "unique_cids": len(by_cid), "checksums_sha256": sha256_file(checksum_path),
    }
