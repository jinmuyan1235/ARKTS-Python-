# OCSR Benchmark

This folder contains the manifest format and a small demo manifest for exercising the benchmark framework. The bundled `example_manifest.csv` references local demo images already present in `data/samples`; it is not a scientific benchmark and must not be reported as real OCSR accuracy.

## Manifest Format

Required CSV columns:

```csv
sample_id,image_path,ground_truth_smiles,category,source,notes
aspirin_001,images/aspirin_001.png,CC(=O)Oc1ccccc1C(=O)O,clean_generated,local_demo,
```

Recommended columns for acceptance runs:

```csv
split,scaffold_key,source_document,image_quality,complexity,perturbation,structure_features
test,benzene_carboxylate,patent_us_xxx,scanned,medium,low_resolution,ester;acid;aromatic
```

Rules enforced by the loader:

- `sample_id`, `image_path`, `ground_truth_smiles`, `category`, and `source` must be non-empty.
- `sample_id` values must be unique.
- `image_path` must exist and remain inside `--dataset-root`.
- `ground_truth_smiles` must be parseable by RDKit.
- Rows with `expected_action=reject` may omit `ground_truth_smiles`.
- Strict real acceptance validation requires SHA-256 integrity, matching InChIKey values, reviewer/annotator metadata for verified rows, and no split leakage across a shared `source_document`.
- Invalid rows are reported explicitly; they are not silently skipped.

Recommended fields are optional for compatibility, but real acceptance reports should include them. The evaluator stratifies metrics by `source`, `image_quality`, `complexity`, `perturbation`, `structure_features`, `split`, backend and preprocessing strategy.

By default, `--dataset-root` is the project root, so paths like `data/samples/aspirin.png` work. For a separate dataset directory, pass `--dataset-root C:\path\to\dataset` and keep image paths relative to that root.

## Run

```bash
python scripts/evaluate_ocsr.py \
  --manifest benchmark/example_manifest.csv \
  --backend demo \
  --output data/outputs/benchmark
```

Windows PowerShell:

```powershell
python scripts/evaluate_ocsr.py `
  --manifest benchmark/example_manifest.csv `
  --backend demo `
  --output data/outputs/benchmark
```

Each run creates a new directory such as:

```text
data/outputs/benchmark/20260711_153000_demo/
├── config.json
├── predictions.csv
├── metrics.json
├── report.md
├── failure_cases.csv
└── charts/
```

Historical runs are never overwritten.

## Build a Local Acceptance Pack

Generate a deterministic local acceptance dataset with clean RDKit renders, common image perturbations and reject/distractor controls:

```bash
python scripts/build_ocsr_acceptance_set.py
python scripts/evaluate_ocsr.py \
  --manifest data/ocsr_acceptance/manifest.csv \
  --dataset-root data/ocsr_acceptance \
  --backend molscribe \
  --output data/outputs/benchmark
```

To ingest manually cropped and labeled real-world images, fill `benchmark/manual_labels_template.csv` and run:

```bash
python scripts/ingest_ocsr_labeled_images.py \
  --labels data/raw_ocsr_real/labels.csv \
  --image-root data/raw_ocsr_real \
  --output-root data/ocsr_manual_labeled
```

See `docs/ocsr_dataset_curation.md` for the full labeling guide.

## Real Datasets

To benchmark a real backend:

1. Prepare images from a source you are allowed to use.
2. Create a manifest with verified ground-truth SMILES.
3. Split by molecule identity, scaffold, document/source family, or vendor batch rather than random image rows.
4. Install and configure the selected backend, for example MolScribe with `MOLSCRIBE_MODEL_PATH`.
5. Run the CLI with the backend and preprocessing strategy you want to compare.
6. Compare reports by `backend`, `preprocessing_strategy`, `source`, `image_quality`, `complexity` and `perturbation`.

Minimum acceptance coverage should include clean RDKit/CDK drawings, scanned literature figures, screenshots or compressed images, low-resolution/rotated/blurred/noisy/non-white backgrounds, hand-drawn structures, stereochemistry and E/Z bonds, isotopes and charges, salts/fragments/metal complexes, long chains, macrocycles and fused rings, R-groups/abbreviations/polymers, pages containing multiple structures, and reaction/text/table distractors. Use the `category`, `image_quality`, `complexity`, `perturbation` and `structure_features` fields to label these cases explicitly.

The core report separates valid-SMILES rate from exact-match rate, and adds stereochemistry exact rate, atom-count error rate, formal-charge error rate, bond-type profile error rate, confidence calibration error and rejection coverage for distractor-like rows.

## Fixed Release Acceptance

Use `data/ocsr_real_acceptance/` for the release-only reviewed acceptance set:

```text
data/ocsr_real_acceptance/
├── images/
├── manifest.csv
├── source_manifest.csv
├── dataset_card.md
└── checksums.sha256
```

Images are ignored by Git, but they must be reproducible from fixed upstream sources. Rebuild and validate them before running release acceptance:

```bash
python scripts/download_real_acceptance_set.py
python scripts/validate_real_acceptance_set.py
```

The downloader uses fixed raw URLs and expected SHA-256 values from `source_manifest.csv`, writes temporary downloads first, verifies source and final hashes, then atomically materializes images. Existing correct images are skipped; existing mismatched images fail the run.

The release manifest must include reviewed source/license and integrity fields:

```text
dataset_version,image_sha256,source_document,source_license,annotator,reviewer,review_status,ground_truth_smiles,ground_truth_inchikey,expected_action,supported_scope
```

Run a fixed release gate:

```bash
python scripts/download_real_acceptance_set.py
python scripts/validate_real_acceptance_set.py
python scripts/run_release_acceptance.py \
  --release-version starter-v0.1 \
  --manifest data/ocsr_real_acceptance/manifest.csv \
  --dataset-root data/ocsr_real_acceptance \
  --backends molscribe,ensemble
```

This writes files such as:

```text
benchmark/releases/starter-v0.1/
├── molscribe_metrics.json
├── ensemble_metrics.json
├── errors.csv
└── report.md
```

Default project-phase gates are:

- valid SMILES rate >= 95%;
- canonical exact match rate >= 80%;
- false accept rate on reject/non-molecule samples <= 5%;
- high-risk errors are routed to review;
- P95 single-GPU latency <= 15 seconds.
- positive sample count >= 100;
- negative sample count >= 20;
- independent source document count >= 30;
- unique molecule count >= 100;
- unique scaffold count >= 50;
- verified sample rate = 100%;
- license unclear row count = 0;
- missing image count = 0;
- checksum error count = 0.

The bundled `starter-v0.1` set is a starter smoke benchmark only. It has too few independent sources, and perturbations of the same source image are not independent samples. It is not statistically meaningful, not release-qualified, and must not be used to claim real-world OCSR accuracy or tune thresholds before reporting it as an independent test set. Current backend gate failures are expected and should remain visible.

Compare a new release with the previous fixed baseline:

```bash
python scripts/compare_benchmark_runs.py \
  --current benchmark/releases/v0.2 \
  --previous benchmark/releases/starter-v0.1
```

Do not train on, tune thresholds against, or repeatedly optimize prompts/models with the release acceptance set.

Do not commit model weights, proprietary datasets, or benchmark results that cannot be reproduced from documented inputs.
