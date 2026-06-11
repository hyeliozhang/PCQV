# PCQV Artifact

This repository contains the reproducibility artifact for **Visibility Capsules for Efficient and Certified Policy-Constrained Query Optimization**.

PCQV is a policy-constrained relational query optimization artifact. It implements visibility capsules, rewrite certificates, protected-cardinality planning, safe/unsafe controls, semantic checkers, benchmark drivers, and cached result files used by the manuscript.

## Quick Start

From the repository root:

```bash
python artifact/verify_final_claims.py
```

The verifier checks cached local audit results and validates the numerical, semantic, packaging, figure, reference, and manuscript-consistency claims reported in the paper. To regenerate the cached local audits as part of the verifier, use:

```bash
python artifact/verify_final_claims.py --rerun-local
```

## Container

```bash
docker build -t pcqv-artifact .
docker run --rm pcqv-artifact
```

## Main Components

- `artifact/pcqv/engine.py`: core policy, compiler, optimizer, checker, and benchmark implementation.
- `artifact/verify_final_claims.py`: top-level cached verifier for the final artifact.
- `artifact/run_all_repro.py`: main controlled benchmark driver.
- `artifact/run_standard_benchmarks.py`: TPC-H/SSB-style stress benchmark.
- `artifact/run_joblike_benchmarks.py`: JOB/IMDb-style stress benchmark.
- `artifact/make_figures.py`: regenerates the manuscript figure from result files.
- `results/`: cached result tables and audit summaries used by the verifier.
- `data/`: small SQLite databases used by the artifact.
- `paper/`: manuscript source, final PDF, bibliography, and generated figure files needed by manuscript-consistency gates.

## Scope

The repository is CPU-only and uses Python 3.10+ with SQLite from the Python standard library. `matplotlib` is needed only for figure regeneration. No GPU, server DBMS, paid API, external model service, or network service is required for verification.

## Expected Final Checks

The final local package was checked before release with:

- 14-page PDF with a 12-page main body.
- 81 cited references and 81 BibTeX entries.
- No Type 3 fonts, undefined citations, undefined references, or overfull-box failures in the final build.
- Final figure visual audit: PASS.
- Final artifact verifier: PASS on cached gates.
