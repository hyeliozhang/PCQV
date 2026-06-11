# Format Check

Final PDF format audit:

- `pdfinfo paper/main.pdf`: **14 pages**.
- Research/EAB body: **pages 1-12**.
- AI-generated content acknowledgement and references: **pages 13-14**.
- No appendix marker in the body.
- No paper build transients are included under `paper/`.
- `pdffonts paper/main.pdf`: embedded fonts; **no Type 3 fonts**.
- `pdf_preflight paper/main.pdf`: openable, unencrypted, not scanned, no XFA.
- `artifact/format_claim_gate_audit.py`: page 12 filled to the bottom margin in both columns.
- Efficiency/scalability gate: `artifact/efficiency_scalability_audit.py` PASS with 480 scale-sweep rows.
- Final visual render: `render_check_final/contact_final.png`, with rendered page images 1-14 in `render_check_final/`.

Final logs:

- `logs/final_pdfinfo.log`
- `logs/final_pdffonts.log`
- `logs/final_pdflatex3.log`
- `logs/final_render.log`
- `logs/final_pdf_preflight.log`
- `logs/final_verify_final_claims.log`
