# OCSR Starter Acceptance Report starter-v0.1

This report is generated from a fixed, reviewed acceptance manifest. Starter smoke benchmarks are not evidence of real-world OCSR accuracy.

## Gate Summary

| Backend | Gates | Valid SMILES | Canonical exact | False accept | Negative hallucination | High-risk review | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| molscribe | FAIL | 0.9 | 0.9 | 0 | 0.5 | 1 | 9167.34 | 9308.17 |
| decimer | FAIL | 1 | 0.5 | 0 | 1 | 1 | 32108.3 | 33302.5 |
| ensemble | FAIL | 0.5 | 0.5 | 0 | 0.5 | 1 | 40972.9 | 41120.1 |

## Dataset Sufficiency

| Metric | Value |
| --- | --- |
| Total rows | 12 |
| Positive samples | 10 |
| Negative samples | 2 |
| Independent source documents | 2 |
| Independent original images | 2 |
| Derived perturbations | 10 |
| Unique molecules | 2 |
| Unique scaffolds | 2 |
| Verified samples | 12 |
| Verified sample rate | 1 |
| License unclear rows | 12 |
| Missing images | 0 |
| Checksum errors | 0 |

- starter dataset only
- not statistically meaningful
- not release-qualified

## Gate Details

### molscribe

```json
{
  "passed": false,
  "thresholds": {
    "valid_smiles_rate_min": 0.95,
    "canonical_exact_match_rate_min": 0.8,
    "false_accept_rate_max": 0.05,
    "high_risk_error_review_needed_rate_min": 1.0,
    "p95_latency_ms_max": 15000.0,
    "positive_sample_count_min": 100,
    "negative_sample_count_min": 20,
    "independent_source_document_count_min": 30,
    "unique_molecule_count_min": 100,
    "unique_scaffold_count_min": 50,
    "verified_sample_rate_min": 1.0,
    "license_unclear_count_max": 0,
    "missing_image_count_max": 0,
    "checksum_error_count_max": 0
  },
  "checks": [
    {
      "metric": "valid_smiles_rate",
      "operator": ">=",
      "threshold": 0.95,
      "value": 0.9,
      "denominator": 10,
      "passed": false
    },
    {
      "metric": "canonical_exact_match_rate",
      "operator": ">=",
      "threshold": 0.8,
      "value": 0.9,
      "denominator": 10,
      "passed": true
    },
    {
      "metric": "false_accept_rate",
      "operator": "<=",
      "threshold": 0.05,
      "value": 0.0,
      "denominator": 2,
      "passed": true
    },
    {
      "metric": "high_risk_error_review_needed_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": 1,
      "passed": true
    },
    {
      "metric": "p95_latency_ms",
      "operator": "<=",
      "threshold": 15000.0,
      "value": 9308.172,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "positive_sample_count",
      "operator": ">=",
      "threshold": 100,
      "value": 10.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "negative_sample_count",
      "operator": ">=",
      "threshold": 20,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "independent_source_document_count",
      "operator": ">=",
      "threshold": 30,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_molecule_count",
      "operator": ">=",
      "threshold": 100,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_scaffold_count",
      "operator": ">=",
      "threshold": 50,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "verified_sample_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "license_unclear_count",
      "operator": "<=",
      "threshold": 0,
      "value": 12.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "missing_image_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "checksum_error_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    }
  ],
  "data_sufficiency": {
    "metrics": {
      "total_samples": 12,
      "positive_sample_count": 10,
      "negative_sample_count": 2,
      "independent_source_document_count": 2,
      "independent_original_image_count": 2,
      "derived_perturbation_count": 10,
      "unique_molecule_count": 2,
      "unique_scaffold_count": 2,
      "verified_sample_count": 12,
      "verified_sample_rate": 1.0,
      "license_unclear_count": 12,
      "missing_image_count": 0,
      "checksum_error_count": 0
    },
    "release_qualified": false,
    "starter_dataset_only": true,
    "not_statistically_meaningful": true,
    "not_release_qualified": true,
    "failed_checks": [
      "positive_sample_count",
      "negative_sample_count",
      "independent_source_document_count",
      "unique_molecule_count",
      "unique_scaffold_count",
      "license_unclear_count"
    ]
  }
}
```

### decimer

```json
{
  "passed": false,
  "thresholds": {
    "valid_smiles_rate_min": 0.95,
    "canonical_exact_match_rate_min": 0.8,
    "false_accept_rate_max": 0.05,
    "high_risk_error_review_needed_rate_min": 1.0,
    "p95_latency_ms_max": 15000.0,
    "positive_sample_count_min": 100,
    "negative_sample_count_min": 20,
    "independent_source_document_count_min": 30,
    "unique_molecule_count_min": 100,
    "unique_scaffold_count_min": 50,
    "verified_sample_rate_min": 1.0,
    "license_unclear_count_max": 0,
    "missing_image_count_max": 0,
    "checksum_error_count_max": 0
  },
  "checks": [
    {
      "metric": "valid_smiles_rate",
      "operator": ">=",
      "threshold": 0.95,
      "value": 1.0,
      "denominator": 10,
      "passed": true
    },
    {
      "metric": "canonical_exact_match_rate",
      "operator": ">=",
      "threshold": 0.8,
      "value": 0.5,
      "denominator": 10,
      "passed": false
    },
    {
      "metric": "false_accept_rate",
      "operator": "<=",
      "threshold": 0.05,
      "value": 0.0,
      "denominator": 2,
      "passed": true
    },
    {
      "metric": "high_risk_error_review_needed_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": 6,
      "passed": true
    },
    {
      "metric": "p95_latency_ms",
      "operator": "<=",
      "threshold": 15000.0,
      "value": 33302.465,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "positive_sample_count",
      "operator": ">=",
      "threshold": 100,
      "value": 10.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "negative_sample_count",
      "operator": ">=",
      "threshold": 20,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "independent_source_document_count",
      "operator": ">=",
      "threshold": 30,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_molecule_count",
      "operator": ">=",
      "threshold": 100,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_scaffold_count",
      "operator": ">=",
      "threshold": 50,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "verified_sample_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "license_unclear_count",
      "operator": "<=",
      "threshold": 0,
      "value": 12.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "missing_image_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "checksum_error_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    }
  ],
  "data_sufficiency": {
    "metrics": {
      "total_samples": 12,
      "positive_sample_count": 10,
      "negative_sample_count": 2,
      "independent_source_document_count": 2,
      "independent_original_image_count": 2,
      "derived_perturbation_count": 10,
      "unique_molecule_count": 2,
      "unique_scaffold_count": 2,
      "verified_sample_count": 12,
      "verified_sample_rate": 1.0,
      "license_unclear_count": 12,
      "missing_image_count": 0,
      "checksum_error_count": 0
    },
    "release_qualified": false,
    "starter_dataset_only": true,
    "not_statistically_meaningful": true,
    "not_release_qualified": true,
    "failed_checks": [
      "positive_sample_count",
      "negative_sample_count",
      "independent_source_document_count",
      "unique_molecule_count",
      "unique_scaffold_count",
      "license_unclear_count"
    ]
  }
}
```

### ensemble

```json
{
  "passed": false,
  "thresholds": {
    "valid_smiles_rate_min": 0.95,
    "canonical_exact_match_rate_min": 0.8,
    "false_accept_rate_max": 0.05,
    "high_risk_error_review_needed_rate_min": 1.0,
    "p95_latency_ms_max": 15000.0,
    "positive_sample_count_min": 100,
    "negative_sample_count_min": 20,
    "independent_source_document_count_min": 30,
    "unique_molecule_count_min": 100,
    "unique_scaffold_count_min": 50,
    "verified_sample_rate_min": 1.0,
    "license_unclear_count_max": 0,
    "missing_image_count_max": 0,
    "checksum_error_count_max": 0
  },
  "checks": [
    {
      "metric": "valid_smiles_rate",
      "operator": ">=",
      "threshold": 0.95,
      "value": 0.5,
      "denominator": 10,
      "passed": false
    },
    {
      "metric": "canonical_exact_match_rate",
      "operator": ">=",
      "threshold": 0.8,
      "value": 0.5,
      "denominator": 10,
      "passed": false
    },
    {
      "metric": "false_accept_rate",
      "operator": "<=",
      "threshold": 0.05,
      "value": 0.0,
      "denominator": 2,
      "passed": true
    },
    {
      "metric": "high_risk_error_review_needed_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": 5,
      "passed": true
    },
    {
      "metric": "p95_latency_ms",
      "operator": "<=",
      "threshold": 15000.0,
      "value": 41120.083,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "positive_sample_count",
      "operator": ">=",
      "threshold": 100,
      "value": 10.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "negative_sample_count",
      "operator": ">=",
      "threshold": 20,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "independent_source_document_count",
      "operator": ">=",
      "threshold": 30,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_molecule_count",
      "operator": ">=",
      "threshold": 100,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "unique_scaffold_count",
      "operator": ">=",
      "threshold": 50,
      "value": 2.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "verified_sample_rate",
      "operator": ">=",
      "threshold": 1.0,
      "value": 1.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "license_unclear_count",
      "operator": "<=",
      "threshold": 0,
      "value": 12.0,
      "denominator": null,
      "passed": false
    },
    {
      "metric": "missing_image_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    },
    {
      "metric": "checksum_error_count",
      "operator": "<=",
      "threshold": 0,
      "value": 0.0,
      "denominator": null,
      "passed": true
    }
  ],
  "data_sufficiency": {
    "metrics": {
      "total_samples": 12,
      "positive_sample_count": 10,
      "negative_sample_count": 2,
      "independent_source_document_count": 2,
      "independent_original_image_count": 2,
      "derived_perturbation_count": 10,
      "unique_molecule_count": 2,
      "unique_scaffold_count": 2,
      "verified_sample_count": 12,
      "verified_sample_rate": 1.0,
      "license_unclear_count": 12,
      "missing_image_count": 0,
      "checksum_error_count": 0
    },
    "release_qualified": false,
    "starter_dataset_only": true,
    "not_statistically_meaningful": true,
    "not_release_qualified": true,
    "failed_checks": [
      "positive_sample_count",
      "negative_sample_count",
      "independent_source_document_count",
      "unique_molecule_count",
      "unique_scaffold_count",
      "license_unclear_count"
    ]
  }
}
```

## Error Rows

- Error row count: 17
- See `errors.csv` for row-level details.

## Release Policy

- Test/acceptance manifests must not be used for training or threshold tuning.
- Starter datasets are smoke benchmarks only; they are not statistically meaningful and are not release-qualified.
- Perturbations of the same source image are not independent samples.
- Do not tune thresholds on this set and then report it as an independent test set.
- Current backend gate failures are expected and should remain visible until a release-qualified dataset exists.
- Metrics are project phase targets, not real-world accuracy claims.
