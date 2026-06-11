# PCQV Artifact

This artifact reproduces the numerical claims in the PCQV manuscript using CPU-only Python and SQLite.

## Quick verification

```bash
python artifact/verify_final_claims.py
```

The verifier reruns `artifact/code_quality_audit.py`, `artifact/manuscript_consistency_audit.py`, `artifact/reviewer_round_audit.py`, `artifact/clean_submission_audit.py`, and `artifact/submission_integrity_audit.py`, then checks the main benchmark, explicit efficiency/scalability scale sweep, standard-schema stress test, JOB/IMDb-style stress test, finite-model semantics, SQL-feature semantics, randomized semantic fuzzing, optimizer-search audit, proof-carrying rewrite suite, capsule-minimality audit, statistical confidence audit, portable SQL/plan audit, package audit, and code-quality audit.

## Submission integrity gates

```bash
python artifact/tests/test_core_invariants.py
python artifact/proof_carrying_rewrite_suite.py
python artifact/manuscript_consistency_audit.py
python artifact/reviewer_round_audit.py
python artifact/clean_submission_audit.py
python artifact/submission_integrity_audit.py
python artifact/efficiency_scalability_audit.py
python artifact/memo_integration_audit.py
python artifact/review_readiness_audit.py
python artifact/code_quality_audit.py
python artifact/verify_final_claims.py
```

These gates check unit-level compiler/policy/optimizer invariants, certificate acceptance/rejection, clean source packaging, local IEEEtran template files, manuscript-body hygiene, cited-reference count, Python compilability, benchmark claims, semantic audits, randomized fuzzing, optimizer-search evidence, and stale-number prevention.

## Reproduce from scratch

```bash
python artifact/run_all_repro.py
python artifact/run_standard_benchmarks.py --out . --reps 1
python artifact/exhaustive_semantics_checker.py
python artifact/sql_feature_semantics_checker.py
python artifact/randomized_semantic_fuzzer.py --db results/main_corr.sqlite --out . --n 100 --seed 20270605
python artifact/optimizer_search_audit.py --out results --cases 12 --seed 20270605
python artifact/artifact_quality_audit.py
python artifact/proof_carrying_rewrite_suite.py
python artifact/make_figures.py
python artifact/manuscript_consistency_audit.py
python artifact/reviewer_round_audit.py
python artifact/verify_final_claims.py
```

## Main files

- `pcqv/engine.py`: core policy/compiler/optimizer/checker/benchmark code.
- `run_all_repro.py`: main controlled benchmark.
- `run_standard_benchmarks.py`: TPC-H/SSB-style standard-schema stress test.
- `run_joblike_benchmarks.py`: JOB/IMDb-style synthetic stress test.
- `exhaustive_semantics_checker.py`: finite-model rewrite-obligation audit.
- `sql_feature_semantics_checker.py`: SQL-feature semantic audit.
- `randomized_semantic_fuzzer.py`: generated-template metamorphic fuzzer.
- `optimizer_search_audit.py`: visibility-capsule optimizer-search audit.
- `efficiency_scalability_audit.py`: verifies the five-size scale sweep and requires explicit efficiency/scalability manuscript wording.
- `memo_integration_audit.py`: maps emitted safe candidates to optimizer-style memo/property records for integration review.
- `review_readiness_audit.py`: aggregates local evidence into high-confidence reviewer-readiness dimensions.
- `proof_carrying_rewrite_suite.py`: certificate acceptance/rejection suite for emitted candidates.
- `manuscript_consistency_audit.py`: checks that manuscript tables and figure includes match the current result files.
- `reviewer_round_audit.py`: checks RQ coherence, theorem-scope language, unsafe-baseline labels, paired/global speedup distinction, PDF page count, and evidence-ledger paths.
- `static_submission_audit.py`, `artifact_quality_audit.py`, `clean_submission_audit.py`, `submission_integrity_audit.py`, and `code_quality_audit.py`: submission hygiene checks.
- `make_figures.py`: regenerates manuscript figures from CSV results with embedded non-Type-3 fonts.

## Requirements

Python 3.10+ with the standard library is sufficient for core checks. SQLite is accessed through Python's `sqlite3` module. Figure generation uses `matplotlib`.

## Externality benchmark note

The artifact includes a JOB/IMDb-style synthetic benchmark driver:

```bash
python artifact/run_joblike_benchmarks.py
```

It creates an independently specified title/cast/person/company/keyword/movie-info/ACL schema and evaluates six multi-way join families under four policy levels. Outputs are written to `results/joblike_external_raw.csv`, `results/joblike_external_correctness.csv`, and `results/joblike_external_metrics.json`. This benchmark is not an IMDb or JOB compliance claim; it is an externality stress test for the same protected-view reference, safe candidate encodings, and visibility-capsule optimizer contract on a second schema family.


## Containerized verification

The package includes `requirements.txt`, `Dockerfile`, and `REPRODUCIBILITY.md` at the repository root. A reviewer can run `python artifact/verify_final_claims.py` directly on a local Python 3.10+ installation, or build the container and run the same final verifier inside it.
