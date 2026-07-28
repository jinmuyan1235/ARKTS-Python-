# OCSR Real Acceptance Starter v1

## What is included

This starter contains **12 image rows from 2 independent source images**:

- **1 genuine patent/document crop** from the public OCMR repository. It contains `Formula II`, atom numbering, and surrounding document content. The molecule is benzene-1,2-dicarbonitrile (`N#Cc1ccccc1C#N`).
- **1 independent official MolScribe example image** with the ground-truth SMILES published in the MolScribe README. Its upstream provenance is not stated, so it is classified as an external official example rather than a real literature crop.
- Derived low-resolution, JPEG, rotated, thresholded, crop, and negative-control variants.
- Two real-document negative controls: text-only content and an incomplete molecular crop.

## Important limitation

This package fixes the repository's “zero real sample” problem, but it is **not a statistically meaningful benchmark**. It is a starter smoke benchmark with only a few independent sources. Perturbations of the same source must remain in the same evaluation group and must not be counted as independent evidence.

It must not be used to claim real-world OCSR accuracy, and it must not be used for threshold tuning before being reported as an independent test set. Current backend release gates are expected to fail until a release-qualified dataset is curated.

## Reproducibility

The images referenced by `manifest.csv` are ignored by Git and are rebuilt deterministically from fixed upstream revisions:

```bash
python scripts/download_real_acceptance_set.py
python scripts/validate_real_acceptance_set.py
```

`source_manifest.csv` records the fixed source URL, upstream revision, source SHA-256, final image SHA-256, license note, and deterministic crop/perturbation operation for every manifest row. The downloader writes local `download_metadata.json` with the materialization time and provenance.

## Verification policy

Rows in this starter are marked `verified` because:

- the OCMR patent image was visually checked against the SMILES printed by the upstream OCMR example;
- the MolScribe image uses the ground-truth SMILES shown by the upstream MolScribe README;
- negative controls were manually cropped and assigned `expected_action=reject`.

For a real release benchmark, manually review newly downloaded samples before changing their status from `pending` to `verified`.

## Leakage policy

All variants sharing a `source_document` belong to the same source group. Do not place variants of the same image in different train/dev/test splits.

## Licensing

- MolScribe repository: MIT license.
- OCMR `test.png`: publicly accessible GitHub source, but no explicit repository license was found during packaging. Treat this item as local research/evaluation material and verify reuse terms before republishing it in a public release.
- This starter smoke benchmark is not release-qualified and should remain visibly failing the default release data-sufficiency gates.
