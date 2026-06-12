# PCQV Artifact Code

This directory contains the executable artifact for the PCQV manuscript. The code is CPU-only Python and uses SQLite through the standard library.

## Quick Verification

```bash
python artifact/verify_final_claims.py
```

The verifier checks cached result files, package gates, manuscript consistency, semantic audits, proof/certificate checks, randomized fuzzing, optimizer-search evidence, scalability evidence, and code-quality checks.

## Integrity Gates

```bash
python artifact/tests/test_core_invariants.py
python artifact/proof_carrying_rewrite_suite.py
python artifact/manuscript_consistency_audit.py
python artifact/manuscript_package_audit.py
python artifact/clean_submission_audit.py
python artifact/submission_integrity_audit.py
python artifact/efficiency_scalability_audit.py
python artifact/memo_integration_audit.py
python artifact/evidence_completeness_audit.py
python artifact/claim_boundary_audit.py
python artifact/code_quality_audit.py
python artifact/verify_final_claims.py
```

These gates check compiler/policy/optimizer invariants, certificate acceptance/rejection, clean source packaging, local IEEEtran template files, manuscript-body hygiene, cited-reference count, Python compilability, benchmark claims, semantic audits, randomized fuzzing, optimizer-search evidence, and stale-number prevention.

## Recompute Main Results

```bash
python artifact/run_all_repro.py
python artifact/run_standard_benchmarks.py --out . --reps 1
python artifact/run_joblike_benchmarks.py
python artifact/exhaustive_semantics_checker.py
python artifact/sql_feature_semantics_checker.py
python artifact/randomized_semantic_fuzzer.py --db results/main_corr.sqlite --out . --n 240 --seed 20270605
python artifact/optimizer_search_audit.py --out results --cases 150 --seed 20270605
python artifact/artifact_quality_audit.py
python artifact/proof_carrying_rewrite_suite.py
python artifact/make_figures.py
python artifact/manuscript_consistency_audit.py
python artifact/manuscript_package_audit.py
python artifact/verify_final_claims.py --rerun-local
```

## Main Files

- `pcqv/engine.py`: core policy/compiler/optimizer/checker/benchmark code.
- `run_all_repro.py`: main controlled benchmark.
- `run_standard_benchmarks.py`: TPC-H/SSB-style standard-schema stress test.
- `run_joblike_benchmarks.py`: JOB/IMDb-style synthetic stress test.
- `exhaustive_semantics_checker.py`: finite-model rewrite-obligation audit.
- `sql_feature_semantics_checker.py`: SQL-feature semantic audit.
- `randomized_semantic_fuzzer.py`: generated-template metamorphic fuzzer.
- `optimizer_search_audit.py`: visibility-capsule optimizer-search audit.
- `efficiency_scalability_audit.py`: verifies the five-size scale sweep and manuscript wording.
- `memo_integration_audit.py`: maps emitted safe candidates to optimizer-style memo/property records.
- `evidence_completeness_audit.py`: checks coverage of the main evidence categories.
- `claim_boundary_audit.py`: checks that manuscript claims stay within local evidence.
- `proof_carrying_rewrite_suite.py`: certificate acceptance/rejection suite for emitted candidates.
- `manuscript_consistency_audit.py`: checks that manuscript tables and figure includes match result files.
- `manuscript_package_audit.py`: checks RQ coherence, theorem-scope language, unsafe-baseline labels, paired/global speedup distinction, PDF page count, and evidence-ledger paths.
- `static_submission_audit.py`, `artifact_quality_audit.py`, `clean_submission_audit.py`, `submission_integrity_audit.py`, and `code_quality_audit.py`: package hygiene checks.
- `make_figures.py`: regenerates manuscript figures from CSV results with embedded non-Type-3 fonts.

## JOB/IMDb-Style Stress Benchmark

```bash
python artifact/run_joblike_benchmarks.py
```

The driver creates a title/cast/person/company/keyword/movie-info/ACL schema and evaluates six multi-way join families under four policy levels. Outputs are written to `results/joblike_external_raw.csv`, `results/joblike_external_correctness.csv`, and `results/joblike_external_metrics.json`. This benchmark is not an IMDb or JOB compliance claim; it is an externality stress test for the same protected-view reference, safe candidate encodings, and visibility-capsule optimizer contract on a second schema family.
