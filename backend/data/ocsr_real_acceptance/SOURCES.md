# Sources

## Real patent/document sample

- Project: OCMR
- Fixed source image: `https://raw.githubusercontent.com/zhangruochi/OCMR/5d2a6d691b360ec1dae78c2a117e0a27d93ec60d/test.png`
- Upstream version: `5d2a6d691b360ec1dae78c2a117e0a27d93ec60d`
- Source SHA-256: `0b862ebf7b0b8533add56bdf21d2138c053feb916697e159b09f394a17c3614d`
- Upstream README output: `N#Cc1ccccc1C#N`
- The image visibly contains `Formula II`, atom numbering, and two nitrile groups on adjacent benzene positions.
- License note: no explicit repository license was found; use locally for research/evaluation unless reuse terms are independently confirmed.

## External official OCSR example

- Project: MolScribe
- Fixed source image: `https://raw.githubusercontent.com/thomas0809/MolScribe/7296a30413eb55436702011efdff78131f66d162/assets/example.png`
- Upstream version: `7296a30413eb55436702011efdff78131f66d162`
- Source SHA-256: `d9ef557d8bb00b39dc52127046966953a90b1a95c75e48302d819bd210d0f458`
- Ground truth shown in README: `Fc1ccc(-c2cc(-c3ccccc3)n(-c3ccccc3)c2)cc1`
- Repository license: MIT.

## Deterministic starter downloader

`data/ocsr_real_acceptance/source_manifest.csv` maps every `manifest.csv` row to a fixed source URL, source SHA-256, final image SHA-256, source license note, and deterministic operation. Rebuild the ignored images with:

```bash
python scripts/download_real_acceptance_set.py
python scripts/validate_real_acceptance_set.py
```

## Candidate full benchmark sources

- Images: `Kohulan/OCSR_Review`, archives for CLEF, JPO, UOB, USPTO.
- SMILES labels: `hustvl/MolSight/data/real/*.csv`.
- The OCSR Review README describes 961 CLEF images, 450 JPO images, 5,740 UOB images, and 5,719 USPTO images.
