"""Unit tests for audit-only OCSR machine review with mocked OCSR backends."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from rdkit import Chem

from src.datasets.machine_review import MACHINE_REVIEW_FIELDS, MachineReviewProcessor
from src.datasets.pipeline import perceptual_hash


class FakeResult:
    def __init__(self, backend: str, smiles: str | None) -> None:
        self.backend = backend
        self.smiles = smiles

    def to_dict(self) -> dict[str, str | None]:
        return {"backend": self.backend, "status": "success" if self.smiles else "failed", "smiles": self.smiles}


class FakeRecognizer:
    predictions = {"molscribe": "CCO", "decimer": "CCO", "ensemble": "CCO"}

    def __init__(self, backend: str) -> None:
        self.backend = backend

    def recognize(self, image_path: Path) -> FakeResult:
        assert image_path.is_file()
        return FakeResult(self.backend, self.predictions[self.backend])


def _image(path: Path, variant: int = 0) -> None:
    image = Image.new("RGB", (220, 180), "white")
    draw = ImageDraw.Draw(image)
    if variant == 0:
        draw.line((50, 90, 170, 90), fill="black", width=5)
        draw.ellipse((92, 55, 128, 125), outline="black", width=4)
    else:
        draw.rectangle((55, 55, 165, 125), outline="black", width=5)
        draw.line((55, 55, 165, 125), fill="black", width=3)
    image.save(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quality(_: Path) -> dict[str, object]:
    return {"score": 0.95, "level": "high", "edge_ink": False}


def _similarity(_: Path, __: str, redraw_path: Path) -> float:
    redraw_path.write_bytes(b"mock redraw")
    return 0.95


def _row(sample_id: str, path: Path, *, category: str = "molecule", **overrides: str) -> dict[str, str]:
    inchikey = Chem.MolToInchiKey(Chem.MolFromSmiles("CCO"))
    row = {
        "sample_id": sample_id,
        "image_path": f"images/{path.name}",
        "image_sha256": _sha(path),
        "perceptual_hash": perceptual_hash(path),
        "category": category,
        "expected_action": "recognize" if category == "molecule" else "reject",
        "source_kind": "pubchem",
        "source_id": sample_id,
        "source_document": f"source-{sample_id}",
        "source_url": f"https://example.test/{sample_id}",
        "source_license": "Public Domain (PubChem)",
        "attribution": "PubChem test attribution",
        "reference_smiles": "CCO",
        "reference_inchikey": inchikey,
        "bbox": "[]",
        "candidate_predictions": "[]",
        "review_status": "pending",
        "notes": "test candidate",
    }
    row.update(overrides)
    return row


def _write_pending(root: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with (root / "pending_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _run(root: Path) -> tuple[dict[str, object], list[dict[str, str]]]:
    result = MachineReviewProcessor(
        root,
        output_dir=root / "review",
        recognizer_factory=FakeRecognizer,
        image_quality_fn=_quality,
        redraw_similarity_fn=_similarity,
    ).run()
    with Path(str(result["machine_review_manifest"])).open(encoding="utf-8", newline="") as handle:
        return result, list(csv.DictReader(handle))


def test_valid_candidate_becomes_machine_verified_without_mutating_pending_manifest(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "valid.png"
    _image(image)
    _write_pending(root, [_row("valid", image)])
    before = (root / "pending_manifest.csv").read_text(encoding="utf-8")

    result, rows = _run(root)

    assert rows[0]["verification_status"] == "machine_verified"
    assert rows[0]["models_agree"] == "true"
    assert rows[0]["source_formula"] == "C2H6O"
    assert rows[0]["dataset_root"] == str(root.resolve())
    assert rows[0]["ground_truth_origin"] == "pubchem"
    assert rows[0]["ground_truth_smiles"] == "CCO"
    assert (root / "pending_manifest.csv").read_text(encoding="utf-8") == before
    assert Path(str(result["review_report"])).is_file()
    assert set(rows[0]).issuperset(MACHINE_REVIEW_FIELDS)


def test_invalid_path_is_rejected_and_path_escape_is_audited(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _image(outside)
    row = _row("escape", outside, image_path="../outside.png")
    _write_pending(root, [row])

    result, rows = _run(root)

    assert rows[0]["verification_status"] == "rejected_invalid"
    assert "manifest_path_escape" in json.loads(rows[0]["deterministic_errors"])
    assert len(list(csv.DictReader(Path(str(result["rejected_manifest"])).open(encoding="utf-8")))) == 1


def test_exact_image_duplicate_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "shared.png"
    _image(image)
    _write_pending(root, [_row("first", image), _row("second", image)])

    _, rows = _run(root)

    states = {row["sample_id"]: row["verification_status"] for row in rows}
    assert states == {"first": "machine_verified", "second": "rejected_invalid"}
    assert "duplicate_image" in json.loads(next(row for row in rows if row["sample_id"] == "second")["deterministic_errors"])


def test_perceptual_neighbour_is_reviewed_instead_of_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    first, second = image_dir / "first.png", image_dir / "second.png"
    _image(first, 0)
    _image(second, 1)
    rows = [_row("first", first), _row("second", second)]
    # Exercise the collision path independently of the exact file hashes.
    rows[1]["perceptual_hash"] = rows[0]["perceptual_hash"]
    _write_pending(root, rows)

    _, reviewed = _run(root)

    second_row = next(row for row in reviewed if row["sample_id"] == "second")
    assert second_row["verification_status"] == "pending_human_review"
    assert json.loads(second_row["deterministic_errors"]) == []
    assert "near_duplicate_image" in json.loads(second_row["risk_reasons"])


def test_exact_cross_category_collision_requires_human_review(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "shared.png"
    _image(image)
    _write_pending(root, [_row("molecule", image), _row("text", image, category="text")])

    _, reviewed = _run(root)

    conflict = next(row for row in reviewed if row["sample_id"] == "text")
    assert conflict["verification_status"] == "pending_human_review"
    assert "duplicate_category_conflict" in json.loads(conflict["risk_reasons"])
    assert "duplicate_image" not in json.loads(conflict["deterministic_errors"])


def test_cross_split_leakage_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    first, second = image_dir / "first.png", image_dir / "second.png"
    _image(first, 0)
    _image(second, 1)
    rows = [
        _row("leak-a", first, source_document="shared-document", split="train"),
        _row("leak-b", second, source_document="shared-document", split="test"),
    ]
    _write_pending(root, rows)

    _, reviewed = _run(root)

    assert {row["verification_status"] for row in reviewed} == {"rejected_invalid"}
    assert all("split_leakage_source_document" in " ".join(json.loads(row["split_leakage"])) for row in reviewed)


def test_model_disagreement_routes_to_human_review(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "disagreement.png"
    _image(image)
    _write_pending(root, [_row("disagreement", image, reference_smiles="", reference_inchikey="")])
    FakeRecognizer.predictions = {"molscribe": "CCO", "decimer": "CCN", "ensemble": "CCO"}
    try:
        result, rows = _run(root)
    finally:
        FakeRecognizer.predictions = {"molscribe": "CCO", "decimer": "CCO", "ensemble": "CCO"}

    assert rows[0]["verification_status"] == "pending_human_review"
    assert "model_disagreement" in json.loads(rows[0]["risk_reasons"])
    assert len(list(csv.DictReader(Path(str(result["human_review_queue"])).open(encoding="utf-8")))) == 1


def test_negative_sample_with_valid_smiles_routes_to_human_review(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "text.png"
    _image(image)
    _write_pending(root, [_row("negative", image, category="text", reference_smiles="", reference_inchikey="")])

    _, rows = _run(root)

    assert rows[0]["verification_status"] == "pending_human_review"
    assert "negative_sample_has_valid_smiles" in json.loads(rows[0]["risk_reasons"])


def test_negative_without_prediction_has_no_redraw_similarity_risk(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    image_dir = root / "images"
    image_dir.mkdir(parents=True)
    image = image_dir / "text.png"
    _image(image)
    _write_pending(root, [_row("negative", image, category="text", reference_smiles="", reference_inchikey="")])
    FakeRecognizer.predictions = {"molscribe": "", "decimer": "", "ensemble": ""}
    try:
        _, rows = _run(root)
    finally:
        FakeRecognizer.predictions = {"molscribe": "CCO", "decimer": "CCO", "ensemble": "CCO"}

    assert "low_redraw_similarity" not in json.loads(rows[0]["risk_reasons"])
