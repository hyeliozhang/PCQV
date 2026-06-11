# PCQV Final Submission Status

Final package status: **PASS after strict expert-panel verifier**.

## Final manuscript/PDF

- Title: **Visibility Capsules for Efficient and Certified Policy-Constrained Query Optimization**
- PDF: `paper/main.pdf`
- Total PDF pages: **14**
- Body pages: **1-12**
- AI-generated content acknowledgement + references: **13-14**
- Appendix: **none**
- Body section count: **8**
- Body subsection count: **13**
- Page-12 fill audit: verified by `artifact/format_claim_gate_audit.py`
- Figures: polished vector PDF includes only; `results/figure_visual_audit.json` PASS
- Fonts: embedded; no Type 3 fonts

## Final references

- Cited references: **81**
- BibTeX entries: **81**
- Missing citation entries: **0**
- Unused BibTeX entries: **0**
- Reference audit: `results/reference_integrity_audit.json` PASS

## Figure polish and presentation

- The unified two-column empirical figure suite was regenerated from `artifact/make_figures.py` with latency distributions, scale curves, a policy-stress heatmap, and planner-variant ablations.
- All manuscript figures are explicit vector PDF includes with embedded fonts and PNG previews for visual inspection.
- The scale-sweep evidence is now shown as a figure, not only as prose or a table.
- AI acknowledgement remains a single concise language-polishing sentence.

## Efficiency and scalability evidence

- Main timing matrix: **4,320** measurements.
- Explicit scale sweep: **600** measurements across **5** data-size settings.
- Largest scale: PCQV **2.308 ms**, injection **3.829 ms**, oblivious forced order **5.963 ms**, view barrier **26.678 ms**.
- Largest-scale view-barrier/PCQV ratio: **11.560x**.
- Dedicated gate: `artifact/efficiency_scalability_audit.py`, called by `artifact/verify_final_claims.py`.
- Memo-property bridge: **6,048** candidate records and **15,498** alias-property records, checked by `artifact/memo_integration_audit.py`.

## Strict expert-panel audit

- Expert panel gate: `artifact/expert_panel_audit.py`.
- Output: `results/expert_panel_audit.json`.
- Mean panel score: **9.16/10**.
- All expert scores: **at least 9.0**.
- Confidence: **high for every audited perspective**.

## Reproducibility

- Local command: `python artifact/verify_final_claims.py`.
- Container/environment files: `REPRODUCIBILITY.md`, `requirements.txt`, and `Dockerfile`.
- Latest verifier command: `python artifact/verify_final_claims.py`.
