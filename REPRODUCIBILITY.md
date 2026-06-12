# Reproducibility

The artifact is CPU-only and uses Python 3.10+ with SQLite from the Python standard library. Figure regeneration additionally needs `matplotlib`; PDF-layout checks need Poppler tools on `PATH` or in `PCQV_POPPLER_BIN`.

## Fast Verifier

```bash
python artifact/verify_final_claims.py
```

This command validates the cached result files and package checks without rerunning the full benchmark suite.

## Recompute Evidence

```bash
python artifact/run_all_repro.py
python artifact/run_standard_benchmarks.py --out . --reps 1
python artifact/run_joblike_benchmarks.py
python artifact/exhaustive_semantics_checker.py
python artifact/sql_feature_semantics_checker.py
python artifact/randomized_semantic_fuzzer.py --db results/main_corr.sqlite --out . --n 240 --seed 20270605
python artifact/optimizer_search_audit.py --out results --cases 150 --seed 20270605
python artifact/proof_carrying_rewrite_suite.py
python artifact/capsule_minimality_audit.py
python artifact/statistical_confidence_audit.py
python artifact/portable_sql_plan_audit.py
python artifact/memo_integration_audit.py
python artifact/efficiency_scalability_audit.py
python artifact/make_figures.py
python artifact/verify_final_claims.py --rerun-local
```

The repository includes generated `results/` files so claims can be inspected quickly. The commands above rebuild the main result files, regenerate figures, rerun local package checks, and then verify the manuscript-facing claims.

## Container

```bash
docker build -t pcqv-artifact .
docker run --rm pcqv-artifact
```
