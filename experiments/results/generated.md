# ORBIT-BENCH — generated worlds

- **run_at**: `2026-10-03T06:13:02+00:00`
- **git_commit**: `c79195a`
- **schema_revision**: `0009`
- **reasoning_provider**: `rule-based-v1`
- **embedding_provider**: `hashing-bow-v1`
- **retrieval_provider**: `in-memory-cosine-v1`
- **python**: `3.9.6`
- **generator**: `v1 (scene, task, conflict)`
- **worlds**: 40 per family × 3 families (scene, task, conflict), generator seed 0

Ground truth comes from a simulated true world; ORBIT only sees observations of it. Values are estimates with 95 % percentile-bootstrap intervals over worlds.

## Results

| Metric | orbit | no_freshness | last_writer_wins | unobserved_removed | no_evidence_gate | event_log_diff | naive_resume | no_risk_grading |
|---|---|---|---|---|---|---|---|---|
| entity_persistence_accuracy | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] |
| false_merge_rate ↓ | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] | 0.00 [0.00, 0.01] |
| diff_precision | 0.88 [0.78, 0.96] | 0.88 [0.78, 0.96] | 0.88 [0.78, 0.96] | 0.24 [0.16, 0.34] | 0.88 [0.78, 0.96] | 0.34 [0.19, 0.70] | 0.88 [0.78, 0.96] | 0.88 [0.78, 0.96] |
| diff_recall | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] | 0.27 [0.18, 0.37] | 0.96 [0.93, 0.99] | 0.24 [0.17, 0.33] | 0.96 [0.93, 0.99] | 0.96 [0.93, 0.99] |
| stale_claim_rate ↓ | 0.13 [0.07, 0.20] | 0.18 [0.12, 0.24] | 0.24 [0.19, 0.30] | 0.11 [0.06, 0.17] | 0.26 [0.21, 0.31] | 0.13 [0.07, 0.20] | 0.13 [0.07, 0.20] | 0.13 [0.07, 0.20] |
| evidence_backed_claim_rate | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.62 [0.54, 0.69] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] |
| correct_abstention_rate | 1.00 [1.00, 1.00] | 0.70 [0.55, 0.85] | 0.35 [0.19, 0.49] | 1.00 [1.00, 1.00] | 0.00 [0.00, 0.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] |
| useful_answer_rate | 0.95 [0.90, 0.99] | 0.98 [0.95, 1.00] | 0.95 [0.90, 0.99] | 0.95 [0.90, 0.99] | 0.98 [0.95, 1.00] | 0.95 [0.90, 0.99] | 0.95 [0.90, 0.99] | 0.95 [0.90, 0.99] |
| conflict_detection_rate | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.00 [0.00, 0.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] |
| unsafe_continuation_rate ↓ | 0.10 [0.02, 0.20] | 0.10 [0.02, 0.20] | 0.10 [0.02, 0.20] | 0.10 [0.02, 0.20] | 0.10 [0.02, 0.20] | 0.10 [0.02, 0.20] | 0.68 [0.52, 0.81] | 0.28 [0.14, 0.41] |
| search_coverage_precision | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.43 [0.21, 0.70] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] |
| progress_rate | 0.65 [0.50, 0.80] | 0.65 [0.50, 0.80] | 0.65 [0.50, 0.80] | 0.65 [0.50, 0.80] | 0.65 [0.50, 0.80] | 0.65 [0.50, 0.80] | 0.33 [0.19, 0.48] | 0.72 [0.59, 0.86] |

## Paired difference vs ORBIT (same worlds)

Δ = ablation − ORBIT with a 95 % paired bootstrap interval; **worse**/**better** when the interval excludes 0.

| Metric | no_freshness | last_writer_wins | unobserved_removed | no_evidence_gate | event_log_diff | naive_resume | no_risk_grading |
|---|---|---|---|---|---|---|---|
| entity_persistence_accuracy | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| false_merge_rate | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| diff_precision | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | -0.63 [-0.74, -0.53] **worse** | +0.00 [+0.00, +0.00] | -0.53 [-0.63, -0.26] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| diff_recall | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | -0.70 [-0.79, -0.59] **worse** | +0.00 [+0.00, +0.00] | -0.72 [-0.80, -0.62] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| stale_claim_rate | +0.04 [+0.02, +0.08] **worse** | +0.11 [+0.06, +0.16] **worse** | -0.02 [-0.06, +0.00] | +0.13 [+0.08, +0.18] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| evidence_backed_claim_rate | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | -0.38 [-0.46, -0.31] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| correct_abstention_rate | -0.30 [-0.45, -0.15] **worse** | -0.65 [-0.81, -0.51] **worse** | +0.00 [+0.00, +0.00] | -1.00 [-1.00, -1.00] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| useful_answer_rate | +0.03 [+0.00, +0.06] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.03 [+0.00, +0.06] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| conflict_detection_rate | +0.00 [+0.00, +0.00] | -1.00 [-1.00, -1.00] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| unsafe_continuation_rate | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.58 [+0.42, +0.72] **worse** | +0.18 [+0.06, +0.30] **worse** |
| search_coverage_precision | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | -0.57 [-0.78, -0.30] **worse** | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] |
| progress_rate | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | +0.00 [+0.00, +0.00] | -0.33 [-0.52, -0.12] **worse** | +0.07 [+0.00, +0.16] |

## ORBIT by family

| Metric | scene | task | conflict |
|---|---|---|---|
| entity_persistence_accuracy | 0.96 | — | — |
| false_merge_rate | 0.00 | — | — |
| diff_precision | 0.88 | — | — |
| diff_recall | 0.96 | — | — |
| stale_claim_rate | 0.21 | — | 0.00 |
| evidence_backed_claim_rate | 1.00 | — | 1.00 |
| correct_abstention_rate | 1.00 | — | 1.00 |
| useful_answer_rate | 0.90 | — | 1.00 |
| conflict_detection_rate | — | — | 1.00 |
| unsafe_continuation_rate | — | 0.10 | — |
| search_coverage_precision | 1.00 | — | — |
| progress_rate | — | 0.65 | — |

## Sample of ORBIT's own errors on generated worlds

- gen-scene-19: diff FP: OBJECT_REMOVED_OR_UNOBSERVED cable_10 location NOT_FOUND_PARTIAL_COVERAGE
- gen-scene-19: diff FP: OBJECT_REMOVED_OR_UNOBSERVED cable_11 location NOT_REOBSERVED
- gen-scene-19: diff FP: OBJECT_REMOVED_OR_UNOBSERVED cable_8 location NOT_FOUND_PARTIAL_COVERAGE
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_08745f7b57 None None
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_593812e889 None None
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_64f3a88389 None None
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_6b8229c196 None None
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_7bac7ab2c7 None None
- gen-scene-19: diff FP: OBJECT_ADDED entity_cable_c841c0b4bc None None
- gen-scene-19: diff FN: OBJECT_MOVED cable_11 location None
- gen-scene-19: query 'Where is cable_10?' @243.0: expected answer, got abstain: cable_10 was last seen at s5 (2026-09-30 09:00 UTC), but that is stale (an indistinguishable cable was seen at s5 (ambig
- gen-scene-27: diff FP: OBJECT_ADDED entity_notebook_0b6c4fa9fe None None
