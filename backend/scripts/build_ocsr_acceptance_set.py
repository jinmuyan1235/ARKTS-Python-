"""Build a local OCSR acceptance dataset from labeled SMILES seeds.

The generated images are useful for smoke and stress testing, not a substitute
for separately curated literature/patent screenshots.
"""

from __future__ import annotations

import argparse
import csv
from io import BytesIO
from pathlib import Path
import random
import sys
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from rdkit import Chem
from rdkit.Chem import Draw, rdDepictor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.file_utils import ensure_directory, safe_stem


MANIFEST_FIELDS = [
    "sample_id",
    "image_path",
    "ground_truth_smiles",
    "expected_action",
    "category",
    "source",
    "split",
    "scaffold_key",
    "source_document",
    "image_quality",
    "complexity",
    "perturbation",
    "structure_features",
    "notes",
]


def _canvas(image: Image.Image, size: tuple[int, int] = (512, 512), background: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    fitted = ImageOps.contain(image.convert("RGB"), size)
    output = Image.new("RGB", size, background)
    output.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return output


def _draw_molecule(smiles: str, size: tuple[int, int]) -> Image.Image:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid seed SMILES: {smiles}")
    rdDepictor.Compute2DCoords(mol)
    return Draw.MolToImage(mol, size=size, kekulize=True).convert("RGB")


def _low_res(image: Image.Image) -> Image.Image:
    small = image.resize((128, 128), Image.Resampling.BILINEAR)
    return small.resize(image.size, Image.Resampling.BILINEAR)


def _rotated(image: Image.Image) -> Image.Image:
    rotated = image.rotate(8, expand=True, fillcolor=(255, 255, 255), resample=Image.Resampling.BICUBIC)
    return _canvas(rotated, image.size)


def _blurred_noise(image: Image.Image, rng: random.Random) -> Image.Image:
    blurred = image.filter(ImageFilter.GaussianBlur(radius=1.1))
    array = np.asarray(blurred).astype(np.int16)
    noise_rng = np.random.default_rng(rng.randint(0, 2**32 - 1))
    noisy = np.clip(array + noise_rng.normal(0, 12, array.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, "RGB")


def _jpeg_compressed(image: Image.Image) -> Image.Image:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=28, optimize=True)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def _nonwhite_background(image: Image.Image) -> Image.Image:
    array = np.asarray(image.convert("RGB")).copy()
    white = np.all(array > 245, axis=-1)
    array[white] = np.array([239, 243, 232], dtype=np.uint8)
    return Image.fromarray(array, "RGB")


VARIANTS: dict[str, tuple[str, str, Callable[[Image.Image, random.Random], Image.Image]]] = {
    "clean": ("clean", "none", lambda image, _rng: image),
    "low_res": ("low_resolution", "low_resolution", lambda image, _rng: _low_res(image)),
    "rotated": ("rotated", "rotation", lambda image, _rng: _rotated(image)),
    "blurred_noise": ("noisy_blurry", "blur_noise", _blurred_noise),
    "jpeg": ("compressed", "jpeg_compression", lambda image, _rng: _jpeg_compressed(image)),
    "nonwhite_bg": ("non_white_background", "background_tint", lambda image, _rng: _nonwhite_background(image)),
}


def _read_seed(seed_path: Path) -> list[dict[str, str]]:
    with seed_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _generated_row(
    seed: dict[str, str],
    image_path: Path,
    output_root: Path,
    variant: str,
    image_quality: str,
    perturbation: str,
) -> dict[str, Any]:
    canonical = Chem.MolToSmiles(Chem.MolFromSmiles(seed["smiles"]), canonical=True, isomericSmiles=True)
    sample_id = f"{safe_stem(seed['sample_id'])}_{variant}"
    notes = seed.get("notes", "")
    return {
        "sample_id": sample_id,
        "image_path": _relative(image_path, output_root),
        "ground_truth_smiles": canonical,
        "expected_action": "recognize",
        "category": seed.get("category") or "clean_generated",
        "source": seed.get("source") or "rdkit_seed",
        "split": seed.get("split") or "test",
        "scaffold_key": seed.get("scaffold_key") or seed["sample_id"],
        "source_document": seed.get("source_document") or "generated_seed",
        "image_quality": image_quality,
        "complexity": seed.get("complexity") or "unspecified",
        "perturbation": perturbation,
        "structure_features": seed.get("structure_features") or "unspecified",
        "notes": f"{notes}; generated variant={variant}".strip("; "),
    }


def _make_distractor_image(kind: str, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    if kind == "reaction":
        draw.text((44, 210), "A + B", fill=(20, 20, 20))
        draw.line((180, 220, 330, 220), fill=(20, 20, 20), width=4)
        draw.polygon([(330, 220), (308, 208), (308, 232)], fill=(20, 20, 20))
        draw.text((360, 210), "C", fill=(20, 20, 20))
        draw.text((205, 185), "cat.", fill=(20, 20, 20))
    elif kind == "table":
        for x in range(70, 450, 95):
            draw.line((x, 110, x, 380), fill=(60, 60, 60), width=2)
        for y in range(110, 390, 54):
            draw.line((70, y, 450, y), fill=(60, 60, 60), width=2)
        draw.text((92, 132), "MW", fill=(20, 20, 20))
        draw.text((190, 132), "LogP", fill=(20, 20, 20))
        draw.text((294, 132), "TPSA", fill=(20, 20, 20))
        draw.text((104, 196), "180.2", fill=(20, 20, 20))
        draw.text((206, 196), "1.2", fill=(20, 20, 20))
    else:
        draw.text((80, 140), "Melting point: 135 C", fill=(20, 20, 20))
        draw.text((80, 190), "Yield: 72%", fill=(20, 20, 20))
        draw.text((80, 240), "No molecule in this region", fill=(20, 20, 20))
    return image


def _distractor_rows(output_root: Path, image_dir: Path, size: tuple[int, int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind in ("text", "reaction", "table"):
        filename = f"distractor_{kind}.png"
        path = image_dir / filename
        _make_distractor_image(kind, size).save(path)
        rows.append({
            "sample_id": f"distractor_{kind}",
            "image_path": _relative(path, output_root),
            "ground_truth_smiles": "",
            "expected_action": "reject",
            "category": f"{kind}_distractor",
            "source": "local_generated_negative",
            "split": "test",
            "scaffold_key": "negative_control",
            "source_document": "generated_negative_controls",
            "image_quality": "clean",
            "complexity": "none",
            "perturbation": "none",
            "structure_features": "non_molecule",
            "notes": "Negative control: the backend should reject or fail this region.",
        })
    return rows


def build_acceptance_set(
    seed_path: Path,
    output_root: Path,
    variants: list[str],
    size: tuple[int, int],
    include_distractors: bool,
    random_seed: int,
) -> Path:
    output_root = ensure_directory(output_root)
    image_dir = ensure_directory(output_root / "images")
    rng = random.Random(random_seed)
    rows: list[dict[str, Any]] = []
    for seed in _read_seed(seed_path):
        if not seed.get("sample_id") or not seed.get("smiles"):
            raise ValueError(f"Seed row must include sample_id and smiles: {seed}")
        base = _draw_molecule(seed["smiles"], size)
        for variant in variants:
            if variant not in VARIANTS:
                raise ValueError(f"Unsupported variant: {variant}. Choices: {', '.join(VARIANTS)}")
            image_quality, perturbation, transform = VARIANTS[variant]
            image = transform(base.copy(), rng)
            filename = f"{safe_stem(seed['sample_id'])}_{variant}.png"
            image_path = image_dir / filename
            image.save(image_path)
            rows.append(_generated_row(seed, image_path, output_root, variant, image_quality, perturbation))
    if include_distractors:
        rows.extend(_distractor_rows(output_root, image_dir, size))
    manifest = output_root / "manifest.csv"
    _write_manifest(manifest, rows)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", default=str(PROJECT_ROOT / "benchmark" / "acceptance_seed.csv"))
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "data" / "ocsr_acceptance"))
    parser.add_argument("--variants", default="clean,low_res,rotated,blurred_noise,jpeg,nonwhite_bg")
    parser.add_argument("--image-size", type=int, default=512)
    parser.add_argument("--no-distractors", action="store_true")
    parser.add_argument("--random-seed", type=int, default=20260715)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    manifest = build_acceptance_set(
        seed_path=Path(args.seed).expanduser().resolve(),
        output_root=Path(args.output_root).expanduser().resolve(),
        variants=variants,
        size=(args.image_size, args.image_size),
        include_distractors=not args.no_distractors,
        random_seed=args.random_seed,
    )
    print(f"Wrote OCSR acceptance manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
