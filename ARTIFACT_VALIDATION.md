# Artifact Validation

This file summarizes the local checks used to keep the manuscript, figures, result files, and repository contents aligned.

## Validation Gates

- `artifact/verify_final_claims.py`: top-level cached verifier.
- `artifact/manuscript_consistency_audit.py`: table, macro, figure, and result consistency.
- `artifact/manuscript_package_audit.py`: manuscript wording, PDF layout, cited references, and evidence-ledger paths.
- `artifact/format_claim_gate_audit.py`: page budget, figure format, citation/bibliography hygiene, and page-fill checks.
- `artifact/figure_visual_audit.py`: vector figure inclusion, embedded fonts, dimensions, preview resolution, and disclosure placement.
- `artifact/evidence_completeness_audit.py`: coverage of the main evidence categories.
- `artifact/claim_boundary_audit.py`: checks that claims stay within the locally supported result files.
- `artifact/clean_submission_audit.py`: repository hygiene, transient-file checks, and evidence-ledger path validation.
- `artifact/submission_integrity_audit.py`: required files, citation counts, generated numbers, and Python compilability.

## Current Package Facts

- PDF pages: 14 total.
- Main body: pages 1-12.
- Disclosure and references: pages 13-14.
- Appendix: none.
- Body structure: 8 numbered sections and 13 subsections.
- References: 81 cited entries and 81 BibTeX entries.
- Efficiency/scalability: 600 scale-sweep rows across 5 data sizes; largest-size view-barrier/PCQV ratio 11.560x.
- Optimizer-transfer evidence: 6,048 memo candidate records and 15,498 alias-property records.
- Reproducibility: local verifier plus Docker environment files.

## Verification Command

```bash
python artifact/verify_final_claims.py
```
