# Format Check

PDF and manuscript-format checks:

- `pdfinfo paper/main.pdf`: **14 pages**.
- Main body: **pages 1-12**.
- Required disclosure and references: **pages 13-14**.
- No appendix marker in the body.
- No paper build transients are included under `paper/`.
- `pdffonts paper/main.pdf`: embedded fonts; **no Type 3 fonts**.
- PDF preflight: openable, unencrypted, not scanned, no XFA.
- `artifact/format_claim_gate_audit.py`: page 12 is filled to the bottom margin in both columns.
- `artifact/efficiency_scalability_audit.py`: PASS with **600** scale-sweep rows.
- `artifact/figure_visual_audit.py`: checks vector figure inclusion, embedded fonts, dimensions, and preview resolution.

The manuscript figure source is `paper/figures/fig_performance_suite.pdf`; the companion preview is `paper/figures/fig_performance_suite.png`.
