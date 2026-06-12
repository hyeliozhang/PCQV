# PCQV Artifact

This repository contains the reproducibility artifact for **Visibility Capsules for Efficient and Certified Policy-Constrained Query Optimization**.

PCQV is a CPU-only Python/SQLite artifact for policy-constrained relational query optimization. It implements visibility capsules, rewrite certificates, protected-cardinality planning, safe and diagnostic controls, semantic checkers, benchmark drivers, figure generation, and the cached result files used by the manuscript.

## Quick Start

Run the cached verifier from the repository root:

```bash
python artifact/verify_final_claims.py
```

The verifier checks the manuscript-facing claims against cached numerical results, semantic audits, proof/certificate checks, figure metadata, package hygiene, and manuscript-consistency gates. To rerun the lightweight local gates before verification:

```bash
python artifact/verify_final_claims.py --rerun-local
```

## Container

```bash
docker build -t pcqv-artifact .
docker run --rm pcqv-artifact
```

## Repository Layout

- `artifact/pcqv/engine.py`: core policy, compiler, optimizer, checker, and benchmark implementation.
- `artifact/verify_final_claims.py`: top-level claim verifier.
- `artifact/run_all_repro.py`: main controlled benchmark driver.
- `artifact/run_standard_benchmarks.py`: TPC-H/SSB-style stress benchmark.
- `artifact/run_joblike_benchmarks.py`: JOB/IMDb-style stress benchmark.
- `artifact/make_figures.py`: regenerates the manuscript figure from result files.
- `results/`: cached result tables and audit summaries consumed by the verifier.
- `data/`: small SQLite databases used by the artifact.
- `paper/`: manuscript source, PDF, bibliography, and generated figure files used by consistency gates.

## Scope

The artifact uses Python 3.10+ and SQLite from the Python standard library. `matplotlib` is needed for figure regeneration. No GPU, server DBMS, paid API, external model service, or network service is required for verification.

PDF-layout audits use Poppler command-line tools such as `pdfinfo`, `pdftotext`, and `pdftoppm`. Put them on `PATH`, or set `PCQV_POPPLER_BIN` to the directory containing those binaries before running PDF gates.

## Checked Properties

- 14-page PDF with a 12-page main body.
- 81 cited references and 81 BibTeX entries.
- Embedded fonts, no Type 3 fonts, no undefined citations, and no undefined references.
- Vector manuscript figure with a high-resolution PNG preview for visual inspection.
- Cached verifier PASS for numerical, semantic, proof/certificate, figure, packaging, and manuscript-consistency gates.
