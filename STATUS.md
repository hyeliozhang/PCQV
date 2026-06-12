# PCQV Artifact Status

Package status: **PASS** under the repository verifier.

## Manuscript/PDF

- Title: **Visibility Capsules for Efficient and Certified Policy-Constrained Query Optimization**
- PDF: `paper/main.pdf`
- Total PDF pages: **14**
- Main-body pages: **1-12**
- Disclosure and references: **13-14**
- Appendix: **none**
- Body section count: **8**
- Body subsection count: **13**
- Page-12 fill audit: `artifact/format_claim_gate_audit.py`
- Figures: vector PDF includes with embedded fonts and PNG previews
- Fonts: embedded; no Type 3 fonts

## References

- Cited references: **81**
- BibTeX entries: **81**
- Missing citation entries: **0**
- Unused BibTeX entries: **0**
- Reference audit: `results/reference_integrity_audit.json`

## Efficiency and Scalability Evidence

- Main timing matrix: **4,320** measurements.
- Explicit scale sweep: **600** measurements across **5** data-size settings.
- Largest scale: PCQV **2.308 ms**, injection **3.829 ms**, oblivious forced order **5.963 ms**, view barrier **26.678 ms**.
- Largest-scale view-barrier/PCQV ratio: **11.560x**.
- Efficiency/scalability gate: `artifact/efficiency_scalability_audit.py`.
- Memo-property bridge: **6,048** candidate records and **15,498** alias-property records, checked by `artifact/memo_integration_audit.py`.

## Artifact Gates

- Top-level verifier: `artifact/verify_final_claims.py`.
- Manuscript/package audit: `artifact/manuscript_package_audit.py`.
- Evidence completeness audit: `artifact/evidence_completeness_audit.py`.
- Claim boundary audit: `artifact/claim_boundary_audit.py`.
- Clean package audit: `artifact/clean_submission_audit.py`.
- Submission integrity audit: `artifact/submission_integrity_audit.py`.

## Reproducibility

- Local command: `python artifact/verify_final_claims.py`.
- Container/environment files: `REPRODUCIBILITY.md`, `requirements.txt`, and `Dockerfile`.
